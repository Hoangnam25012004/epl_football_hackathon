"""The voice over the API (docs/day5-design.md sections 3, 9 and 12.2): two viewers of one match,
one listening; cues on live spoken lines only; the audio endpoint; recaps, greeting, voices,
health, the agent trace; a refused voice; a failed clip; TTS_PROVIDER=off (the Day 4 app)."""

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import generated
from starlette.websockets import WebSocketDisconnect

from pundit.agents.triggers import detect_triggers
from pundit.api.app import create_app
from pundit.engine.replay import SteppingClock
from pundit.llm.settings import Settings
from pundit.speech.audio import parse_wav

MATCH = "dominant_loser-42"
PROFILE = "mode=casual&lang=vi&club=HAR&beginner=1"
Message = dict[str, Any]


@dataclass
class Played:
    client: TestClient
    hello_voice: Message
    hello_plain: Message
    voice: list[Message]
    plain: list[Message]


def voice_settings(root: Path, **values: Any) -> Settings:
    settings: dict[str, Any] = {
        "studio_queue_size": 4096, "studio_drop_at": 4096, "tts_provider": "mock",
        "audio_cache_dir": str(root / "cache"), "audio_store_dir": str(root / "store"),
        "mock_tts_time_scale": 0.01,
    }  # fmt: skip
    settings.update(values)
    return Settings(**settings)


def is_last(message: Message) -> bool:
    payload = message["payload"]
    return message["type"] == "recap" and payload["scope"] == "FULL" and payload["revision"] == 2


def read_until(ws: Any, done: Callable[[Message], bool], limit: int = 50_000) -> list[Message]:
    messages = []
    for _ in range(limit):
        message = ws.receive_json()
        messages.append(message)
        if done(message):
            return messages
    raise AssertionError("the stream never ended")


