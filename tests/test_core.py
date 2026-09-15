import asyncio
import time
from dataclasses import replace
from uuid import UUID

import pytest

from serverbot.engine import Engine
from serverbot.models import Candidate, Observation, Panel, join_url, parse_page, parse_place, rank
from serverbot.roblox import ProviderError, RateGate, retry_after
from serverbot.storage import Store


def job(number=1):
    return str(UUID(int=number))


def panel(**kwargs):
    return replace(Panel("abcd12345678", "10", "20", "30", "Test"), **kwargs)


def test_parse_game_inputs():
    assert parse_place("2753915549") == "2753915549"
    assert parse_place("<https://www.roblox.com/games/2753915549/Blox-Fruits?x=1>") == "2753915549"
    assert parse_place("https://www.roblox.com/es/games/123/abc") == "123"
    assert parse_place("https://www.roblox.com/games/start?placeId=123") == "123"


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "https://roblox.com.evil.test/games/1", "http://roblox.com/games/1", "https://user:pass@roblox.com/games/1", "https://127.0.0.1/games/1", "https://roblox.com:444/games/1", "https://www.roblox.com/share?code=123", "https://roblox.com/games/start?placeId=1&placeId=2"])
def test_rejects_unsupported_or_unsafe_inputs(value):
    with pytest.raises(ValueError):
        parse_place(value)


def test_schema_and_duplicate_ids():
    observations, cursor = parse_page({"data": [
        {"id": job(), "playing": 0, "maxPlayers": 20},
        {"id": job(), "playing": 1, "maxPlayers": 20},
        {"id": job(2), "playing": True, "maxPlayers": 20},
        {"id": job(3), "playing": 25, "maxPlayers": 20}], "nextPageCursor": "opaque"}, 100)
    assert len(observations) == 1 and observations[0].playing == 1
    assert cursor == "opaque"
    with pytest.raises(ValueError):
        parse_page({"data": [{"id": "invalid"}]}, 100)
    with pytest.raises(ValueError):
        parse_page({"data": [], "nextPageCursor": 12}, 100)


def test_freshness_is_individual_and_occupancy_wins():
    candidates = [
        Candidate(job(), 1, 20, 90, 1, 1, 100),
        Candidate(job(2), 0, 20, 99, 99, 99, 1),
        Candidate(job(3), 1, 20, 10, 1, 1, 100),
        Candidate(job(4), 2, 20, 100, 1, 1, 100),
    ]
    assert [c.job_id for c in rank(candidates, panel(), 100)] == [job(2), job()]
    assert rank(candidates, panel(), 116) == []
    assert rank(candidates, panel(state="paused"), 100) == []
    assert rank(candidates, panel(), 0) == []  # Clock rollback must not create freshness.


def test_retry_after_formats():
    assert retry_after("120", 5) == 120
    assert retry_after("Thu, 01 Jan 1970 00:02:00 GMT", 5, now=100) == 20
    assert retry_after("invalid", 5) == 5
    assert retry_after("nan", 5) == 5
    assert retry_after("-1", 5) == 0
    assert retry_after("86400", 5) == 86400  # Never cap server-mandated delay downwards.


def test_rate_gate_spaces_and_rechecks_global_cooldown():
    async def scenario():
        clock = [0.0]
        sleeps = []
        async def sleep(delay):
            sleeps.append(delay)
            clock[0] += delay
            if len(sleeps) == 1:
                gate.penalize(5)
        gate = RateGate(30, clock=lambda: clock[0], sleep=sleep)
        await gate.acquire()
        await gate.acquire()
        assert sleeps == [2, 5]
        assert clock[0] == 7
    asyncio.run(scenario())


def test_history_persists_without_inventing_continuity(tmp_path):
    async def scenario():
        path = str(tmp_path / "history.db")
        store = Store(path)
        await store.initialize()
        await store.save(panel())
        for when in [100, 115, 130]:
            candidates = await store.observe("30", [Observation(job(), 1, 20, when)])
        assert candidates[0].stable
        assert await store.observe("30", [Observation(job(), 1, 20, 130)]) == []
        await store.close()
        store = Store(path)
        await store.initialize()
        assert (await store.get(panel().id)).freshness == 15
        candidates = await store.observe("30", [Observation(job(), 1, 20, 500)])
        assert candidates[0].first_seen == 100
        assert candidates[0].low_samples == 1
        assert not candidates[0].stable
        # Same JobId at another Place must not inherit history.
        other = await store.observe("31", [Observation(job(), 1, 20, 510)])
        assert other[0].first_seen == 510
        await store.close()
    asyncio.run(scenario())


class FakeRoblox:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def servers(self, place, cursor):
        self.calls.append((place, cursor))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


