import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID


def parse_place(value: str) -> str:
    value = value.strip().strip("<>")
    if re.fullmatch(r"[1-9][0-9]{0,19}", value):
        return value
    url = urlsplit(value)
    if url.scheme != "https" or url.hostname not in {"www.roblox.com", "roblox.com"}:
        raise ValueError("Usa un PlaceId o https://www.roblox.com/games/ID/nombre.")
    if url.username or url.password or url.port not in {None, 443}:
        raise ValueError("Enlace de Roblox no compatible.")
    match = re.match(r"^/(?:[a-z]{2}(?:-[a-z]{2})?/)?games/([1-9][0-9]{0,19})(?:/|$)", url.path)
    if match:
        return match.group(1)
    if url.path == "/games/start":
        ids = parse_qs(url.query).get("placeId", [])
        if len(ids) == 1 and re.fullmatch(r"[1-9][0-9]{0,19}", ids[0]):
            return ids[0]
    raise ValueError("El enlace no contiene un PlaceId. Copia la URL /games/ID de la página del juego.")


@dataclass
class Panel:
    id: str
    guild_id: str
    channel_id: str
    place_id: str
    name: str
    message_id: str = ""
    max_players: int = 1
    interval: int = 10
    freshness: int = 15
    pages: int = 2
    state: str = "active"
    version: int = 1
    error: str = ""
    profile: str = "equilibrado"
    event_since: float = 0

    @property
    def ttl(self) -> int:
        return min(self.freshness, 8 if self.profile == "evento" else 10) if self.profile in {"precision", "evento", "profundo"} else self.freshness

    def validate(self):
        if self.profile not in {"rapido", "equilibrado", "precision", "evento", "profundo"}:
            raise ValueError("Perfil desconocido.")
        for value, low, high, label in (
            (self.max_players, 0, 10, "Máximo de jugadores"),
            (self.interval, 5, 300, "Intervalo"),
            (self.freshness, 5, 60, "Vigencia"),
            (self.pages, 1, 5, "Páginas"),
        ):
            if not low <= value <= high:
                raise ValueError(f"{label}: debe estar entre {low} y {high}.")


@dataclass(frozen=True)
class Observation:
    job_id: str
    playing: int
    capacity: int
    observed_at: float


@dataclass(frozen=True)
class Candidate:
    job_id: str
    playing: int
    capacity: int
    observed_at: float
    first_seen: float
    low_since: float
    low_samples: int
    previous_playing: int | None = None
    history: tuple[tuple[float, int], ...] = ()

    def eligible(self, panel: Panel, now: float) -> bool:
        ttl = panel.ttl
        return (panel.state == "active" and 0 <= now - self.observed_at <= ttl
                and (panel.profile != "evento" or self.observed_at >= panel.event_since)
                and self.playing <= panel.max_players and self.playing < self.capacity)

    @property
    def stable(self) -> bool:
        return self.playing <= 1 and self.low_samples >= 3 and self.observed_at - self.low_since >= 30


def rank(candidates, panel: Panel, now: float) -> list[Candidate]:
    from .analytics import ranked
    return ranked(candidates, panel, now)


def parse_page(payload: dict, observed_at: float) -> tuple[list[Observation], str | None]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("La API no devolvió una lista de servidores compatible.")
    cursor = payload.get("nextPageCursor")
    if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 4096):
        raise ValueError("Cursor incompatible.")
    result = {}
    for item in payload["data"]:
        if not isinstance(item, dict):
            continue
        try:
            job = str(UUID(item["id"]))
            playing, capacity = item["playing"], item["maxPlayers"]
            if type(playing) is not int or type(capacity) is not int:
                continue
            if not 0 <= playing <= capacity or capacity < 1:
                continue
            result[job] = Observation(job, playing, capacity, observed_at)
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    if payload["data"] and not result:
        raise ValueError("La respuesta contiene servidores, pero ninguno tiene datos válidos.")
    return list(result.values()), cursor or None


def join_url(place: str, job: str) -> str:
    return "https://www.roblox.com/games/start?" + urlencode({"placeId": place, "gameInstanceId": str(UUID(job))})
