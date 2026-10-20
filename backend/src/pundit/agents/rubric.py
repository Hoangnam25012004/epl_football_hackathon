"""Deterministic judging rules (docs/day2-design.md sections 5.1-5.4).

Constants are documented in the design and calibrated on generated matches (section 14 step 4).
Everything here is a pure function of numbers, so it is tested exhaustively.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from pundit.agents.contracts import Op, Outcome, Verdict
from pundit.stats.result import Unit

EPS = 1e-9

# unit -> (tolerance when stated exactly, tolerance when stated approximately)
TOLERANCES: dict[Unit, tuple[float, float]] = {
    "percent": (2.5, 5.0),
    "count": (0.0, 1.0),
    "xg": (0.05, 0.15),
    "minutes": (0.5, 1.5),
    "kmh": (1.0, 3.0),
    "meters": (1.0, 3.0),
    "ratio": (0.05, 0.15),
    "index": (0.05, 0.15),
    "rating": (0.05, 0.15),
}
COUNT_NEAR_MIN = 5  # a count off by one is PARTIAL only from this value on
RATIO_MET_SHARE = 0.9  # "twice as many" holds from 1.8x

# Dominance rubric (section 5.3)
DOMINANCE_POSSESSION_MIN = 55.0
DOMINANCE_SHOTS_RATIO = 1.5
DOMINANCE_SHOTS_DIFF = 2.0
DOMINANCE_XG_DIFF = 0.3
DOMINANCE_MOMENTUM_MIN = 0.3
DOMINANCE_SUPPORTED_K = 3
DOMINANCE_PARTIAL_K = 2
MIN_IN_PLAY_MINUTES = 5.0  # below this, dominance is INSUFFICIENT (low_sample)


@dataclass(frozen=True)
class Judgement:
    outcome: Outcome
    rationale: str


def tolerance(unit: Unit, approx: bool, actual: float) -> float:
    exact, approximate = TOLERANCES[unit]
    if unit == "count":
        return approximate if approx and actual >= COUNT_NEAR_MIN else 0.0
    return approximate if approx else exact


def partial_band(unit: Unit, approx: bool, actual: float) -> float:
    """Distance beyond which a stated number is CONTRADICTED (2x tolerance; counts: +1)."""
    if unit == "count":
        base = tolerance(unit, approx, actual)
        if base > 0:
            return 2 * base
        return 1.0 if actual >= COUNT_NEAR_MIN or approx else 0.0
    return 2 * tolerance(unit, approx, actual)


def judge_value(actual: float, stated: float, op: Op, unit: Unit, approx: bool) -> Judgement:
    tol = tolerance(unit, approx, actual)
    if op in ("==", "~"):
        diff = abs(actual - stated)
        if diff <= tol + EPS:
            return Judgement("SUPPORTED", "value_within_tolerance")
        if diff <= partial_band(unit, approx, actual) + EPS:
            return Judgement("PARTIAL", "value_near")
        return Judgement("CONTRADICTED", "value_off")
    if op == ">":
        gap = stated - actual  # > 0 means the claim fails
        holds = actual > stated + EPS
    elif op == ">=":
        gap = stated - actual
        holds = actual >= stated - EPS
    elif op == "<":
        gap = actual - stated
        holds = actual < stated - EPS
    else:
        gap = actual - stated
        holds = actual <= stated + EPS
    if holds:
        return Judgement("SUPPORTED", "bound_holds")
    if tol > 0 and gap <= tol + EPS:
        return Judgement("PARTIAL", "bound_marginal")
    return Judgement("CONTRADICTED", "bound_fails")


def judge_compare(
    a: float, b: float, op: Op, unit: Unit, *, ratio: float | None = None, approx: bool = False
) -> Judgement:
    """`a op b` for the subject (a) and the other side (b); polarity is already in `op`."""
    if ratio is not None:
        high, low = (a, b) if op in (">", ">=") else (b, a)
        if high > 0 and (low <= 0 or high / low >= RATIO_MET_SHARE * ratio - EPS):
            return Judgement("SUPPORTED", "ratio_met")
        if high > low:
            return Judgement("PARTIAL", "ratio_short")
        return Judgement("CONTRADICTED", "ratio_fails")
    if op in ("==", "~"):
        diff = abs(a - b)
        reference = max(a, b)
        if diff <= tolerance(unit, approx or op == "~", reference) + EPS:
            return Judgement("SUPPORTED", "equal_within_tolerance")
        if diff <= partial_band(unit, approx or op == "~", reference) + EPS:
            return Judgement("PARTIAL", "nearly_equal")
        return Judgement("CONTRADICTED", "not_equal")
    diff = a - b if op in (">", ">=") else b - a  # > 0 means the claim holds
    strict = op in (">", "<")
    if unit == "count":
        holds = diff > EPS if strict else diff >= -EPS
        return (
            Judgement("SUPPORTED", "compare_holds")
            if holds
            else Judgement("CONTRADICTED", "compare_fails")
        )
    tol = tolerance(unit, approx, max(a, b))
    if diff > tol + EPS or (not strict and diff >= -EPS and tol == 0):
        return Judgement("SUPPORTED", "compare_holds")
    if abs(diff) <= tol + EPS:
        return Judgement("PARTIAL", "compare_marginal")
    return Judgement("CONTRADICTED", "compare_fails")


@dataclass(frozen=True)
class DominanceInputs:
    possession: float
    shots: float
    opponent_shots: float
    xg: float
    opponent_xg: float
    momentum: float


def dominance_indicators(d: DominanceInputs) -> dict[str, bool]:
    return {
        "possession": d.possession >= DOMINANCE_POSSESSION_MIN - EPS,
        "shots": d.shots >= DOMINANCE_SHOTS_RATIO * d.opponent_shots - EPS
        and d.shots - d.opponent_shots >= DOMINANCE_SHOTS_DIFF - EPS,
        "xg": d.xg - d.opponent_xg >= DOMINANCE_XG_DIFF - EPS,
        "momentum": d.momentum >= DOMINANCE_MOMENTUM_MIN - EPS,
    }


def judge_dominance(d: DominanceInputs) -> Judgement:
    met = [name for name, ok in dominance_indicators(d).items() if ok]
    k = len(met)
    rationale = f"dominance_{k}of4" + (":" + "+".join(met) if met else "")
    if k >= DOMINANCE_SUPPORTED_K:
        return Judgement("SUPPORTED", rationale)
    if k == DOMINANCE_PARTIAL_K:
        return Judgement("PARTIAL", rationale)
    return Judgement("CONTRADICTED", rationale)


def negate(outcome: Outcome) -> Outcome:
    """Negation flips SUPPORTED and CONTRADICTED; PARTIAL and INSUFFICIENT stay."""
    if outcome == "SUPPORTED":
        return "CONTRADICTED"
    if outcome == "CONTRADICTED":
        return "SUPPORTED"
    return outcome


def aggregate(outcomes: Sequence[Outcome]) -> Verdict:
    """Section 5.4, tested on every combination of 1-3 assertions."""
    if not outcomes or all(o == "INSUFFICIENT" for o in outcomes):
        return "INSUFFICIENT_DATA"
    if all(o == "SUPPORTED" for o in outcomes):
        return "TRUE"
    if all(o == "CONTRADICTED" for o in outcomes):
        return "FALSE"
    return "PARTLY_TRUE"


def is_finite(*values: float | None) -> bool:
    return all(v is not None and math.isfinite(v) for v in values)
