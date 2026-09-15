"""Explainable evidence scoring. A score is NOT a probability of a successful join."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Candidate, Panel


@dataclass(frozen=True)
class Evidence:
    score: float
    band: str
    samples: int
    span: float
    coverage: float
    growth_per_minute: float
    volatility: float
    recent_peak: int
    confirmed: bool
    rising: bool
    reasons: tuple[str, ...]


def evidence(candidate: Candidate, panel: Panel, now: float) -> Evidence:
    floor = max(candidate.observed_at - 600, panel.event_since if panel.profile == "evento" else 0)
    points = [(t, p) for t, p in candidate.history if floor <= t <= candidate.observed_at]
    if not points or points[-1][0] != candidate.observed_at:
        points.append((candidate.observed_at, candidate.playing))
    # A long gap ends the current evidence run; old history never bridges missing data.
    run = [points[-1]]
    for point in reversed(points[:-1]):
        if run[-1][0] - point[0] > 45:
            break
        if run[-1][0] - point[0] >= 3:
            run.append(point)
    run.reverse()
    span = run[-1][0] - run[0][0]
    intervals = [(b[0] - a[0], b[1] - a[1]) for a, b in zip(run, run[1:])]
    duration = sum(dt for dt, _ in intervals)
    covered = sum(min(15, dt) for dt, _ in intervals)
    coverage = covered / duration if duration else 0.0
    positive = sum(max(0, delta) for _, delta in intervals)
    growth = 60 * positive / duration if duration else 0.0
    # Recent least-squares trend detects a filling wave without interpreting departures as safety.
    recent = run[-5:]
    if len(recent) >= 2:
        xs = [(t - recent[0][0]) / 60 for t, _ in recent]
        ys = [p for _, p in recent]
        mean_x, mean_y = statistics.mean(xs), statistics.mean(ys)
        denom = sum((x - mean_x) ** 2 for x in xs)
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom if denom else 0
        growth = max(growth, slope)
    volatility = statistics.pstdev(p for _, p in run) if len(run) > 1 else 0
    last_window = [(t, p) for t, p in points if t >= candidate.observed_at - 90]
    peak = max((p for _, p in last_window), default=candidate.playing)
    last_bad = max((t for t, p in last_window if p > panel.max_players), default=0)
    recovery = sum(t > last_bad and p <= panel.max_players for t, p in run)
    rising = growth > 2 or (last_bad > 0 and candidate.observed_at - last_bad < 30 and recovery < 3)
    confirmed = len(run) >= 2 and span >= 5 and all(p <= panel.max_players for _, p in run[-2:]) and not rising
    stable = confirmed and len(run) >= 4 and span >= 30 and peak <= panel.max_players and volatility <= 0.5 and coverage >= 0.6
    age = max(0, now - candidate.observed_at)
    freshness = max(0, 1 - age / max(1, panel.ttl))
    occupancy = max(0, 1 - candidate.playing / (panel.max_players + 1))
    low_duration = min(1, math.log1p(max(0, candidate.observed_at - max(candidate.low_since, floor))) / math.log1p(300))
    score = (30 * freshness + 16 * occupancy + 20 * low_duration * coverage
             + 14 * coverage + 20 * min(1, (len(run) - 1) / 5)
             - min(25, growth * 4) - min(15, volatility * 5)
             - min(20, max(0, peak - panel.max_players) * 4))
    if candidate.playing == 0 and not confirmed:
        score -= 12  # A solitary zero may be a closing or transient instance.
    band = "estable" if stable else "confirmado" if confirmed else "creciendo" if rising else "provisional"
    reasons = []
    if confirmed:
        reasons.append(f"{len(run)} muestras en {int(span)} s")
    else:
        reasons.append("falta reobservación suficiente")
    if rising:
        reasons.append("crecimiento o llenado reciente")
    if candidate.playing == 0:
        reasons.append("cero jugadores: acceso incierto")
    if coverage < 0.6 and len(run) > 1:
        reasons.append("huecos entre observaciones")
    return Evidence(round(max(0, min(100, score)), 1), band, len(run), span, coverage,
                    round(growth, 2), round(volatility, 2), peak, confirmed, rising, tuple(reasons))


def ranked(candidates, panel: Panel, now: float, incumbents: tuple[str, ...] = ()) -> list[Candidate]:
    assessed = []
    ttl = panel.ttl
    for candidate in candidates:
        if not candidate.eligible(panel, now) or now - candidate.observed_at > ttl:
            continue
        stats = evidence(candidate, panel, now)
        if panel.profile in {"precision", "evento", "profundo"} and (not stats.confirmed or stats.score < 60):
            continue
        if panel.profile == "profundo" and (stats.band != "estable" or stats.span < 60):
            continue
        sticky = 3 if candidate.job_id in incumbents and now - candidate.observed_at <= ttl / 2 else 0
        if panel.profile == "rapido":
            key = (candidate.playing, -candidate.observed_at, -stats.score, candidate.job_id)
        else:
            key = (-(stats.score + sticky), candidate.playing, -candidate.observed_at, candidate.job_id)
        assessed.append((key, candidate))
    return [c for _, c in sorted(assessed, key=lambda item: item[0])[:5]]


def outcome_summary(rows: dict[str, int]) -> dict:
    success, failed, unknown = (rows.get(k, 0) for k in ("success", "failed", "unknown"))
    total = success + failed + unknown
    known = success + failed
    # Bounds include unknown outcomes rather than counting disappearance as failure/success.
    return {"success": success, "failed": failed, "unknown": unknown, "total": total,
            "coverage": known / total if total else None,
            "known_rate": success / known if known else None,
            "lower": success / total if total else None,
            "upper": (success + unknown) / total if total else None,
            "goal_supported": total >= 30 and success / total >= 0.7}
