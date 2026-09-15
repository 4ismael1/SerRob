"""Bounded public capture and causal replay. API reobservation is not join success."""
import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serverbot.analytics import ranked, outcome_summary
from serverbot.models import Observation, Panel, parse_place
from serverbot.roblox import Roblox, ProviderError
from serverbot.storage import Store


async def capture(args):
    place = parse_place(args.place)
    api = Roblox(args.rpm)
    await api.start()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Exclusive creation prevents accidental loss of an earlier audit.
        with path.open("x", encoding="utf-8") as output:
            for step in range(args.rounds):
                start = time.time()
                observations, visited, cursor = {}, set(), None
                try:
                    async with asyncio.timeout(30):
                        for _ in range(args.pages):
                            visited.add(cursor)
                            page, cursor = await api.servers(place, cursor)
                            observations.update((o.job_id, asdict(o)) for o in page)
                            if not cursor or cursor in visited:
                                break
                except (ProviderError, TimeoutError) as exc:
                    print(f"CAPTURE_STOPPED | {type(exc).__name__}: {exc}")
                    break  # No retry loop or quota bypass during an audit.
                row = {"place": place, "at": time.time(), "observations": list(observations.values())}
                output.write(json.dumps(row) + "\n")
                output.flush()
                print(f"CAPTURE | {step + 1}/{args.rounds} | {len(observations)} observations", flush=True)
                if step + 1 < args.rounds:
                    await asyncio.sleep(max(0, start + args.interval - time.time()))
    finally:
        await api.close()


async def replay(rows):
    """Resolve previous predictions before ingesting each snapshot; never use future ranking data."""
    stores = {mode: Store(":memory:") for mode in ("rapido", "equilibrado", "precision", "profundo")}
    panels = {mode: Panel(mode, "audit", "audit", "audit", "Audit", profile=mode) for mode in stores}
    candidates = {mode: {} for mode in stores}
    pending = {mode: [] for mode in stores}
    counts = {mode: {"success": 0, "failed": 0, "unknown": 0, "pending": 0, "empty_snapshots": 0} for mode in stores}
    incumbents = {mode: () for mode in stores}
    last_at = float("-inf")
    try:
        for store in stores.values():
            await store.initialize()
        for row in rows:
            observations = [Observation(**o) for o in row["observations"]]
            now = row.get("at", max((o.observed_at for o in observations), default=last_at))
            if now < last_at:
                raise ValueError("Snapshots must be chronological")
            last_at = now
            by_id = {o.job_id: o for o in observations}
            for mode, store in stores.items():
                keep = []
                for job, issued in pending[mode]:
                    obs = by_id.get(job)
                    if obs and issued + 10 <= obs.observed_at <= issued + 30:
                        counts[mode]["success" if obs.playing <= 1 else "failed"] += 1
                    elif now > issued + 30:
                        counts[mode]["unknown"] += 1
                    else:
                        keep.append((job, issued))
                pending[mode] = keep
                for c in await store.observe("audit", observations):
                    candidates[mode][c.job_id] = c
                candidates[mode] = {j:c for j,c in candidates[mode].items() if now-c.observed_at <= 300}
                selected = ranked(candidates[mode].values(), panels[mode], now, incumbents[mode])
                incumbents[mode] = tuple(c.job_id for c in selected)
                counts[mode]["empty_snapshots"] += not selected
                pending[mode].extend((c.job_id, now) for c in selected
                                     if not any(j == c.job_id for j, _ in pending[mode]))
        return {mode: {**outcome_summary(counts[mode]), "pending": len(pending[mode]),
                       "empty_snapshots": counts[mode]["empty_snapshots"]} for mode in stores}
    finally:
        for store in stores.values():
            await store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    live = commands.add_parser("capture")
    live.add_argument("place")
    live.add_argument("--output", required=True)
    live.add_argument("--rounds", type=int, default=30)
    live.add_argument("--interval", type=int, default=15)
    live.add_argument("--pages", type=int, default=2)
    live.add_argument("--rpm", type=int, default=20)
    offline = commands.add_parser("replay")
    offline.add_argument("input")
    args = parser.parse_args()
    if args.command == "capture":
        if not (1 <= args.rounds <= 360 and 5 <= args.interval <= 300 and 1 <= args.pages <= 5 and 1 <= args.rpm <= 30):
            parser.error("rounds 1–360, interval 5–300, pages 1–5, rpm 1–30")
        asyncio.run(capture(args))
    else:
        with Path(args.input).open(encoding="utf-8") as source:
            rows = [json.loads(line) for line in source if line.strip()]
        places = {row.get("place") for row in rows if row.get("place")}
        if len(places) > 1:
            parser.error("Replay one Place per file")
        print(json.dumps({"metric": "API reobservation, NOT real joins", "snapshots": len(rows),
                          "profiles": asyncio.run(replay(rows))}, indent=2))


if __name__ == "__main__":
    main()
