"""Day 5 contracts (docs/day5-design.md section 3.6): the new schemas exist, real cues validate,
old payloads stay valid (`speech` is optional), the model rejects inconsistent cues."""

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from pundit.agents.contracts import AudioRef, Profile, SpeechCue, SpeechSegment
from pundit.cli import SCHEMA_DIR

SEGMENT = SpeechSegment(text="Vào!", preset="burst", rate=1.2, pitch=1.18, volume=1.0)
AUDIO = AudioRef(clip_id="a" * 32, url=f"/audio/{'a' * 32}.mp3", media_type="audio/mpeg",
                 duration_ms=1200, exact=False)  # fmt: skip


def schema(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text("utf-8")))


def test_new_schemas_exist_and_validate_a_cue() -> None:
    cue = SpeechCue(lang="vi-VN", voice="vi-VN-HoaiMyNeural", gender="female", preset="peak",
                    segments=(SEGMENT,), audio=AUDIO)  # fmt: skip
    schema("speech_cue").validate(json.loads(cue.model_dump_json()))
    schema("audio_ref").validate(json.loads(AUDIO.model_dump_json()))
    for name in ("speech_voice", "recognition_result"):
        assert (SCHEMA_DIR / f"{name}.schema.json").is_file()


def test_old_payloads_stay_valid_and_speech_validates() -> None:
    validator = schema("commentary_view")
    old: dict[str, Any] = {
        "id": "x-c", "trigger_id": "x", "kind": "goal", "period": 1, "clock_ms": 0,
        "display_minute": "1'", "importance": 3, "language": "vi", "mode": "casual",
        "text": "Vào!", "source": "template",
    }  # fmt: skip
    validator.validate(old)  # a Day 4 payload, without the key
    cue = SpeechCue(lang="vi-VN", voice="vi-VN-HoaiMyNeural", gender="female", preset="calm",
                    segments=(SEGMENT,), note="server_off")  # fmt: skip
    validator.validate({**old, "speech": json.loads(cue.model_dump_json())})
    with pytest.raises(Exception):  # noqa: B017 - jsonschema's ValidationError
        validator.validate({**old, "speech": {"lang": "fr-FR"}})


@pytest.mark.parametrize(
    "values",
    [
        {"audio": AUDIO, "note": "server_off"},  # both
        {},  # neither
        {"note": "guard"},  # a rejected cue keeps no segment
    ],
)
def test_inconsistent_cues_are_refused(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SpeechCue(lang="vi-VN", voice="v", gender="male", preset="calm", segments=(SEGMENT,),
                  **values)  # fmt: skip


def test_profile_key_is_unchanged_without_a_voice() -> None:
    profile = Profile(mode="casual", language="vi", team="harbor_lions", beginner=True)
    assert profile.key() == "casual|vi|harbor_lions|-|1|-"  # the Day 3 key
    assert profile.model_copy(update={"voice": "male"}).key() == "casual|vi|harbor_lions|-|1|-|male"
    with pytest.raises(ValidationError):
        AudioRef(clip_id="../x", url="/x", media_type="audio/mpeg", duration_ms=1, exact=True)
