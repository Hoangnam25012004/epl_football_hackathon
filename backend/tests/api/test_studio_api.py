"""Studio over the API (docs/day3-design.md sections 11.6, 14.4 and 15): personalized WebSocket
streams with one message numbering, causal order (S-8), profile errors and limits, the studio
REST endpoints, health, and STUDIO_ENABLED=false."""

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import DAY2_HELLO_KEYS, STUDIO_HELLO_KEYS, generated
from starlette.websockets import WebSocketDisconnect

from pundit.agents.triggers import detect_triggers
from pundit.api import ws as ws_module
from pundit.api.app import create_app
from pundit.engine.replay import SteppingClock
from pundit.llm.settings import Settings

MATCH = "dominant_loser-42"
CASUAL = "mode=casual&lang=en&club=HAR&beginner=1"
STUDIO_TYPES = {"commentary", "talking_point", "lower_third", "recap"}
Message = dict[str, Any]


@dataclass
class Played:
    client: TestClient
    hello_analyst: Message
    hello_casual: Message
    analyst: list[Message]
    casual: list[Message]


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


def wait_studio(client: TestClient, match_id: str = MATCH, timeout: float = 120.0) -> Message:
    """Until the replay finished and the studio processed every trigger and both recaps."""
    deadline = time.monotonic() + timeout
    info: Message = {}
    match = generated(*_split(match_id))
    total = len(detect_triggers(match.meta, match.events))
    while time.monotonic() < deadline:
        info = client.get(f"/matches/{match_id}").json()["info"]
        studio = info.get("studio") or {}
        if (
            info["status"] == "finished"
            and studio.get("processed") == total
            and "FULL@2" in studio.get("recaps", [])
            and "H1@2" in studio.get("recaps", [])
        ):
            return info
        time.sleep(0.05)
    raise AssertionError(f"the studio did not finish: {info}")


def _split(match_id: str) -> tuple[str, int]:
    scenario, seed = match_id.rsplit("-", 1)
    return scenario, int(seed)


@pytest.fixture(scope="module")
def played() -> Iterator[Played]:
    # the stepping clock replays far faster than real time: a queue that never drops
    settings = Settings(studio_queue_size=4096, studio_drop_at=4096)
    app = create_app(settings, clock_factory=SteppingClock, queue_size=20_000)
    with TestClient(app) as client:
        body = {"scenario": "dominant_loser", "seed": 42, "speed": 1000, "autostart": False}
        assert client.post("/matches", json=body).status_code == 200
        with (
            client.websocket_connect(f"/ws/match/{MATCH}") as analyst,
            client.websocket_connect(f"/ws/match/{MATCH}?{CASUAL}") as casual,
        ):
            hello_analyst, hello_casual = analyst.receive_json(), casual.receive_json()
            client.post(f"/matches/{MATCH}/start")
            wait_studio(client)
            stream_analyst = read_until(analyst, is_last)
            stream_casual = read_until(casual, is_last)
        yield Played(client, hello_analyst, hello_casual, stream_analyst, stream_casual)


# --------------------------------------------------------------------------- WebSocket


def test_hello_echoes_the_profile_and_carries_the_studio_keys(played: Played) -> None:
    for hello in (played.hello_analyst, played.hello_casual):
        assert set(hello["payload"]) == DAY2_HELLO_KEYS | STUDIO_HELLO_KEYS
    assert played.hello_analyst["payload"]["profile"]["mode"] == "analyst"
    casual = played.hello_casual["payload"]["profile"]
    assert casual == {
        "mode": "casual",
        "language": "en",
        "team": "harbor_lions",
        "player": None,
        "beginner": True,
        "metric": None,
    }


def test_every_profile_gets_the_same_numbering(played: Played) -> None:
    analyst, casual = played.analyst, played.casual
    assert [m["n"] for m in analyst] == [m["n"] for m in casual]
    assert [m["type"] for m in analyst] == [m["type"] for m in casual]
    start = played.hello_analyst["n"]
    assert [m["n"] for m in analyst] == list(range(start + 1, start + 1 + len(analyst)))
    assert {m["type"] for m in analyst} >= STUDIO_TYPES


