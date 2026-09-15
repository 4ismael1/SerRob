import asyncio
import logging
import random
import time
from dataclasses import dataclass, field, replace

from .models import Candidate, Panel
from .analytics import evidence, ranked
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
    cycles: int = 0
    reobserved: int = 0
    low_count: int = 0
    pressure: float = 0
    effective_interval: float = 10
    page_hints: dict[str, tuple[str | None, float]] = field(default_factory=dict)
    explore_cursor: str | None = None
    explore_at: float = 0
    incumbents: dict[str, tuple[str, ...]] = field(default_factory=dict)
    watchlist: tuple[str, ...] = ()
    metrics: dict[str, dict] = field(default_factory=dict)
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
        return ranked(state.candidates.values(), panel, time.time() if now is None else now,
                      state.incumbents.get(panel.id, ())) if state else []

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
        state.pages, state.scanned, state.reobserved, state.low_count = 0, 0, 0, 0
        before = dict(state.candidates)
        cursor, cursors, seen = None, set(), set()
        interval = min(p.interval for p in panels)
        threshold = min(p.max_players for p in panels)
        # Focus on a bounded watchlist, keeping its page cursors only as short-lived hints.
        watch = sorted((c for c in before.values() if c.playing <= threshold and state.last_started - c.observed_at <= 180),
                       key=lambda c: evidence(c, panels[0], c.observed_at).score, reverse=True)[:50]
        targets = {c.job_id for c in watch}
        explore_due = state.cycles % 3 == 0
        hint_weights = {}
        for c in watch:
            hint = state.page_hints.get(c.job_id)
            if hint and hint[0] and state.last_started - hint[1] <= 45:
                hint_weights[hint[0]] = hint_weights.get(hint[0], 0) + evidence(c, panels[0], c.observed_at).score
        hints = sorted(hint_weights, key=hint_weights.get, reverse=True)
        head_next = None
        repeated, filling = 0, 0
        state.cycles += 1
        exploration_request = False
        try:
            async with asyncio.timeout(20):
                for _ in range(max(p.pages for p in panels)):
                    cursors.add(cursor)
                    try:
                        observations, next_cursor = await self.roblox.servers(place, cursor)
                    except ProviderError as exc:
                        if cursor and exc.code == "bad_request":
                            # A moved/expired cursor is never a dead-server signal.
                            state.explore_cursor = None
                            state.page_hints = {j:h for j,h in state.page_hints.items() if h[0] != cursor}
                            state.reason = "Cursor caducado; próximo recorrido desde el inicio"
                            break
                        raise
                    state.pages += 1
                    if cursor is None:
                        head_next = next_cursor
                    observations = [o for o in observations if o.job_id not in seen]
                    seen.update(o.job_id for o in observations)
                    state.scanned += len(observations)
                    state.low_count += sum(o.playing <= threshold for o in observations)
                    state.reobserved += sum(o.job_id in before for o in observations)
                    for obs in observations:
                        old = before.get(obs.job_id)
                        if old and 0 < obs.observed_at - old.observed_at <= 45:
                            repeated += 1
                            filling += old.playing <= threshold and obs.playing > threshold
                        state.page_hints[obs.job_id] = (cursor, obs.observed_at)
                    await self.store.resolve_checks(place, observations, time.time())
                    # Save low-population candidates and updates that invalidate existing ones.
                    selected = [o for o in observations if o.playing <= 10 or o.job_id in state.candidates]
                    for candidate in await self.store.observe(place, selected):
                        state.candidates[candidate.job_id] = candidate
                    state.last_success = time.time()
                    state.error = ""
                    if exploration_request:
                        state.explore_cursor, state.explore_at = next_cursor, time.time()
                    enough = all(len(self.results(p)) >= 5 and
                                 (p.profile == "rapido" or sum(evidence(c, p, time.time()).confirmed for c in self.results(p)) >= 3)
                                 for p in panels)
                    missing_targets = targets - seen
                    if enough and (all(p.profile == "rapido" for p in panels) or
                                   ((not explore_due or state.pages >= 2) and (not missing_targets or state.pages >= 2))):
                        state.reason = "TOP reciente con evidencia; presupuesto restante reservado"
                        break
                    # Exploration periodically advances beyond the same first two pages.
                    choices = []
                    if explore_due:
                        if state.explore_cursor and time.time() - state.explore_at <= 45:
                            choices.append((state.explore_cursor, True))
                        choices.append((next_cursor or head_next, True))
                    if missing_targets:
                        choices.extend((h, False) for h in hints)
                    choices.append((next_cursor or head_next, True))
                    choice = next(((value, explore) for value, explore in choices if value and value not in cursors), None)
                    if not choice and not next_cursor:
                        state.reason = "Fin de la lista disponible; cobertura no garantizada"
                        break
                    if not choice:
                        state.reason = "Cursor repetido; recorrido detenido"
                        break
                    cursor, exploration_request = choice
                else:
                    state.reason = "Presupuesto de páginas agotado"
            if repeated >= 5:
                state.pressure = 0.6 * state.pressure + 0.4 * filling / repeated
            if any(p.profile == "evento" for p in panels) or state.pressure >= 0.2:
                interval = max(5, interval / 2)
            state.effective_interval = interval
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
            state.page_hints = {j:h for j,h in state.page_hints.items() if j in state.candidates and now-h[1] <= 45}
            state.watchlist = tuple(c.job_id for c in sorted(
                (c for c in state.candidates.values() if c.playing <= threshold),
                key=lambda c: evidence(c, panels[0], c.observed_at).score, reverse=True)[:50])
            state.generation += 1
            state.changed.set()
        # Persist prospective checks only after ingestion: no look-ahead in scoring.
        try:
            await self.record_quality(place, panels, state)
        except Exception:
            log.exception("No se pudo guardar la medición de calidad de Place %s", place)

    async def record_quality(self, place, panels, state):
        if state.last_success >= state.last_started:
            for panel in panels:
                current = await self.store.get(panel.id)
                if current and current.state == "active":
                    results = self.results(current)
                    state.incumbents[current.id] = tuple(c.job_id for c in results)
                    await self.store.issue_checks(current, results, time.time())
                    state.metrics[current.id] = await self.store.quality(place, current.max_players, current.profile)

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
        if candidate and ranked([candidate], current, time.time()) and time.time() - candidate.observed_at <= 5:
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
