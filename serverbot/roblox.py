import asyncio
import json
import math
import random
import time
from datetime import timezone
from email.utils import parsedate_to_datetime

import aiohttp

from .models import parse_page


class ProviderError(Exception):
    def __init__(self, message: str, retry_seconds: float = 30, code: str = "network"):
        super().__init__(message)
        self.retry_seconds = retry_seconds
        self.code = code


def retry_after(value: str | None, fallback: float, now: float | None = None) -> float:
    if value:
        try:
            seconds = float(value)
            if math.isfinite(seconds):
                return max(0.0, seconds)
        except ValueError:
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                return max(0.0, date.timestamp() - (time.time() if now is None else now))
            except (ValueError, TypeError, OverflowError):
                pass
    return fallback


class RateGate:
    """Spaced requests, no bursts; a 429 suspends all Roblox work."""

    def __init__(self, rpm: int, clock=time.monotonic, sleep=asyncio.sleep):
        self.spacing = 60 / rpm
        self.base_spacing = self.spacing
        self.last_limit = -float("inf")
        self.successes = 0
        self.clock, self.sleep = clock, sleep
        self.next_at = 0.0
        self.blocked_until = 0.0
        self.lock = asyncio.Lock()

    def penalize(self, seconds: float):
        self.blocked_until = max(self.blocked_until, self.clock() + seconds)

    def reduce_rate(self):
        self.spacing = min(60, self.spacing * 2)
        self.last_limit = self.clock()
        self.successes = 0

    def success(self):
        self.successes += 1
        if self.successes >= 20 and self.clock() - self.last_limit >= 120:
            self.spacing = max(self.base_spacing, self.spacing * 0.9)
            self.successes = 0

    async def acquire(self):
        async with self.lock:
            while True:
                delay = max(self.next_at, self.blocked_until) - self.clock()
                if delay <= 0:
                    self.next_at = self.clock() + self.spacing
                    return
                await self.sleep(delay)


class Roblox:
    def __init__(self, rpm: int):
        self.gate = RateGate(rpm)
        self.session: aiohttp.ClientSession | None = None
        self.slots = asyncio.Semaphore(2)
        self.failures = 0
        self.requests = 0
        self.rate_limits = 0

    async def start(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            cookie_jar=aiohttp.DummyCookieJar(),
            headers={"User-Agent": "DiscordPublicServerPanels/1.0", "Accept": "application/json"},
        )

    async def close(self):
        if self.session:
            await self.session.close()

    async def get(self, url: str, params: dict | None = None) -> tuple[dict, float]:
        if self.session is None:
            raise RuntimeError("Cliente HTTP no iniciado.")
        async with self.slots:
            await self.gate.acquire()
            started = time.time()
            self.requests += 1
            try:
                async with self.session.get(url, params=params, allow_redirects=False) as response:
                    if response.status == 429:
                        self.failures += 1
                        self.rate_limits += 1
                        self.gate.reduce_rate()
                        fallback = min(300, 5 * 2 ** min(self.failures, 6))
                        delay = retry_after(response.headers.get("Retry-After"), fallback) + random.uniform(0.1, 1)
                        self.gate.penalize(delay)
                        raise ProviderError("Roblox limitó las consultas (429). Esperando sin reintentar en ráfaga.", delay, "rate_limit")
                    if response.status in {401, 403}:
                        raise ProviderError("Roblox restringe este acceso (401/403). El bot no utiliza cookies.", 300, "restricted")
                    if response.status == 400:
                        raise ProviderError("Roblox rechazó los parámetros o el cursor (400).", 30, "bad_request")
                    if response.status == 404:
                        raise ProviderError("Roblox no encontró el recurso (404).", 300, "not_found")
                    if response.status >= 500:
                        self.failures += 1
                        delay = min(300, 5 * 2 ** min(self.failures, 6))
                        if self.failures >= 3:
                            self.gate.penalize(delay)
                        raise ProviderError("Roblox tiene un error temporal de servicio.", delay, "upstream")
                    if response.status != 200:
                        raise ProviderError(f"Respuesta HTTP {response.status}; escaneo aplazado.", 120, "http")
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        body.extend(chunk)
                        if len(body) > 2 * 1024 * 1024:
                            raise ProviderError("Respuesta demasiado grande.", 120, "schema")
                    try:
                        data = json.loads(body)
                    except (ValueError, UnicodeError) as exc:
                        raise ProviderError("Respuesta no JSON de Roblox.", 120, "schema") from exc
                    if not isinstance(data, dict):
                        raise ProviderError("Formato de respuesta no compatible.", 120, "schema")
                    self.failures = 0
                    self.gate.success()
                    try:
                        if int(response.headers.get("x-ratelimit-remaining", "-1")) == 0:
                            self.gate.penalize(retry_after(response.headers.get("x-ratelimit-reset"), 30))
                    except ValueError:
                        pass
                    # Age denotes upstream cache age; don't label an old cache as fresh.
                    try:
                        age = float(response.headers.get("Age", "0"))
                        if not math.isfinite(age) or age < 0:
                            raise ValueError
                    except ValueError:
                        age = 3600.0  # Unknown cache age is conservatively stale.
                    return data, started - age
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                self.failures += 1
                delay = min(300, 5 * 2 ** min(self.failures, 6))
                if self.failures >= 3:
                    self.gate.penalize(delay)
                raise ProviderError("Error de conexión/TLS con Roblox. Comprueba la red y los certificados del host.", delay) from exc

    async def servers(self, place: str, cursor: str | None = None):
        params = {"sortOrder": "Asc", "excludeFullGames": "true", "limit": "100"}
        if cursor:
            params["cursor"] = cursor
        data, observed_at = await self.get(f"https://games.roblox.com/v1/games/{place}/servers/Public", params)
        try:
            return parse_page(data, observed_at)
        except ValueError as exc:
            raise ProviderError(str(exc), 120, "schema") from exc

    async def title(self, place: str) -> str:
        data, _ = await self.get(f"https://apis.roblox.com/universes/v1/places/{place}/universe")
        universe = data.get("universeId")
        if type(universe) is not int or universe <= 0:
            raise ProviderError("No se pudo resolver el Universe de este Place.", 300, "schema")
        data, _ = await self.get("https://games.roblox.com/v1/games", {"universeIds": str(universe)})
        games = data.get("data")
        if not isinstance(games, list) or not games or not isinstance(games[0], dict):
            raise ProviderError("No se pudo resolver el nombre del juego.", 300, "schema")
        return str(games[0].get("name") or f"Place {place}")[:100]
