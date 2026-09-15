import os
from dataclasses import dataclass


def integer(name: str, default: int, minimum: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} debe estar entre {minimum} y {maximum}.")
    return value


@dataclass(frozen=True)
class Settings:
    token: str = ""
    guild_id: int | None = None
    database: str = "data/bot.sqlite3"
    rpm: int = 30
    max_places: int = 5
    max_panels: int = 10
    edit_seconds: int = 5
    join_mode: str = "legacy"

    @classmethod
    def from_env(cls):
        mode = os.getenv("JOIN_MODE", "legacy")
        if mode not in {"legacy", "game"}:
            raise ValueError("JOIN_MODE debe ser legacy o game.")
        return cls(
            token=os.getenv("DISCORD_TOKEN", "").strip(),
            guild_id=int(os.environ["DISCORD_GUILD_ID"]) if os.getenv("DISCORD_GUILD_ID") else None,
            database=os.getenv("DATABASE_PATH", "data/bot.sqlite3"),
            rpm=integer("ROBLOX_REQUESTS_PER_MINUTE", 30, 1, 120),
            max_places=integer("MAX_ACTIVE_PLACES", 5, 1, 50),
            max_panels=integer("MAX_PANELS_PER_GUILD", 10, 1, 50),
            edit_seconds=integer("PANEL_EDIT_SECONDS", 5, 3, 60),
            join_mode=mode,
        )
