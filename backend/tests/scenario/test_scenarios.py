"""Scenario storylines hold for every seed (design section 12.5)."""

import statistics

import pytest
from helpers import SCRIPTED_KEYS, SEEDS, generated

from pundit.domain.events import CardEvent, GoalEvent, OffsideEvent, ShotEvent, VarCheckEvent
from pundit.domain.matchtime import MS_PER_MINUTE
from pundit.engine.scenarios import get_scenario
from pundit.stats import MetricParams, compute


@pytest.mark.parametrize("scenario", SCRIPTED_KEYS)
@pytest.mark.parametrize("seed", SEEDS)
def test_expectations_hold(scenario: str, seed: int) -> None:
    match = generated(scenario, seed)
    assert get_scenario(scenario).failed_expectations(match.view()) == []


@pytest.mark.parametrize("scenario", SCRIPTED_KEYS)
def test_few_retries_are_needed(scenario: str) -> None:
    attempts = [generated(scenario, seed).meta.attempt for seed in SEEDS]
    assert statistics.mean(attempts) <= 3


@pytest.mark.parametrize("scenario", SCRIPTED_KEYS)
@pytest.mark.parametrize("seed", SEEDS)
def test_scripted_goals_happen_on_time_and_nothing_else_scores(scenario: str, seed: int) -> None:
    spec = get_scenario(scenario)
    match = generated(scenario, seed)
    goals = [e for e in match.events if isinstance(e, GoalEvent)]
    assert len(goals) == len(spec.scripted_goals)
    for goal, scripted in zip(goals, spec.scripted_goals, strict=True):
        period, clock_ms = scripted.timing
        assert goal.team == match.meta.team_for(scripted.side)
        assert goal.period == period
        assert 0 <= goal.clock_ms - clock_ms <= 2 * MS_PER_MINUTE


def test_dominant_loser_story() -> None:
    view = generated("dominant_loser", 42).view()
    home, away = view.meta.team_ids
    assert compute(view, "score", MetricParams(team=away)).value == 2
    assert compute(view, "score", MetricParams(team=home)).value == 1


@pytest.mark.parametrize("seed", SEEDS)
def test_comeback_var_story(seed: int) -> None:
    match = generated("comeback_var", seed)
    var_checks = [e for e in match.events if isinstance(e, VarCheckEvent)]
    assert len(var_checks) == 1 and var_checks[0].decision == "overturned"
    var_offsides = [e for e in match.events if isinstance(e, OffsideEvent) and e.via_var]
    assert len(var_offsides) == 1
    view = match.view()
    home, away = match.meta.team_ids
    assert compute(view, "score", MetricParams(team=home)).value == 3
    assert compute(view, "score", MetricParams(team=away)).value == 2


@pytest.mark.parametrize("seed", SEEDS)
def test_red_card_swing_story(seed: int) -> None:
    match = generated("red_card_swing", seed)
    away = match.meta.team_ids[1]
    reds = [
        e
        for e in match.events
        if isinstance(e, CardEvent) and e.team == away and e.color == "red" and e.period == 2
    ]
    assert reds
    first = min(reds, key=lambda e: e.seq)
    period, clock_ms = get_scenario("red_card_swing").scripted_cards[0].timing  # 52'
    assert first.period == period
    assert clock_ms <= first.clock_ms < clock_ms + 8 * MS_PER_MINUTE


@pytest.mark.parametrize("seed", SEEDS)
def test_balanced_goals_come_only_from_shots(seed: int) -> None:
    match = generated("balanced", seed)
    goals = [e for e in match.events if isinstance(e, GoalEvent)]
    scoring_shots = [e for e in match.events if isinstance(e, ShotEvent) and e.outcome == "goal"]
    assert len(goals) == len(scoring_shots)
