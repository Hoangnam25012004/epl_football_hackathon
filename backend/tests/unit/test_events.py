from typing import Any

import pytest
from builders import MatchBuilder
from helpers import generated
from pydantic import ValidationError

from pundit.domain.events import (
    EVENT_ADAPTER,
    EVENT_CLASSES,
    EVENT_TYPES,
    BallDeadEvent,
    GoalEvent,
    OffsideEvent,
    PassEvent,
    PressureEvent,
    ShotEvent,
    SubstitutionEvent,
    VarCheckEvent,
)


def _all_types_sample() -> dict[str, Any]:
    sample: dict[str, Any] = {}
    for scenario in ("comeback_var", "red_card_swing"):
        for event in generated(scenario, 0).events:
            sample.setdefault(event.type, event)
    return sample


def test_generated_matches_cover_every_event_type() -> None:
    assert set(_all_types_sample()) == set(EVENT_TYPES) == set(EVENT_CLASSES)


def test_every_type_round_trips_through_json() -> None:
    for event in _all_types_sample().values():
        assert EVENT_ADAPTER.validate_json(EVENT_ADAPTER.dump_json(event)) == event


def _pass_fields(b: MatchBuilder, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "team": b.team_of("H9"),
        "player": b.p("H9"),
        "x": 50.0,
        "y": 34.0,
        "outcome": "complete",
        "end_x": 60.0,
        "end_y": 34.0,
        "duration_ms": 700,
        "height": "ground",
        "play_type": "open_play",
        "recipient": b.p("H8"),
        "possession_id": 1,
        "possession_team": b.team_of("H9"),
    }
    fields.update(overrides)
    return fields


@pytest.mark.parametrize(
    "overrides",
    [
        {"team": None},
        {"x": None},
        {"recipient": None},
        {"outcome": "incomplete"},  # incomplete pass with a recipient
        {"recipient": "kestrel_athletic-04"},  # recipient from the other team
        {"recipient": "harbor_lions-09"},  # pass to yourself
        {"player": "kestrel_athletic-04"},  # player not in team
        {"possession_team": None},
        {"duration_ms": 0},
        {"end_x": 106.0},
        {"related_ids": ("x",)},
        {"unknown_field": 1},
    ],
)
def test_invalid_pass_is_rejected(overrides: dict[str, Any]) -> None:
    b = MatchBuilder(start=False)
    with pytest.raises(ValidationError):
        b.make(PassEvent, **_pass_fields(b, **overrides))


def test_id_and_minute_must_be_consistent() -> None:
    b = MatchBuilder(start=False)
    event = b.make(PassEvent, **_pass_fields(b))
    data = event.model_dump()
    with pytest.raises(ValidationError):
        PassEvent.model_validate({**data, "id": "test-0-00099"})
    with pytest.raises(ValidationError):
        PassEvent.model_validate({**data, "minute": 7})


def test_events_are_immutable() -> None:
    b = MatchBuilder(start=False)
    event = b.make(PassEvent, **_pass_fields(b))
    with pytest.raises(ValidationError):
        event.x = 1.0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("cls", "fields"),
    [
        (ShotEvent, {"situation": "penalty", "xg": 0.5}),
        (GoalEvent, {"own_goal": True}),
        (GoalEvent, {"related_ids": ()}),
        (VarCheckEvent, {"decision": "overturned", "reason": "none"}),
        (BallDeadEvent, {"reason": "period_end", "related_ids": ("x",)}),
        (OffsideEvent, {"via_var": True, "related_ids": ("x",)}),
        (PressureEvent, {"target_player": "kestrel_athletic-08"}),  # own teammate
        (SubstitutionEvent, {"player_in": "harbor_lions-09"}),
    ],
)
def test_type_specific_rules(cls: type, fields: dict[str, Any]) -> None:
    b = MatchBuilder(start=False)
    common: dict[str, dict[str, Any]] = {
        "ShotEvent": {
            "team": "harbor_lions",
            "player": "harbor_lions-09",
            "x": 94.0,
            "y": 34.0,
            "outcome": "goal",
            "xg": 0.76,
            "body_part": "right_foot",
            "situation": "open_play",
            "speed_kmh": 90.0,
            "possession_id": 1,
            "possession_team": "harbor_lions",
        },
        "GoalEvent": {
            "team": "harbor_lions",
            "player": "harbor_lions-09",
            "related_ids": ("a",),
            "possession_id": 1,
            "possession_team": "harbor_lions",
        },
        "VarCheckEvent": {
            "check_type": "goal",
            "decision": "stands",
            "reason": "none",
            "duration_ms": 1000,
            "related_ids": ("a",),
        },
        "BallDeadEvent": {
            "team": "harbor_lions",
            "reason": "foul",
            "related_ids": ("a",),
            "possession_id": 1,
            "possession_team": "harbor_lions",
        },
        "OffsideEvent": {
            "team": "harbor_lions",
            "player": "harbor_lions-09",
            "x": 90.0,
            "y": 30.0,
            "related_ids": ("a",),
            "possession_id": 1,
            "possession_team": "harbor_lions",
        },
        "PressureEvent": {
            "team": "kestrel_athletic",
            "player": "kestrel_athletic-04",
            "x": 50.0,
            "y": 34.0,
            "target_player": "harbor_lions-09",
            "duration_ms": 500,
            "possession_id": 1,
            "possession_team": "harbor_lions",
        },
        "SubstitutionEvent": {
            "team": "harbor_lions",
            "player": "harbor_lions-09",
            "player_in": "harbor_lions-19",
        },
    }
    base = common[cls.__name__]
    b.make(cls, **base)  # the base sample is valid
    with pytest.raises(ValidationError):
        b.make(cls, **{**base, **fields})
