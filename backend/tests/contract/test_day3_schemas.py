"""Day 3 contracts (docs/day3-design.md sections 3.8, 3.9 and 21): the exported schemas accept
the payloads the API really sends; only types and fields were added."""

import json
from typing import get_args

from jsonschema import Draft202012Validator
from studio_helpers import offline

from pundit.agents.context import build_match_context
from pundit.agents.contracts import Degraded, Profile, ViolationCode, WsMessage, WsType
from pundit.agents.personalize import render_commentary, render_lower_third, render_recap
from pundit.cli import _schemas


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads(_schemas()[name]))


def test_new_message_types_and_codes_are_added() -> None:
    assert {"talking_point", "lower_third", "commentary", "recap", "talking_point_status"} <= set(
        get_args(WsType)
    )
    assert {"hello", "event", "moment", "claim", "snapshot", "status", "resync"} <= set(
        get_args(WsType)
    )  # Day 2 types are kept: v stays 1
    assert {"WRONG_ENTITY", "WRONG_OUTCOME", "WRONG_LANGUAGE", "WRONG_REGISTER"} <= set(
        get_args(ViolationCode)
    )
    assert {"writer_fallback", "llm_skipped"} <= set(get_args(Degraded))


def test_real_payloads_validate_against_the_exported_schemas() -> None:
    run = offline("comeback_var", 7)
    match = build_match_context(run.view_at(0))
    profile = Profile(mode="casual", language="en", team="ironvale_united", beginner=True)
    views = [render_commentary(o.commentary, profile, match)[0] for o in run.outputs]
    checker = validator("commentary_view.schema.json")
    for view in views:
        checker.validate(json.loads(view.model_dump_json()))
    lower = validator("lower_third_view.schema.json")
    for output in run.outputs:
        if output.lower_third is not None:
            lower.validate(
                json.loads(render_lower_third(output.lower_third, profile).model_dump_json())
            )
        if output.talking_point is not None:
            validator("talking_point.schema.json").validate(
                json.loads(output.talking_point.model_dump_json())
            )
    recap = render_recap(run.recaps["FULL"][-1], profile)
    validator("recap_view.schema.json").validate(json.loads(recap.model_dump_json()))
    validator("profile.schema.json").validate(json.loads(profile.model_dump_json()))
    message = WsMessage(
        type="commentary", n=7, match_id="comeback_var-7", payload=views[0].model_dump(mode="json")
    )
    validator("ws_message.schema.json").validate(json.loads(message.model_dump_json()))