def wait_studio(client: TestClient, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    match = generated("dominant_loser", 42)
    total = len(detect_triggers(match.meta, match.events))
    info: Message = {}
    while time.monotonic() < deadline:
        info = client.get(f"/matches/{MATCH}").json()["info"]
        studio = info.get("studio") or {}
        if (
            info["status"] == "finished"
            and studio.get("processed") == total
            and {"FULL@2", "H1@2"} <= set(studio.get("recaps", []))
        ):
            return
        time.sleep(0.05)
    raise AssertionError(f"the studio did not finish: {info}")


@pytest.fixture(scope="module")
def played(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Played]:
    app = create_app(voice_settings(tmp_path_factory.mktemp("voice")),
                     clock_factory=SteppingClock, queue_size=20_000)  # fmt: skip
    with TestClient(app) as client:
        body = {"scenario": "dominant_loser", "seed": 42, "speed": 20, "autostart": False}
        assert client.post("/matches", json=body).status_code == 200
        with (
            client.websocket_connect(f"/ws/match/{MATCH}?{PROFILE}&voice=female") as voice,
            client.websocket_connect(f"/ws/match/{MATCH}?{PROFILE}") as plain,
        ):
            hello_voice, hello_plain = voice.receive_json(), plain.receive_json()
            client.post(f"/matches/{MATCH}/start")
            wait_studio(client)
            stream_voice, stream_plain = read_until(voice, is_last), read_until(plain, is_last)
        yield Played(client, hello_voice, hello_plain, stream_voice, stream_plain)


def commentary(messages: list[Message]) -> list[Message]:
    return [m for m in messages if m["type"] == "commentary"]


def test_cues_only_on_live_spoken_lines(played: Played) -> None:
    spoken = 0
    for message in commentary(played.voice):
        payload, speech = message["payload"], message["payload"]["speech"]
        if not payload["speak"]:
            assert speech is None
            continue
        spoken += 1
        assert speech["lang"] == "vi-VN" and speech["voice"] == "vi-VN-HoaiMyNeural"
        assert speech["note"] is None and speech["segments"]
        assert speech["audio"]["url"] == f"/audio/{speech['audio']['clip_id']}.wav"
        assert not any(ch.isdigit() for s in speech["segments"] for ch in s["text"])
    assert spoken >= 20


def test_the_listener_gets_the_day4_payload_plus_a_cue(played: Played) -> None:
    assert [m["n"] for m in played.voice] == [m["n"] for m in played.plain]  # one numbering
    for with_voice, without in zip(commentary(played.voice), commentary(played.plain),
                                   strict=True):  # fmt: skip
        assert without["payload"]["speech"] is None
        rest = {k: v for k, v in with_voice["payload"].items() if k != "speech"}
        assert rest == {k: v for k, v in without["payload"].items() if k != "speech"}


def test_hello_never_speaks(played: Played) -> None:
    assert played.hello_voice["payload"]["profile"]["voice"] == "female"
    assert played.hello_plain["payload"]["profile"]["voice"] is None
    client = played.client
    with client.websocket_connect(f"/ws/match/{MATCH}?{PROFILE}&voice=male") as late:
        hello = late.receive_json()
    assert hello["payload"]["commentary"]
    assert all(item["speech"] is None for item in hello["payload"]["commentary"])
    assert all(recap["speech"] is None for recap in hello["payload"]["recaps"])


def test_audio_endpoint(played: Played) -> None:
    client = played.client
    urls = [m["payload"]["speech"]["audio"]["url"] for m in commentary(played.voice)
            if m["payload"]["speech"]][:5]  # fmt: skip
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200 and response.headers["content-type"] == "audio/wav"
        assert "immutable" in response.headers["cache-control"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert parse_wav(response.content).duration_ms > 0
    head = client.head(urls[0])
    assert head.status_code == 200 and head.content == b"" and int(head.headers["content-length"])
    part = client.get(urls[0], headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100
    assert part.headers["content-range"].startswith("bytes 0-99/")
    unknown = client.get(f"/audio/{'f' * 32}.wav")
    assert unknown.status_code == 404 and unknown.headers["cache-control"] == "no-store"
    for bad in ("x.wav", "../../etc/passwd", f"{'a' * 32}.exe", f"{'A' * 32}.wav"):
        assert client.get(f"/audio/{bad}").status_code in (404, 422)
    assert client.get(f"/audio/{'a' * 31}.wav").status_code == 422


def test_recaps_are_read_aloud(played: Played) -> None:
    client = played.client
    voiced = [m["payload"] for m in played.voice if m["type"] == "recap"]
    assert {r["scope"] for r in voiced} == {"H1", "FULL"}
    for recap in voiced:
        assert recap["speech"]["audio"] is not None and recap["speech"]["preset"] == "recap"
    assert all(m["payload"]["speech"] is None for m in played.plain if m["type"] == "recap")
    query = "scope=FULL&lang=vi&mode=casual"
    rest = client.get(f"/matches/{MATCH}/recap?{query}&voice=male").json()
    assert rest["speech"]["voice"] == "vi-VN-NamMinhNeural"
    audio = client.get(rest["speech"]["audio"]["url"])
    assert audio.status_code == 200 and parse_wav(audio.content).duration_ms > 0
    assert client.get(f"/matches/{MATCH}/recap?{query}").json()["speech"] is None
    assert client.get(f"/matches/{MATCH}/recap?{query}&voice=robot").status_code == 422


def test_voices_greeting_health_and_trace(played: Played) -> None:
    client = played.client
    voices = client.get("/speech/voices").json()
    assert [(v["voice"], v["server_audio"]) for v in voices] == [
        ("vi-VN-HoaiMyNeural", True), ("vi-VN-NamMinhNeural", True),
    ]  # fmt: skip
    greeting = client.get("/speech/greeting?lang=vi&voice=female").json()
    assert greeting["preset"] == "greeting" and greeting["audio"]["url"].startswith("/audio/")
    assert client.get(greeting["audio"]["url"]).status_code == 200
    assert client.get("/speech/greeting?lang=en").status_code == 404
    speech = client.get("/healthz").json()["speech"]
    assert (
        speech["synthesized"] > 20 and speech["guard_rejected"] == 0 and speech["store"] == "local"
    )
    spoken = next(m["payload"] for m in commentary(played.voice) if m["payload"]["speech"])
    detail = client.get(f"/matches/{MATCH}/studio/{spoken['id']}").json()
    narrator_spans = [s for s in detail["trace"] if s["agent"] == "Narrator"]
    assert {s["name"] for s in narrator_spans} >= {"narrate", "tts.synthesize"}
    studio_end = max(s["start_ms"] + s["duration_ms"] for s in detail["trace"]
                     if s["agent"] != "Narrator")  # fmt: skip
    assert min(s["start_ms"] for s in narrator_spans) >= studio_end - 1e-6


def test_a_bad_voice_is_refused(played: Played) -> None:
    with (
        played.client.websocket_connect(f"/ws/match/{MATCH}?voice=robot") as ws,
        pytest.raises(WebSocketDisconnect) as closed,
    ):
        ws.receive_json()
    assert closed.value.code == 4422 and "voice" in closed.value.reason


def late_stream(client: TestClient, query: str) -> list[Message]:
    body = {"scenario": "dominant_loser", "seed": 42, "speed": 20, "from_minute": 85,
            "autostart": False}  # fmt: skip
    client.post("/matches", json=body)
    with client.websocket_connect(f"/ws/match/{MATCH}?{query}") as ws:
        ws.receive_json()
        client.post(f"/matches/{MATCH}/start")
        return read_until(ws, spoken_or_final)


def spoken_or_final(message: Message) -> bool:
    """The first line read aloud, or the full-time line (nothing is read without the voice)."""
    if message["type"] != "commentary":
        return False
    payload = message["payload"]
    return payload["speech"] is not None or payload["kind"] == "full_time"


def test_a_failed_clip_answers_503(tmp_path: Path) -> None:
    app = create_app(voice_settings(tmp_path, mock_tts_faults="error"),
                     clock_factory=SteppingClock, queue_size=20_000)  # fmt: skip
    with TestClient(app) as client:
        messages = late_stream(client, f"{PROFILE}&voice=female")
        spoken = [m for m in commentary(messages) if m["payload"]["speech"]]
        assert spoken
        response = client.get(spoken[0]["payload"]["speech"]["audio"]["url"])
        assert response.status_code == 503
        assert response.json() == {"detail": "audio_unavailable", "reason": "unavailable"}
        assert response.headers["retry-after"] == "2"


def test_tts_off_is_the_day4_app(tmp_path: Path) -> None:
    app = create_app(voice_settings(tmp_path, tts_provider="off"),
                     clock_factory=SteppingClock, queue_size=20_000)  # fmt: skip
    with TestClient(app) as client:
        assert "speech" not in client.get("/healthz").json()
        assert client.get(f"/audio/{'a' * 32}.wav").status_code == 404
        assert client.get("/speech/voices").status_code == 404
        messages = late_stream(client, f"{PROFILE}&voice=female")  # accepted, never read
        assert commentary(messages)
        assert all(m["payload"]["speech"] is None for m in commentary(messages))
    assert not (tmp_path / "cache").exists() and not (tmp_path / "store").exists()
