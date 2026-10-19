"""Balanced matches look like real football (design section 12.5).

Per-team medians over 16 seeds must fall in EPL-like bands. These bands also document the
generator calibration (pundit.engine.generator behaviour parameters).
"""

import statistics

import pytest
from helpers import generated

from pundit.stats import MetricParams, compute

SEEDS = range(16)
BANDS = {
    "passes": (350, 600),
    "pass_accuracy": (72, 90),
    "shots": (7, 18),
    "shots_on_target": (2, 7),
    "xg": (0.6, 2.5),
    "fouls": (7, 15),
    "corners": (2, 8),
    "offsides": (0, 4),
    "cards": (0, 4),
}


@pytest.fixture(scope="module")
def per_team() -> dict[str, list[float]]:
    values: dict[str, list[float]] = {name: [] for name in BANDS}
    values["possession"] = []
    for seed in SEEDS:
        view = generated("balanced", seed).view()
        for team in view.meta.team_ids:
            for name in values:
                values[name].append(compute(view, name, MetricParams(team=team)).value or 0.0)
    return values


@pytest.mark.parametrize("name", sorted(BANDS))
def test_team_medians_are_realistic(per_team: dict[str, list[float]], name: str) -> None:
    low, high = BANDS[name]
    assert low <= statistics.median(per_team[name]) <= high


def test_possession_is_never_lopsided_in_balanced_matches(per_team: dict[str, list[float]]) -> None:
    assert all(35 <= value <= 65 for value in per_team["possession"])


def test_match_level_realism() -> None:
    in_play = []
    sizes = []
    for seed in SEEDS:
        match = generated("balanced", seed)
        in_play.append(compute(match.view(), "ball_in_play_time", MetricParams()).value or 0.0)
        sizes.append(sum(e.type != "player_physical" for e in match.events))
    assert 50 <= statistics.median(in_play) <= 65
    assert 1_500 <= statistics.median(sizes) <= 2_800
