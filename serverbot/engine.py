import asyncio
import logging
import random
import time
from dataclasses import dataclass, field, replace

from .models import Candidate, Panel, rank
from .roblox import ProviderError

log = logging.getLogger(__name__)


@dataclass
class PlaceState:
    candidates: dict[str, Candidate] = field(default_factory=dict)
    last_started: float = 0
    next_at: float = 0
    blocked_until: float = 0
    last_success: float = 0
    error: str = ""
    pages: int = 0
    scanned: int = 0
    reason: str = "Pendiente del primer escaneo"
    generation: int = 0
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class Engine:
    def __init__(self, store, roblox, max_places: int = 5):
        self.store, self.roblox, self.max_places = store, roblox, max_places
        self.states: dict[str, PlaceState] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.runner: asyncio.Task | None = None

    def start(self):
        self.runner = asyncio.create_task(self.run(), name="scanner-scheduler")

    async def close(self):
        tasks = list(self.tasks.values()) + ([self.runner] if self.runner else [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def results(self, panel: Panel, now: float | None = None) -> list[Candidate]:
        state = self.states.get(panel.place_id)
        return rank(state.candidates.values(), panel, time.time() if now is None else now) if state else []

    async def run(self):
        last_cleanup = 0.0
        while True:
            try:
                panels = await self.store.panels()
                groups = {}
                for panel in panels:
                    if panel.state == "active":
                        groups.setdefault(panel.place_id, []).append(panel)
                # Admission is also checked in panel creation and resume commands.
                for place in list(self.states):
                    if place not in groups:
                        task = self.tasks.pop(place, None)
                        if task:
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                        self.states.pop(place, None)
                for place, subscriptions in list(groups.items())[:self.max_places]:
                    state = self.states.setdefault(place, PlaceState())
                    task = self.tasks.get(place)
                    if (not task or task.done()) and time.time() >= max(state.next_at, state.blocked_until):
                        self.tasks[place] = asyncio.create_task(self.scan(place, subscriptions), name=f"scan-{place}")
                if time.time() - last_cleanup >= 60:
                    await self.store.cleanup()
                    last_cleanup = time.time()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Error del planificador; se recuperará en el próximo ciclo")
            await asyncio.sleep(0.5)

    async def scan(self, place: str, panels: list[Panel]):
        state = self.states.setdefault(place, PlaceState())
        state.last_started = time.time()
        state.pages, state.scanned = 0, 0
        cursor, cursors, seen = None, set(), set()
        interval = min(p.interval for p in panels)
        try:
            async with asyncio.timeout(20):
                for _ in range(max(p.pages for p in panels)):
                    observations, next_cursor = await self.roblox.servers(place, cursor)
                    state.pages += 1
                    observations = [o for o in observations if o.job_id not in seen]
                    seen.update(o.job_id for o in observations)
                    state.scanned += len(observations)
                    # Save low-population candidates and updates that invalidate existing ones.
                    selected = [o for o in observations if o.playing <= 10 or o.job_id in state.candidates]
                    for candidate in await self.store.observe(place, selected):
                        state.candidates[candidate.job_id] = candidate
                    state.last_success = time.time()
                    state.error = ""
                    if all(len(self.results(panel)) >= 5 for panel in panels):
                        state.reason = "Suficientes candidatos recientes"
                        break
                    if not next_cursor:
                        state.reason = "Fin de la lista disponible; cobertura no garantizada"
                        break
                    if next_cursor in cursors:
                        state.reason = "Cursor repetido; recorrido detenido"
                        break
                    cursors.add(next_cursor)
                    cursor = next_cursor
                else:
                    state.reason = "Presupuesto de páginas agotado"
            state.next_at = max(time.time() + 1, state.last_started + interval + random.uniform(0, 0.5))
        except ProviderError as exc:
            state.error = str(exc)
            state.blocked_until = time.time() + exc.retry_seconds
            state.next_at = state.blocked_until
            log.warning("Place %s: %s", place, exc)
        except TimeoutError:
            state.error = "Presupuesto de tiempo agotado; los resultados caducan por separado."
            state.next_at = time.time() + interval
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Error de escaneo en Place %s", place)
            state.error = "Error interno de escaneo; recuperación automática."
            state.next_at = time.time() + 30
        finally:
            now = time.time()
            state.candidates = dict(sorted(
                ((k, c) for k, c in state.candidates.items() if now - c.observed_at <= 300),
                key=lambda pair: pair[1].observed_at, reverse=True)[:500])
            state.generation += 1
            state.changed.set()

    async def refresh(self, panel: Panel, wait: float = 0):
        if panel.state != "active":
            return
        state = self.states.setdefault(panel.place_id, PlaceState())
        generation = state.generation
        task = self.tasks.get(panel.place_id)
        if not task or task.done():
            # Clicks never bypass error cooldown or the minimum scan interval.
            state.next_at = max(state.blocked_until, state.last_started + 5)
        if wait:
            try:
                async with asyncio.timeout(wait):
                    while state.generation == generation:
                        state.changed.clear()
                        await state.changed.wait()
            except TimeoutError:
                pass

    async def prepare(self, panel: Panel, job_id: str) -> Candidate | None:
        state = self.states.get(panel.place_id)
        candidate = state.candidates.get(job_id) if state else None
        if not candidate or time.time() - candidate.observed_at > min(5, panel.freshness):
            await self.refresh(panel, wait=12)
        # Configuration and pause status may have changed while waiting.
        current = await self.store.get(panel.id)
        if not current or current.state != "active" or current.place_id != panel.place_id:
            return None
        state = self.states.get(current.place_id)
        candidate = state.candidates.get(job_id) if state else None
        if candidate and candidate.eligible(current, time.time()) and time.time() - candidate.observed_at <= 5:
            return candidate
        return None

    async def prepare_best(self, panel: Panel) -> Candidate | None:
        """Explicitly choose a fresh candidate; never silently replace a selected JobId."""
        fresh = self.results(replace(panel, freshness=min(5, panel.freshness)))
        if not fresh:
            await self.refresh(panel, wait=12)
        current = await self.store.get(panel.id)
        if not current or current.state != "active" or current.place_id != panel.place_id:
            return None
        return next(iter(self.results(replace(current, freshness=min(5, current.freshness)))), None)
