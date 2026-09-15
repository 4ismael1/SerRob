import asyncio
import sqlite3
import time
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

import pytest

from serverbot.analytics import evidence, ranked, outcome_summary
from serverbot.models import Candidate, Observation, Panel
from serverbot.engine import Engine, PlaceState
from serverbot.storage import Store
from serverbot.roblox import RateGate, ProviderError
from serverbot.discord_app import PanelView, panel_embed


def candidate(number=1, points=((70, 1), (80, 1), (90, 1), (100, 1))):
    return Candidate(str(UUID(int=number)), points[-1][1], 20, points[-1][0],
                     points[0][0], points[0][0], len(points), history=tuple(points))


def panel(**kwargs):
    return replace(Panel("test", "10", "20", "30", "Test"), **kwargs)


def test_history_beats_unconfirmed_zero_but_rapid_prefers_zero():
    stable, zero = candidate(), candidate(2, ((100, 0),))
    assert evidence(stable, panel(), 100).band == "estable"
    assert ranked([zero, stable], panel(), 100)[0] == stable
    assert ranked([zero, stable], panel(profile="rapido"), 100)[0] == zero


@pytest.mark.parametrize("points", [((1, 1), (100, 1)), ((99, 1), (100, 1)), ((90, 0), (100, 1))])
def test_gaps_duplicates_and_growth_do_not_confirm(points):
    c = candidate(points=points)
    assert not evidence(c, panel(), 100).confirmed
    assert ranked([c], panel(profile="precision"), 100) == []


def test_recent_fill_penalty_and_event_reset():
    steady = candidate()
    filled = candidate(2, ((70, 1), (80, 8), (90, 1), (100, 1)))
    assert evidence(filled, panel(), 100).rising
    assert evidence(filled, panel(), 100).score < evidence(steady, panel(), 100).score
    assert ranked([steady], panel(profile="evento", event_since=95), 100) == []
    assert ranked([steady], panel(profile="precision"), 100) == [steady]


def test_incumbent_bonus_never_extends_freshness():
    c = candidate()
    assert ranked([c], panel(), 116, (c.job_id,)) == []
    assert ranked([c], panel(profile="evento"), 109, (c.job_id,)) == []
    assert ranked([c], panel(state="paused"), 100, (c.job_id,)) == []


def test_unknowns_cannot_be_reported_as_seventy_percent():
    summary = outcome_summary({"success": 7, "failed": 3, "unknown": 90})
    assert summary["known_rate"] == .7
    assert summary["lower"] == .07 and summary["coverage"] == .1
    assert not summary["goal_supported"]
    assert outcome_summary({})["known_rate"] is None


def test_quality_is_prospective_and_reports_are_bound_to_user(tmp_path):
    async def run():
        store = Store(str(tmp_path / "quality.db"))
        await store.initialize()
        now = time.time()
        p = panel()
        cs = [candidate(i, ((now, 1),)) for i in range(1, 4)]
        await store.issue_checks(p, cs, now)
        await store.issue_checks(p, cs, now)
        await store.resolve_checks("30", [Observation(c.job_id, 9, 20, now + 5) for c in cs], now + 5)
        assert (await store.quality("30"))["pending"] == 3
        await store.resolve_checks("30", [Observation(cs[0].job_id, 1, 20, now + 12),
                                          Observation(cs[1].job_id, 4, 20, now + 12)], now + 12)
        await store.resolve_checks("30", [], now + 31)
        q = await store.quality("30")
        assert (q["success"], q["failed"], q["unknown"], q["pending"]) == (1, 1, 1, 0)
        receipt = await store.receipt(p, cs[0], 99)
        assert not await store.report(receipt, 98, 10, "low")
        assert not await store.report(receipt, 99, 11, "low")
        assert await store.report(receipt, 99, 10, "busy")
        assert not await store.report(receipt, 99, 10, "low")
        assert await store.reports(p.id) == {"busy": 1}
        await store.close()
    asyncio.run(run())