def test_payloads_follow_the_profile(played: Played) -> None:
    pairs = [
        (a["payload"], c["payload"])
        for a, c in zip(played.analyst, played.casual, strict=True)
        if a["type"] == "commentary"
    ]
    assert all(a["language"] == "vi" and a["mode"] == "analyst" for a, _ in pairs)
    assert all(c["language"] == "en" and c["mode"] == "casual" for _, c in pairs)
    assert any(a["text"] != c["text"] for a, c in pairs)
    assert any(a["facts"] for a, _ in pairs) and not any(c["facts"] for _, c in pairs)
    assert any(c["framing"] for _, c in pairs) and not any(a["framing"] for a, _ in pairs)
    explained = [c["explanation"]["rule"] for _, c in pairs if c["explanation"]]
    assert "offside" in explained and not any(a["explanation"] for a, _ in pairs)
    lower_thirds = [
        (a["payload"], c["payload"])
        for a, c in zip(played.analyst, played.casual, strict=True)
        if a["type"] == "lower_third"
    ]
    assert lower_thirds and all(
        a["language"] == "vi" and c["language"] == "en" for a, c in lower_thirds
    )
    talking = [m["payload"] for m in played.analyst if m["type"] == "talking_point"]
    assert talking and all(tp["trace"] is None and tp["en"] and tp["vi"] for tp in talking)


def test_items_come_after_their_event_and_moment(played: Played) -> None:
    """S-8: on the stream, an item's number is greater than its event's and its moment's."""
    match = generated("dominant_loser", 42)
    seq_of = {t.id: t.seq for t in detect_triggers(match.meta, match.events)}
    event_n = {m["payload"]["seq"]: m["n"] for m in played.analyst if m["type"] == "event"}
    moment_n = {m["payload"]["id"]: m["n"] for m in played.analyst if m["type"] == "moment"}
    checked = 0
    last_trigger_n: dict[str, int] = {}
    for message in played.analyst:
        if message["type"] not in ("commentary", "talking_point", "lower_third"):
            continue
        trigger_id = message["payload"]["trigger_id"]
        seq = seq_of[trigger_id]
        if seq in event_n:
            assert message["n"] > event_n[seq], trigger_id
            checked += 1
        if trigger_id in moment_n:
            assert message["n"] > moment_n[trigger_id], trigger_id
        last_trigger_n[trigger_id] = message["n"]
    assert checked > 50
    order = [m["payload"]["trigger_id"] for m in played.analyst if m["type"] == "commentary"]
    assert [seq_of[t] for t in order] == sorted(seq_of[t] for t in order)  # trigger order


def test_recaps_are_published_twice(played: Played) -> None:
    recaps = [m["payload"] for m in played.analyst if m["type"] == "recap"]
    assert [(r["scope"], r["revision"]) for r in recaps] == [
        ("H1", 1),
        ("H1", 2),
        ("FULL", 1),
        ("FULL", 2),
    ]


def test_a_late_client_gets_the_studio_items_in_hello(played: Played) -> None:
    with played.client.websocket_connect(f"/ws/match/{MATCH}?lang=en") as late:
        payload = late.receive_json()["payload"]
    assert len(payload["commentary"]) == 30 and len(payload["talking_points"]) <= 20
    assert all(c["language"] == "en" for c in payload["commentary"])
    assert {r["scope"] for r in payload["recaps"]} == {"H1", "FULL"}


def test_a_bad_profile_is_closed_with_4422(played: Played) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        played.client.websocket_connect(f"/ws/match/{MATCH}?mode=expert") as socket,
    ):
        socket.receive_json()
    assert closed.value.code == 4422 and "mode" in (closed.value.reason or "")


def test_too_many_profiles_are_closed_with_4429(
    played: Played, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ws_module, "MAX_PROFILES", 1)
    with played.client.websocket_connect(f"/ws/match/{MATCH}") as first:
        first.receive_json()
        with (
            pytest.raises(WebSocketDisconnect) as closed,
            played.client.websocket_connect(f"/ws/match/{MATCH}?lang=en") as second,
        ):
            second.receive_json()
        assert closed.value.code == 4429
        with played.client.websocket_connect(f"/ws/match/{MATCH}") as same_profile:
            assert same_profile.receive_json()["type"] == "hello"  # same key: accepted


# --------------------------------------------------------------------------- REST


