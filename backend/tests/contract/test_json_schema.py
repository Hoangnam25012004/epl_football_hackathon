"""The committed JSON Schemas match the code, and generated data validates against them."""

import json

import pytest
from helpers import generated
from jsonschema import Draft202012Validator

from pundit.cli import SCHEMA_DIR, _schemas
from pundit.engine.io import events_to_jsonl
from pundit.stats import MetricParams, compute


def test_committed_schemas_are_up_to_date() -> None:
    for name, text in _schemas().items():
        committed = (SCHEMA_DIR / name).read_text(encoding="utf-8")
        assert committed == text, f"{name} is stale: run `uv run pundit schema`"


@pytest.mark.parametrize("scenario", ["comeback_var", "red_card_swing", "balanced"])
def test_generated_data_matches_the_schemas(scenario: str) -> None:
    match = generated(scenario, 0)
    event_validator = Draft202012Validator(json.loads(_schemas()["event.schema.json"]))
    seen_types: set[str] = set()
    for index, line in enumerate(events_to_jsonl(match.events).splitlines()):
        data = json.loads(line)
        if data["type"] not in seen_types or index % 10 == 0:  # every type + a 10% sample
            seen_types.add(data["type"])
            event_validator.validate(data)
    meta_validator = Draft202012Validator(json.loads(_schemas()["match_meta.schema.json"]))
    meta_validator.validate(json.loads(match.meta.model_dump_json()))
    result_validator = Draft202012Validator(json.loads(_schemas()["stat_result.schema.json"]))
    result = compute(match.view(), "possession", MetricParams(team=match.meta.team_ids[0]))
    result_validator.validate(json.loads(result.model_dump_json()))


def test_schema_rejects_a_broken_event() -> None:
    match = generated("balanced", 0)
    validator = Draft202012Validator(json.loads(_schemas()["event.schema.json"]))
    event = json.loads(events_to_jsonl(match.events[:5]).splitlines()[4])
    event["type"] = "dribble"
    assert not validator.is_valid(event)