def test_additive_migration_preserves_v1_panel(tmp_path):
    async def run():
        path = str(tmp_path / "old.db")
        store = Store(path)
        await store.initialize()
        await store.save(panel(interval=20))
        await store.close()
        with sqlite3.connect(path) as db:
            db.execute("ALTER TABLE panels DROP COLUMN profile")
            db.execute("ALTER TABLE panels DROP COLUMN event_since")
            db.execute("ALTER TABLE servers DROP COLUMN history_json")
            db.execute("PRAGMA user_version=1")
        store = Store(path)
        await store.initialize()
        p = await store.get("test")
        assert p.interval == 20 and p.profile == "equilibrado"
        await store.run(lambda db: db.execute("PRAGMA user_version=3"))
        with pytest.raises(RuntimeError):
            await store.initialize()
        await store.close()
    asyncio.run(run())


def test_rate_recovers_slowly_without_exceeding_configured_budget():
    clock = [0]
    gate = RateGate(30, clock=lambda: clock[0])
    gate.reduce_rate()
    assert gate.spacing == 4
    for _ in range(100):
        gate.success()
    assert gate.spacing == 4
    clock[0] = 121
    for _ in range(500):
        gate.success()
    assert gate.spacing == gate.base_spacing == 2


def test_planner_explores_and_revisits_cursor_hints(tmp_path):
    async def run():
        store = Store(str(tmp_path / "planner.db"))
        await store.initialize()
        p = panel()
        now = time.time()
        class API:
            calls = []
            async def servers(self, place, cursor):
                self.calls.append(cursor)
                if cursor == "hint":
                    raise ProviderError("expired", 1, "bad_request")
                return [Observation(str(UUID(int=i)), 1, 20, now) for i in range(1, 6)], "next"
        api = API()
        engine = Engine(store, api)
        await engine.scan("30", [p])
        assert api.calls == [None, "next"]  # Five provisional candidates do not end exploration.
        c = candidate(20, ((now, 1),))
        state = engine.states["30"]
        state.candidates[c.job_id] = c
        state.page_hints[c.job_id] = ("hint", now)
        await engine.scan("30", [p])
        assert api.calls[-2:] == [None, "hint"]
        assert c.job_id in state.candidates
        assert c.job_id not in state.page_hints
        assert "caducado" in state.reason
        await store.save(panel(profile="precision"))
        assert await engine.prepare(p, c.job_id) is None
        await store.close()
    asyncio.run(run())


def test_links_and_picker_use_same_snapshot_within_discord_limits():
    async def run():
        now = time.time()
        p = panel()
        cs = [candidate(i, ((now, 1),)) for i in range(1, 6)]
        bot = SimpleNamespace(settings=SimpleNamespace(join_mode="legacy"))
        view = PanelView(bot, p, cs)
        links = [c for c in view.children if getattr(c, "url", None)]
        assert len(links) == 5 and len(view.children) <= 25
        assert [c.url.split("gameInstanceId=")[1] for c in links] == [c.job_id for c in cs]
        assert view.is_persistent()
        assert all(sum(1 for c in view.children if c.row == row) <= 5 for row in (1, 2))
        engine = Engine(None, None)
        engine.states["30"] = PlaceState(candidates={c.job_id: c for c in cs})
        embed = panel_embed(p, engine, results=cs)
        assert all(c.job_id in embed.fields[i].value for i, c in enumerate(cs))
        bot.settings.join_mode = "game"
        assert not any(getattr(c, "url", None) for c in PanelView(bot, p, cs).children)
        assert not any(getattr(c, "url", None) for c in PanelView(bot, replace(p, state="paused"), []).children)
    asyncio.run(run())


def test_causal_replay_counts_missing_and_filled_servers():
    from tools.audit import replay
    rows = []
    for when, counts in ((100, {1: 1, 2: 1, 3: 1}), (112, {1: 1, 2: 8}), (145, {})):
        rows.append({"at": when, "observations": [
            {"job_id": str(UUID(int=j)), "playing": p, "capacity": 20, "observed_at": when}
            for j, p in counts.items()]})
    result = asyncio.run(replay(rows))
    assert result["rapido"]["success"] == 1
    assert result["rapido"]["failed"] == 1
    assert result["rapido"]["unknown"] >= 1
    assert result["precision"]["empty_snapshots"] >= 1
    assert not any(r["goal_supported"] for r in result.values())


