from typing import Any

import pytest
from pydantic import ValidationError

from pundit.domain.errors import ScenarioError
from pundit.domain.matchtime import H2
from pundit.engine.scenarios import SCENARIOS, Expectation, Scenario, ScriptedGoal, get_scenario
from pundit.engine.scenarios.base import Segment, TeamProfile

NEUTRAL = TeamProfile(control=0.5, press=0.5, threat=0.5, discipline=0.6)


def scenario(**overrides: Any) -> Scenario:
    fields: dict[str, Any] = {
        "key": "custom",
        "title_vi": "t",
        "title_en": "t",
        "home": "harbor_lions",
        "away": "kestrel_athletic",
        "kickoff_utc": "2026-10-24T14:00:00Z",
        "weather": "clear",
        "stoppage_minutes": {1: (1, 3), 2: (3, 5)},
        "segments": (
            Segment(period=1, home=NEUTRAL, away=NEUTRAL),
            Segment(period=2, home=NEUTRAL, away=NEUTRAL),
        ),
        "goal_mode": "scripted_only",
    }
    fields.update(overrides)
    return Scenario(**fields)


def test_catalog_is_valid() -> None:
    assert set(SCENARIOS) == {"dominant_loser", "comeback_var", "red_card_swing", "balanced"}
    with pytest.raises(ScenarioError):
        get_scenario("nope")


@pytest.mark.parametrize(
    "overrides",
    [
        {"away": "harbor_lions"},
        {"segments": (Segment(period=1, home=NEUTRAL, away=NEUTRAL),)},
        {"stoppage_minutes": {1: (3, 1), 2: (3, 5)}},
        {"scripted_goals": (ScriptedGoal(side="home", at="45+3'", pattern="cutback"),)},
        {
            "scripted_goals": (
                ScriptedGoal(side="home", at="30'", pattern="cutback"),
                ScriptedGoal(side="away", at="31'", pattern="cutback"),
            )
        },
        {
            "scripted_goals": (
                ScriptedGoal(side="home", at="60'", pattern="cutback"),
                ScriptedGoal(side="away", at="20'", pattern="cutback"),
            )
        },
        {
            "segments": (
                Segment(period=1, home=NEUTRAL, away=NEUTRAL),
                Segment(period=2, home=NEUTRAL, away=NEUTRAL),
                Segment(period=2, from_minute=30, home=NEUTRAL, away=NEUTRAL),
            )
        },
    ],
)
def test_invalid_scenarios_fail_at_creation(overrides: dict[str, Any]) -> None:
    with pytest.raises((ValidationError, KeyError)):
        scenario(**overrides)


def test_expectations_are_validated() -> None:
    with pytest.raises(ValidationError):
        Expectation(kind="metric", metric="goalz", side="home", op=">", value=1)
    with pytest.raises(ValidationError):
        Expectation(kind="metric", metric="possession", side=None, window=H2, op=">", value=1)
    with pytest.raises(ValidationError):
        Expectation(kind="score_diff", op=">", value=1)


def test_profiles_follow_segments() -> None:
    swing = get_scenario("red_card_swing")
    before = swing.profiles(2, 5 * 60_000)  # 50'
    after = swing.profiles(2, 8 * 60_000)  # 53'
    assert before[0].threat < after[0].threat
    assert swing.profiles(1, 0)[0] == swing.segments[0].home
