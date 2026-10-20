"""Day 2 JSON Schemas (design section 3): real results validate; bad requests do not."""

import json

import pytest
from helpers import generated, verify
from jsonschema import Draft202012Validator

from pundit.agents.contracts import WsMessage
from pundit.agents.moments import detect_all
from pundit.cli import _schemas


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads(_schemas()[name]))


def test_claim_results_validate() -> None:
    check = validator("claim_result.schema.json")
    for text in (
        "Đội nhà áp đảo hiệp 2",
        "Trọng tài thiên vị Harbor",
        "Kestrel won despite lower xG",
    ):
        result = verify(text)
        check.validate(json.loads(result.model_dump_json()))
        check.validate(json.loads(result.model_copy(update={"trace": None}).model_dump_json()))


def test_moments_and_ws_messages_validate() -> None:
    match = generated("comeback_var", 7)
    moment_check = validator("moment.schema.json")
    ws_check = validator("ws_message.schema.json")
    for moment in detect_all(match.meta, match.events)[:20]:
        moment_check.validate(json.loads(moment.model_dump_json()))
        message = WsMessage(
            type="moment", n=1, match_id=match.meta.match_id, payload=moment.model_dump(mode="json")
        )
        ws_check.validate(json.loads(message.model_dump_json()))


@pytest.mark.parametrize(
    "body",
    [
        {"match_id": "dominant_loser-42", "text": "Đội nhà áp đảo hiệp 2"},
        {
            "match_id": "dominant_loser-42",
            "text": "Harbor dominated",
            "language": "en",
            "as_of_seq": 10,
            "include_trace": True,
        },
    ],
)
def test_good_requests(body: dict[str, object]) -> None:
    assert validator("claim_request.schema.json").is_valid(body)


@pytest.mark.parametrize(
    "body",
    [
        {"match_id": "dominant_loser-42"},
        {"match_id": "dominant_loser-42", "text": "ab"},
        {"match_id": "dominant_loser-42", "text": "Harbor dominated", "language": "fr"},
        {"match_id": "dominant_loser-42", "text": "Harbor dominated", "as_of_seq": -1},
        {"match_id": "dominant_loser-42", "text": "Harbor dominated", "extra": 1},
        {"match_id": "Dominant Loser", "text": "Harbor dominated"},
    ],
)
def test_bad_requests(body: dict[str, object]) -> None:
    assert not validator("claim_request.schema.json").is_valid(body)