def test_commentary_rest_follows_the_profile(played: Played) -> None:
    client = played.client
    casual = client.get(f"/matches/{MATCH}/commentary?{CASUAL}").json()
    analyst = client.get(f"/matches/{MATCH}/commentary").json()
    assert len(casual) == len(analyst) > 60
    assert casual[0]["language"] == "en" and analyst[0]["language"] == "vi"
    later = client.get(f"/matches/{MATCH}/commentary?since_seq=1500").json()
    assert 0 < len(later) < len(analyst)
    assert client.get(f"/matches/{MATCH}/commentary?club=Nowhere").status_code == 422
    full = [c for c in casual if c["explanation"] and c["explanation"]["depth"] == "full"]
    assert [c["explanation"]["rule"] for c in full].count("offside") == 1


def test_talking_points_lower_thirds_and_recaps(played: Played) -> None:
    client = played.client
    talking = client.get(f"/matches/{MATCH}/talking-points").json()
    assert len(talking) == 17 and all(tp["trace"] is None for tp in talking)
    english = client.get(f"/matches/{MATCH}/lower-thirds?lang=en").json()
    assert english and all(lt["language"] == "en" and lt["synthetic"] for lt in english)
    recap = client.get(f"/matches/{MATCH}/recap?scope=H1&lang=en&mode=casual").json()
    assert recap["scope"] == "H1" and recap["language"] == "en" and recap["revision"] == 2
    assert client.get(f"/matches/{MATCH}/recap?scope=Q3").status_code == 422


def test_studio_item_with_its_trace(played: Played) -> None:
    client = played.client
    talking = client.get(f"/matches/{MATCH}/talking-points").json()[0]
    detail = client.get(f"/matches/{MATCH}/studio/{talking['id']}").json()
    assert detail["kind"] == "talking_point" and detail["item"]["id"] == talking["id"]
    agents = {span["agent"] for span in detail["trace"]}
    assert {"StudioIntake", "FactPackBuilder", "TalkingPointWriter", "Guardrail"} <= agents
    recap = client.get(f"/matches/{MATCH}/studio/{MATCH}-recap-FULL").json()
    assert recap["kind"] == "recap" and recap["trace"]
    assert client.get(f"/matches/{MATCH}/studio/nope-c").status_code == 404


def test_talking_point_status_is_broadcast(played: Played) -> None:
    client = played.client
    talking = client.get(f"/matches/{MATCH}/talking-points").json()[1]
    with client.websocket_connect(f"/ws/match/{MATCH}") as socket:
        socket.receive_json()
        response = client.post(
            f"/matches/{MATCH}/talking-points/{talking['id']}/status", json={"status": "pinned"}
        )
        assert response.status_code == 200 and response.json()["status"] == "pinned"
        message = socket.receive_json()
    assert message["type"] == "talking_point_status"
    assert message["payload"] == {"id": talking["id"], "status": "pinned"}
    bad = client.post(f"/matches/{MATCH}/talking-points/x-tp/status", json={"status": "pinned"})
    assert bad.status_code == 404
    wrong = client.post(
        f"/matches/{MATCH}/talking-points/{talking['id']}/status", json={"status": "starred"}
    )
    assert wrong.status_code == 422


def test_recap_before_half_time_is_409_and_health_has_the_studio(played: Played) -> None:
    client = played.client
    body = {"scenario": "balanced", "seed": 3, "speed": 1000, "autostart": False}
    client.post("/matches", json=body)
    response = client.get("/matches/balanced-3/recap?scope=H1")
    assert response.status_code == 409 and "recap_not_ready" in response.json()["detail"]
    assert client.get("/matches/nope-1/recap").status_code == 404
    health = client.get("/healthz").json()
    assert health["studio"]["enabled"] is True and health["studio"]["dropped"] == 0
    assert health["studio"]["lag_ms_p95"] is not None


# --------------------------------------------------------------------------- studio off


def test_studio_off_is_the_day2_app() -> None:
    app = create_app(Settings(studio_enabled=False), clock_factory=SteppingClock)
    with TestClient(app) as client:
        client.post(
            "/matches",
            json={"scenario": "balanced", "seed": 3, "speed": 1000, "autostart": False},
        )
        with client.websocket_connect("/ws/match/balanced-3?mode=casual") as socket:
            hello = socket.receive_json()
            assert set(hello["payload"]) == DAY2_HELLO_KEYS
            client.post("/matches/balanced-3/start")
            stream = read_until(
                socket,
                lambda m: m["type"] == "status" and m["payload"]["session"] == "finished",
            )
        assert not STUDIO_TYPES & {m["type"] for m in stream}
        assert client.get("/matches/balanced-3/commentary").status_code == 404
        assert "studio" not in client.get("/healthz").json()
