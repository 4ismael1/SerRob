import asyncio
import json
import sqlite3
import time
from uuid import uuid4
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
            if db.execute("PRAGMA user_version").fetchone()[0] > 2:
                raise RuntimeError("La base de datos pertenece a una versión más nueva del bot.")
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
                CREATE TABLE IF NOT EXISTS quality_checks (
                    id TEXT PRIMARY KEY, place_id TEXT NOT NULL, job_id TEXT NOT NULL,
                    threshold INTEGER NOT NULL, issued_at REAL NOT NULL, due_at REAL NOT NULL,
                    expires_at REAL NOT NULL, bucket INTEGER NOT NULL, profile TEXT NOT NULL,
                    band TEXT NOT NULL, score REAL NOT NULL, outcome TEXT, result_playing INTEGER,
                    UNIQUE(place_id, job_id, threshold, bucket, profile)
                );
                CREATE INDEX IF NOT EXISTS quality_pending ON quality_checks(place_id, job_id, outcome);
                CREATE INDEX IF NOT EXISTS quality_age ON quality_checks(issued_at);
                CREATE TABLE IF NOT EXISTS join_reports (
                    id TEXT PRIMARY KEY, panel_id TEXT NOT NULL, place_id TEXT NOT NULL,
                    job_id TEXT NOT NULL, user_id TEXT NOT NULL, guild_id TEXT NOT NULL,
                    issued_at REAL NOT NULL, outcome TEXT
                );
                CREATE INDEX IF NOT EXISTS reports_age ON join_reports(issued_at);
            """)
            # Additive migration from v1: existing panels and history remain intact.
            columns = {r[1] for r in db.execute("PRAGMA table_info(panels)")}
            if "profile" not in columns:
                db.execute("ALTER TABLE panels ADD COLUMN profile TEXT NOT NULL DEFAULT 'equilibrado'")
            if "event_since" not in columns:
                db.execute("ALTER TABLE panels ADD COLUMN event_since REAL NOT NULL DEFAULT 0")
            if "history_json" not in {r[1] for r in db.execute("PRAGMA table_info(servers)")}:
                db.execute("ALTER TABLE servers ADD COLUMN history_json TEXT NOT NULL DEFAULT '[]'")
            db.execute("PRAGMA user_version=2")
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
                    history = json.loads(old["history_json"]) if old else []
                    if old and not history:
                        history.append([old["observed_at"], old["playing"]])
                    history = [(t, p) for t, p in history if t >= obs.observed_at - 1800]
                    history.append((obs.observed_at, obs.playing))
                    history = history[-120:]
                    db.execute("INSERT OR REPLACE INTO servers VALUES (?,?,?,?,?,?,?,?,?)",
                               (place, obs.job_id, obs.playing, obs.capacity, obs.observed_at, first, low_since, samples, json.dumps(history)))
                    last_detail = db.execute("SELECT observed_at FROM observations WHERE place_id=? AND job_id=? ORDER BY observed_at DESC LIMIT 1", (place, obs.job_id)).fetchone()
                    if not old or old["playing"] != obs.playing or not last_detail or obs.observed_at - last_detail[0] >= 60:
                        db.execute("INSERT OR IGNORE INTO observations VALUES (?,?,?,?)",
                                   (place, obs.job_id, obs.observed_at, obs.playing))
                    result.append(Candidate(obs.job_id, obs.playing, obs.capacity, obs.observed_at,
                                            first, low_since, samples, old["playing"] if old else None, tuple(history)))
            return result
        return await self.run(write)

    async def issue_checks(self, panel: Panel, candidates: list[Candidate], now: float):
        from .analytics import evidence
        rows = [(uuid4().hex, panel.place_id, c.job_id, panel.max_players, now, now + 10, now + 30,
                 int(now // 30), panel.profile, evidence(c, panel, now).band, evidence(c, panel, now).score)
                for c in candidates if c.eligible(panel, now)]
        def write(db):
            with db:
                db.executemany("INSERT OR IGNORE INTO quality_checks (id,place_id,job_id,threshold,issued_at,due_at,expires_at,bucket,profile,band,score) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        await self.run(write)

    async def resolve_checks(self, place: str, observations: list[Observation], now: float):
        def write(db):
            with db:
                for obs in observations:
                    db.execute("""UPDATE quality_checks SET outcome=CASE WHEN ? <= threshold THEN 'success' ELSE 'failed' END,
                        result_playing=? WHERE place_id=? AND job_id=? AND outcome IS NULL
                        AND due_at <= ? AND expires_at >= ?""",
                               (obs.playing, obs.playing, place, obs.job_id, obs.observed_at, obs.observed_at))
                db.execute("UPDATE quality_checks SET outcome='unknown' WHERE place_id=? AND outcome IS NULL AND expires_at < ?", (place, now))
        await self.run(write)

    async def quality(self, place: str, threshold: int = 1, profile: str = "equilibrado") -> dict:
        from .analytics import outcome_summary
        def query(db):
            counts = {r[0] or "pending": r[1] for r in db.execute(
                "SELECT outcome,COUNT(*) FROM quality_checks WHERE place_id=? AND threshold=? AND profile=? AND issued_at>=? GROUP BY outcome",
                (place, threshold, profile, time.time() - 86400))}
            return {**outcome_summary(counts), "pending": counts.get("pending", 0)}
        return await self.run(query)

    async def receipt(self, panel: Panel, candidate: Candidate, user_id: int) -> str:
        identifier = uuid4().hex
        def write(db):
            with db:
                db.execute("INSERT INTO join_reports VALUES (?,?,?,?,?,?,?,NULL)",
                           (identifier, panel.id, panel.place_id, candidate.job_id, str(user_id), panel.guild_id, time.time()))
        await self.run(write)
        return identifier

    async def report(self, receipt: str, user_id: int, guild_id: int, outcome: str) -> bool:
        if outcome not in {"low", "busy", "unavailable"}:
            return False
        def write(db):
            with db:
                result = db.execute("UPDATE join_reports SET outcome=? WHERE id=? AND user_id=? AND guild_id=? AND outcome IS NULL AND issued_at>=?",
                                    (outcome, receipt, str(user_id), str(guild_id), time.time() - 300))
                return result.rowcount == 1
        return await self.run(write)

    async def reports(self, panel_id: str) -> dict[str, int]:
        return await self.run(lambda db: {r[0]:r[1] for r in db.execute(
            "SELECT outcome,COUNT(*) FROM join_reports WHERE panel_id=? AND issued_at>=? AND outcome IS NOT NULL GROUP BY outcome",
            (panel_id, time.time() - 86400))})

    async def cleanup(self, now: float | None = None):
        now = time.time() if now is None else now
        def clean(db):
            with db:
                db.execute("DELETE FROM observations WHERE rowid IN (SELECT rowid FROM observations WHERE observed_at < ? LIMIT 5000)", (now - 48 * 3600,))
                db.execute("DELETE FROM servers WHERE rowid IN (SELECT rowid FROM servers WHERE observed_at < ? LIMIT 5000)", (now - 7 * 86400,))
                db.execute("UPDATE quality_checks SET outcome='unknown' WHERE outcome IS NULL AND expires_at < ?", (now,))
                db.execute("DELETE FROM quality_checks WHERE issued_at < ?", (now - 7 * 86400,))
                db.execute("DELETE FROM join_reports WHERE issued_at < ?", (now - 7 * 86400,))
        await self.run(clean)

    async def close(self):
        await self.run(lambda db: db.close())
