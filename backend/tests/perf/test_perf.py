"""Time budgets (design section 12.5). Best of three runs to reduce noise.

Coverage tracing slows Python down several times, so these tests skip themselves when
coverage is active; CI runs them in a separate step without coverage.
"""

import sys
import time

import pytest
from helpers import generated

from pundit.engine.generator import generate
from pundit.stats import MetricParams, compute

P0_TEAM_METRICS = (
    "score",
    "possession",
    "passes",
    "pass_accuracy",
    "shots",
    "shots_on_target",
    "xg",
    "final_third_entries",
    "corners",
    "fouls",
    "cards",
    "offsides",
    "pass_length_avg",
    "pass_difficulty_avg",
    "difficult_passes_completed",
)


pytestmark = pytest.mark.skipif(
    "coverage" in sys.modules, reason="timings are meaningless under coverage"
)


def best_of_three(action: object) -> float:
    timings = []
    for _ in range(3):
        start = time.perf_counter()
        action()  # type: ignore[operator]
        timings.append(time.perf_counter() - start)
    return min(timings)


@pytest.mark.perf
def test_generating_a_match_takes_under_two_seconds() -> None:
    assert best_of_three(lambda: generate("dominant_loser", 11)) < 2.0


@pytest.mark.perf
def test_all_p0_metrics_for_a_full_match_take_under_50ms() -> None:
    view = generated("dominant_loser", 0).view()
    team = view.meta.team_ids[0]

    def run_all() -> None:
        for name in P0_TEAM_METRICS:
            compute(view, name, MetricParams(team=team))

    assert best_of_three(run_all) < 0.05
