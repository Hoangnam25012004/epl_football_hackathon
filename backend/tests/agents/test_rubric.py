"""Judging rules (design sections 5.1-5.4): aggregation of every combination, tolerance edges,
comparisons, ratios and the dominance rubric."""

import itertools

import pytest

from pundit.agents.contracts import Outcome, Verdict
from pundit.agents.rubric import (
    DominanceInputs,
    aggregate,
    judge_compare,
    judge_dominance,
    judge_value,
    negate,
)

OUTCOMES: tuple[Outcome, ...] = ("SUPPORTED", "PARTIAL", "CONTRADICTED", "INSUFFICIENT")


def expected_verdict(outcomes: tuple[Outcome, ...]) -> Verdict:
    """The table of section 5.4, written independently of the implementation."""
    if all(o == "INSUFFICIENT" for o in outcomes):
        return "INSUFFICIENT_DATA"
    if set(outcomes) == {"SUPPORTED"}:
        return "TRUE"
    if set(outcomes) == {"CONTRADICTED"}:
        return "FALSE"
    return "PARTLY_TRUE"


def all_combinations() -> list[tuple[Outcome, ...]]:
    return [c for n in (1, 2, 3) for c in itertools.product(OUTCOMES, repeat=n)]


def test_every_combination_of_one_to_three_assertions() -> None:
    combos = all_combinations()
    assert len(combos) == 4 + 16 + 64 == 84
    for combo in combos:
        assert aggregate(combo) == expected_verdict(combo), combo


def test_empty_spec_is_insufficient() -> None:
    assert aggregate([]) == "INSUFFICIENT_DATA"


def test_negation() -> None:
    assert negate("SUPPORTED") == "CONTRADICTED"
    assert negate("CONTRADICTED") == "SUPPORTED"
    assert negate("PARTIAL") == "PARTIAL"
    assert negate("INSUFFICIENT") == "INSUFFICIENT"
    for outcome in OUTCOMES:
        assert negate(negate(outcome)) == outcome


@pytest.mark.parametrize(
    ("actual", "stated", "op", "unit", "approx", "outcome"),
    [
        # percent: +-2.5 exact (partial up to 5), +-5 approximate (partial up to 10)
        (57.5, 60, "==", "percent", False, "SUPPORTED"),
        (57.4, 60, "==", "percent", False, "PARTIAL"),
        (54.9, 60, "==", "percent", False, "CONTRADICTED"),
        (55.0, 60, "~", "percent", True, "SUPPORTED"),
        (50.1, 60, "~", "percent", True, "PARTIAL"),
        (49.9, 60, "~", "percent", True, "CONTRADICTED"),
        (61.29, 61, "==", "percent", False, "SUPPORTED"),
        # count: exact; off by one is partial from 5 on
        (15, 15, "==", "count", False, "SUPPORTED"),
        (15, 14, "==", "count", False, "PARTIAL"),
        (15, 13, "==", "count", False, "CONTRADICTED"),
        (4, 3, "==", "count", False, "CONTRADICTED"),
        (15, 14, "~", "count", True, "SUPPORTED"),
        (15, 13, "~", "count", True, "PARTIAL"),
        (15, 12, "~", "count", True, "CONTRADICTED"),
        (0, 0, "==", "count", False, "SUPPORTED"),
        # xg: +-0.05 / +-0.15
        (2.24, 2.2, "==", "xg", False, "SUPPORTED"),
        (2.24, 2.15, "==", "xg", False, "PARTIAL"),
        (2.24, 2.0, "==", "xg", False, "CONTRADICTED"),
        # bounds
        (11, 10, ">", "count", False, "SUPPORTED"),
        (10, 10, ">", "count", False, "CONTRADICTED"),
        (10, 10, ">=", "count", False, "SUPPORTED"),
        (1, 2, "<=", "count", False, "SUPPORTED"),
        (3, 2, "<=", "count", False, "CONTRADICTED"),
        (59.0, 60, ">", "percent", False, "PARTIAL"),
        (55.0, 60, ">", "percent", False, "CONTRADICTED"),
        (4, 5, "<", "count", False, "SUPPORTED"),
    ],
)
def test_values(
    actual: float, stated: float, op: str, unit: str, approx: bool, outcome: str
) -> None:
    assert judge_value(actual, stated, op, unit, approx).outcome == outcome  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("a", "b", "op", "unit", "ratio", "outcome"),
    [
        (15, 1, ">", "count", None, "SUPPORTED"),
        (1, 15, ">", "count", None, "CONTRADICTED"),
        (3, 3, ">", "count", None, "CONTRADICTED"),
        (3, 3, ">=", "count", None, "SUPPORTED"),
        (53.0, 47.0, ">", "percent", None, "SUPPORTED"),
        (51.0, 49.0, ">", "percent", None, "PARTIAL"),
        (47.0, 53.0, ">", "percent", None, "CONTRADICTED"),
        (5.34, 13.2, "<", "ratio", None, "SUPPORTED"),
        (13.2, 5.34, "<", "ratio", None, "CONTRADICTED"),
        (5, 5, "==", "count", None, "SUPPORTED"),
        (6, 5, "==", "count", None, "PARTIAL"),
        (3, 2, "==", "count", None, "CONTRADICTED"),
        (50.0, 48.0, "==", "percent", None, "SUPPORTED"),
        # ratio: met from 0.9 x the stated ratio
        (19, 10, ">", "count", 2.0, "SUPPORTED"),
        (18, 10, ">", "count", 2.0, "SUPPORTED"),
        (17, 10, ">", "count", 2.0, "PARTIAL"),
        (10, 10, ">", "count", 2.0, "CONTRADICTED"),
        (5, 0, ">", "count", 2.0, "SUPPORTED"),
        (0, 0, ">", "count", 2.0, "CONTRADICTED"),
        (2, 10, "<", "count", 3.0, "SUPPORTED"),
    ],
)
def test_comparisons(
    a: float, b: float, op: str, unit: str, ratio: float | None, outcome: str
) -> None:
    assert judge_compare(a, b, op, unit, ratio=ratio).outcome == outcome  # type: ignore[arg-type]