def test_scan_stops_early_and_never_refreshes_absent_ids(tmp_path):
    async def scenario():
        store = Store(str(tmp_path / "scan.db"))
        await store.initialize()
        p = panel(profile="rapido")
        await store.save(p)
        now = time.time()
        observations = [Observation(job(i), 1, 20, now) for i in range(1, 6)]
        api = FakeRoblox([(observations, "more"), ([Observation(job(), 20, 20, now + 1)], None)])
        engine = Engine(store, api)
        await engine.scan("30", [p])
        assert len(api.calls) == 1
        assert len(engine.results(p, now)) == 5
        await engine.scan("30", [p])
        state = engine.states["30"]
        assert state.candidates[job(2)].observed_at == now
        assert not state.candidates[job()].eligible(p, now + 2)
        assert engine.results(p, now + 16) == []
        await store.close()
    asyncio.run(scenario())


def test_repeated_cursor_and_429_are_bounded(tmp_path):
    async def scenario():
        store = Store(str(tmp_path / "bounded.db"))
        await store.initialize()
        p = panel(pages=5)
        api = FakeRoblox([([], "same"), ([], "same"), ProviderError("429", 100, "rate_limit")])
        engine = Engine(store, api)
        await engine.scan("30", [p])
        assert len(api.calls) == 2
        assert "repetido" in engine.states["30"].reason
        await engine.scan("30", [p])
        blocked = engine.states["30"].blocked_until
        await engine.refresh(p)
        assert engine.states["30"].next_at >= blocked
        await store.close()
    asyncio.run(scenario())


def test_prepare_checks_exact_job_and_config_after_wait(tmp_path):
    async def scenario():
        store = Store(str(tmp_path / "prepare.db"))
        await store.initialize()
        p = panel()
        await store.save(p)
        now = time.time()
        api = FakeRoblox([([Observation(job(), 1, 20, now), Observation(job(2), 0, 20, now)], None)])
        engine = Engine(store, api)
        await engine.scan("30", [p])
        selected = await engine.prepare(p, job())
        assert selected.job_id == job()  # Not rank #1, which is job(2).
        assert job() in join_url("30", selected.job_id)
        await store.save(replace(p, state="paused"))
        assert await engine.prepare(p, job()) is None
        await store.save(replace(p, max_players=0))
        assert await engine.prepare(p, job()) is None
        await store.close()
    asyncio.run(scenario())


def test_cold_start_does_not_restore_live_recommendations(tmp_path):
    async def scenario():
        store = Store(str(tmp_path / "restart.db"))
        await store.initialize()
        await store.observe("30", [Observation(job(), 1, 20, time.time())])
        engine = Engine(store, FakeRoblox([]))
        assert engine.results(panel()) == []
        await store.close()
    asyncio.run(scenario())


def test_best_now_can_choose_new_instance_explicitly(tmp_path):
    async def scenario():
        store = Store(str(tmp_path / "best.db"))
        await store.initialize()
        p = panel()
        await store.save(p)
        now = time.time()
        api = FakeRoblox([([Observation(job(9), 1, 20, now)], None)])
        engine = Engine(store, api)
        await engine.scan("30", [p])
        selected = await engine.prepare_best(p)
        assert selected.job_id == job(9)
        await store.save(replace(p, max_players=0))
        assert await engine.prepare_best(p) is None
        await store.close()
    asyncio.run(scenario())


def test_retention_preserves_panels(tmp_path):
    async def scenario():
        store = Store(str(tmp_path / "retention.db"))
        await store.initialize()
        await store.save(panel())
        await store.observe("30", [Observation(job(), 1, 20, 1)])
        await store.cleanup(now=8 * 86400)
        assert await store.get(panel().id)
        assert await store.run(lambda db: db.execute("SELECT COUNT(*) FROM observations").fetchone()[0]) == 0
        assert await store.run(lambda db: db.execute("SELECT COUNT(*) FROM servers").fetchone()[0]) == 0
        await store.close()
    asyncio.run(scenario())


def test_scheduler_shares_only_identical_places_and_pauses_independently(tmp_path):
    async def scenario():
        store = Store(str(tmp_path / "scheduler.db"))
        await store.initialize()
        first = panel(interval=300)
        second = replace(first, id="bbbb12345678", guild_id="11")
        other = replace(first, id="cccc12345678", place_id="31")
        for p in [first, second, other]:
            await store.save(p)
        class CountingRoblox:
            def __init__(self):
                self.calls = []
            async def servers(self, place, cursor):
                self.calls.append(place)
                return [Observation(job(), 1, 20, time.time())], None
        api = CountingRoblox()
        engine = Engine(store, api)
        engine.start()
        async def wait_for(predicate):
            async with asyncio.timeout(3):
                while not predicate():
                    await asyncio.sleep(0.01)
        try:
            await wait_for(lambda: len(api.calls) == 2 and all(s.generation for s in engine.states.values()))
            assert sorted(api.calls) == ["30", "31"]
            await store.save(replace(first, state="paused"))
            await store.save(replace(second, state="paused"))
            await wait_for(lambda: "30" not in engine.states)
            assert "31" in engine.states
            assert sorted(api.calls) == ["30", "31"]
        finally:
            await engine.close()
            await store.close()
    asyncio.run(scenario())