def test_configured_interval_survives_event_pressure_and_refresh(tmp_path):
    async def run():
        store = Store(str(tmp_path / "cadence.db"))
        await store.initialize()
        p = panel(profile="evento", interval=60)
        class API:
            async def servers(self, place, cursor):
                return [], None
        engine = Engine(store, API())
        engine.states["30"] = PlaceState(pressure=1)
        await engine.scan("30", [p])
        state = engine.states["30"]
        assert state.effective_interval == 60
        assert state.next_at >= state.last_started + 60
        await engine.refresh(p)
        assert state.next_at >= state.last_started + 60
        await store.close()
    asyncio.run(run())


def test_latest_snapshot_and_provider_block_control_entry():
    engine = Engine(None, None)
    now = time.time()
    c = candidate(points=((now, 1),))
    state = engine.states.setdefault("30", PlaceState(candidates={c.job_id: c}))
    assert engine.entry_candidate(panel(), c.job_id) == c
    state.candidates[c.job_id] = replace(c, playing=9)
    assert engine.entry_candidate(panel(), c.job_id) is None
    state.candidates[c.job_id] = c
    state.blocked_until = now + 30
    assert engine.entry_candidate(panel(), c.job_id) is None


def test_delivery_rejects_server_that_fills_during_receipt(tmp_path):
    from serverbot.discord_app import ServerBot
    from serverbot.config import Settings
    async def run():
        bot = ServerBot(Settings(database=str(tmp_path / "delivery.db")))
        await bot.store.initialize()
        p = panel()
        await bot.store.save(p)
        c = candidate(points=((time.time(), 1),))
        state = bot.engine.states.setdefault("30", PlaceState(candidates={c.job_id: c}))
        messages = []
        async def send(*args, **kwargs):
            messages.append((args, kwargs))
        original_receipt = bot.store.receipt
        async def racing_receipt(*args):
            receipt = await original_receipt(*args)
            state.candidates[c.job_id] = replace(c, playing=10)
            return receipt
        bot.store.receipt = racing_receipt
        interaction = SimpleNamespace(user=SimpleNamespace(id=99), followup=SimpleNamespace(send=send))
        await PanelView(bot, p, []).deliver(interaction, p, c)
        assert len(messages) == 1 and "view" not in messages[0][1]
        await bot.close()
    asyncio.run(run())


def test_deep_profile_requires_minute_of_observed_stability():
    p = panel(profile="profundo")
    proven = candidate(points=((40, 1), (55, 1), (70, 1), (85, 1), (100, 1)))
    young = candidate(2)
    assert ranked([young, proven], p, 100) == [proven]
    assert ranked([proven], p, 111) == []
    interrupted = candidate(3, ((1, 1), (90, 1), (100, 1)))
    assert ranked([interrupted], p, 100) == []


def test_deep_frontier_advances_with_one_page_and_survives_monitoring(tmp_path, monkeypatch):
    async def run():
        clock = [1000.0]
        monkeypatch.setattr("serverbot.engine.time.time", lambda: clock[0])
        store = Store(str(tmp_path / "deep.db"))
        await store.initialize()
        p = panel(profile="profundo", pages=1)
        class API:
            calls = []
            async def servers(self, place, cursor):
                self.calls.append(cursor)
                number = int(cursor) if cursor else 1
                return [Observation(str(UUID(int=number)), 1, 20, clock[0])], str(number + 1)
        api = API()
        engine = Engine(store, api)
        for _ in range(4):
            await engine.scan("30", [p])
            clock[0] += 10
        assert api.calls == [None, "2", "2", "3"]
        assert engine.states["30"].explore_cursor == "4"
        assert engine.states["30"].max_depth == 3
        assert engine.results(p) == []  # Acquisition progress is not proof of a safe recommendation.
        await store.close()
    asyncio.run(run())