def test_swapping_sides_flips_a_strict_comparison() -> None:
    """C1 at the rule level: a > b holds exactly when b > a is contradicted (unless equal)."""
    for a, b in itertools.product(range(5), repeat=2):
        forward = judge_compare(a, b, ">", "count").outcome
        backward = judge_compare(b, a, ">", "count").outcome
        if a != b:
            assert {forward, backward} == {"SUPPORTED", "CONTRADICTED"}


BASE = DominanceInputs(
    possession=61, shots=15, opponent_shots=1, xg=2.24, opponent_xg=0.24, momentum=0.91
)


@pytest.mark.parametrize(
    ("changes", "outcome", "met"),
    [
        ({}, "SUPPORTED", 4),
        ({"possession": 50.0}, "SUPPORTED", 3),
        ({"possession": 50.0, "momentum": 0.0}, "PARTIAL", 2),
        ({"possession": 50.0, "momentum": 0.0, "xg": 0.3}, "CONTRADICTED", 1),
        (
            {
                "possession": 39,
                "shots": 1,
                "opponent_shots": 15,
                "xg": 0.24,
                "opponent_xg": 2.24,
                "momentum": -0.91,
            },
            "CONTRADICTED",
            0,
        ),
    ],
)
def test_dominance_rubric(changes: dict[str, float], outcome: str, met: int) -> None:
    judgement = judge_dominance(BASE.__class__(**{**BASE.__dict__, **changes}))
    assert judgement.outcome == outcome
    assert judgement.rationale.startswith(f"dominance_{met}of4")


def test_dominance_thresholds() -> None:
    def k(**changes: float) -> str:
        values = {**BASE.__dict__, "possession": 50.0, "xg": 0.0, "momentum": 0.0, **changes}
        return judge_dominance(DominanceInputs(**values)).rationale

    assert "possession" in k(possession=55.0)
    assert "possession" not in k(possession=54.9)
    assert "shots" in k(shots=4, opponent_shots=2)  # 2x and +2
    assert "shots" not in k(shots=3, opponent_shots=2)  # 1.5x but only +1
    assert "shots" not in k(shots=5, opponent_shots=4)  # +1, 1.25x
    assert "xg" in k(xg=0.54, opponent_xg=0.24)
    assert "xg" not in k(xg=0.53, opponent_xg=0.24)
    assert "momentum" in k(momentum=0.3)
    assert "momentum" not in k(momentum=0.29)
