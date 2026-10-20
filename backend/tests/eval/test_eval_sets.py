"""Evaluation sets (design section 13.6): the mock must be exact and never wrong; the labelled
numbers are re-checked against the stats so a data change cannot drift silently."""

from functools import cache

import pytest
from helpers import mock_service, run, view_at

from pundit.cli import EVAL_FILES
from pundit.orchestration.evaluation import EvalReport, load_eval, run_eval, seq_at
from pundit.stats import MetricParams, compute, display
from pundit.stats.registry import REGISTRY

LABELLED, HELDOUT = (load_eval(path) for path in EVAL_FILES)


@cache
def report() -> EvalReport:
    return run(run_eval([LABELLED, HELDOUT], mock_service()))


def test_the_sets_have_the_designed_size() -> None:
    assert len(LABELLED.claims) == 20 and not LABELLED.heldout
    assert len(HELDOUT.claims) == 10 and HELDOUT.heldout
    assert len({c.id for c in (*LABELLED.claims, *HELDOUT.claims)}) == 30


def test_mock_is_exact_on_the_labelled_set() -> None:
    result = report()
    misses = [
        (r.case.id, r.result.verdict, r.case.expected)
        for r in result.rows
        if not r.heldout and not r.correct
    ]
    assert misses == []
    assert result.accuracy == 1.0


def test_no_wrong_verdict_and_no_leak_on_either_set() -> None:
    result = report()
    assert [(r.case.id, r.result.verdict) for r in result.wrong] == []
    assert result.leaks == 0 and result.fact_mismatches == []
    assert result.passed(require_full_accuracy=True)
    assert 0.0 <= result.heldout_coverage <= 1.0  # reported, not tuned (design 13.6)


@pytest.mark.parametrize("case", LABELLED.claims, ids=[c.id for c in LABELLED.claims])
def test_labelled_numbers_match_the_stats(case: object) -> None:
    """Oracle check of the file itself: every expected display is recomputed from the data."""
    from pundit.agents.contracts import Subject  # noqa: F401  (documents the key format)

    view = view_at(LABELLED.match.scenario, LABELLED.match.seed, case.at)  # type: ignore[attr-defined]
    for key, expected in case.expect_display.items():  # type: ignore[attr-defined]
        metric, owner, label = key.split(":")
        assert metric in REGISTRY
        team = owner if "-" not in owner else owner.rsplit("-", 1)[0]
        player = owner if "-" in owner else None
        window = {"H1": "first", "H2": "second", "FULL": "full"}[label]
        params = MetricParams(team=team, player=player, window=_window(window))
        assert display(compute(view, metric, params)) == expected, key


def _window(name: str) -> object:
    from pundit.domain.matchtime import FULL, H1, H2

    return {"first": H1, "second": H2, "full": FULL}[name]


def test_report_lines() -> None:
    lines = report().lines()
    assert any(line.startswith("accuracy (labelled set): 100%") for line in lines)
    assert any("wrong verdicts (all sets): 0" in line for line in lines)


def test_seq_at() -> None:
    from helpers import generated

    events = generated(LABELLED.match.scenario, LABELLED.match.seed).events
    assert seq_at(events, None) is None and seq_at(events, "FT") is None
    seq = seq_at(events, "30'")
    assert seq is not None
    assert events[seq].period == 1 and events[seq].clock_ms < 30 * 60_000
    assert events[seq + 1].clock_ms >= 30 * 60_000 or events[seq + 1].period == 2
