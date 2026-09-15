import asyncio
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path

from .models import Candidate, Observation, Panel


class Store:
    """Short SQLite transactions serialized off the Discord event loop."""

    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = asyncio.Lock()

    async def run(self, operation):
        async with self.lock:
            # Finish a transaction even if the caller is cancelled during shutdown.
            task = asyncio.create_task(asyncio.to_thread(operation, self.conn))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise

    async def initialize(self):
        def init(db):
            db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                PRAGMA busy_timeout=5000;
                CREATE TABLE IF NOT EXISTS panels (
                    id TEXT PRIMARY KEY, guild_id TEXT NOT NULL, channel_id TEXT NOT NULL,
                    place_id TEXT NOT NULL, name TEXT NOT NULL, message_id TEXT NOT NULL,
                    max_players INTEGER NOT NULL, interval INTEGER NOT NULL,
                    freshness INTEGER NOT NULL, pages INTEGER NOT NULL,
                    state TEXT NOT NULL, version INTEGER NOT NULL, error TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS panels_place ON panels(place_id, state);
                CREATE TABLE IF NOT EXISTS servers (
                    place_id TEXT NOT NULL, job_id TEXT NOT NULL, playing INTEGER NOT NULL,
                    capacity INTEGER NOT NULL, observed_at REAL NOT NULL, first_seen REAL NOT NULL,
                    low_since REAL NOT NULL, low_samples INTEGER NOT NULL,
                    PRIMARY KEY(place_id, job_id)
                );
                CREATE INDEX IF NOT EXISTS servers_age ON servers(observed_at);
                CREATE TABLE IF NOT EXISTS observations (
                    place_id TEXT NOT NULL, job_id TEXT NOT NULL, observed_at REAL NOT NULL,
                    playing INTEGER NOT NULL, PRIMARY KEY(place_id, job_id, observed_at)
                );
                CREATE INDEX IF NOT EXISTS observations_age ON observations(observed_at);
                PRAGMA user_version=1;
            """)
            db.commit()
        await self.run(init)

    async def panels(self) -> list[Panel]:
        return await self.run(lambda db: [Panel(**dict(r)) for r in db.execute("SELECT * FROM panels")])

    async def get(self, panel_id: str) -> Panel | None:
        def query(db):
            row = db.execute("SELECT * FROM panels WHERE id=?", (panel_id,)).fetchone()
            return Panel(**dict(row)) if row else None
        return await self.run(query)

    async def save(self, panel: Panel):
        panel.validate()
        values = asdict(panel)
        def write(db):
            with db:
                db.execute(f"INSERT OR REPLACE INTO panels ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
        await self.run(write)

    async def delete(self, panel_id: str):
        def delete(db):
            with db:
                db.execute("DELETE FROM panels WHERE id=?", (panel_id,))
        await self.run(delete)

    async def observe(self, place: str, observations: list[Observation]) -> list[Candidate]:
        def write(db):
            result = []
            with db:
                for obs in observations:
                    old = db.execute("SELECT * FROM servers WHERE place_id=? AND job_id=?", (place, obs.job_id)).fetchone()
                    # Re-reading cache or an older upstream page is not new evidence.
                    if old and obs.observed_at <= old["observed_at"]:
                        continue
                    first = old["first_seen"] if old else obs.observed_at
                    continuous = old and 0 < obs.observed_at - old["observed_at"] <= 30 and old["playing"] <= 1 and obs.playing <= 1
                    low_since = old["low_since"] if continuous else obs.observed_at
                    samples = old["low_samples"] + 1 if continuous else int(obs.playing <= 1)
                    db.execute("INSERT OR REPLACE INTO servers VALUES (?,?,?,?,?,?,?,?)",
                               (place, obs.job_id, obs.playing, obs.capacity, obs.observed_at, first, low_since, samples))
                    db.execute("INSERT OR IGNORE INTO observations VALUES (?,?,?,?)",
                               (place, obs.job_id, obs.observed_at, obs.playing))
                    result.append(Candidate(obs.job_id, obs.playing, obs.capacity, obs.observed_at,
                                            first, low_since, samples, old["playing"] if old else None))
            return result
        return await self.run(write)

    async def cleanup(self, now: float | None = None):
        now = time.time() if now is None else now
        def clean(db):
            with db:
                db.execute("DELETE FROM observations WHERE rowid IN (SELECT rowid FROM observations WHERE observed_at < ? LIMIT 5000)", (now - 48 * 3600,))
                db.execute("DELETE FROM servers WHERE rowid IN (SELECT rowid FROM servers WHERE observed_at < ? LIMIT 5000)", (now - 7 * 86400,))
        await self.run(clean)

    async def close(self):
        await self.run(lambda db: db.close())
