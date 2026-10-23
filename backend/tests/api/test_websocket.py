"""WebSocket protocol (design section 11.3): hello, ordering, two clients, late clients."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import DAY2_HELLO_KEYS, STUDIO_HELLO_KEYS, STUDIO_ON, api_settings
from starlette.websockets import WebSocketDisconnect

from pundit.api.app import create_app
from pundit.engine.replay import SteppingClock

MATCH = "dominant_loser-42"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(api_settings(), clock_factory=SteppingClock)) as test_client:
        yield test_client


def read_until_finished(ws: Any) -> list[dict[str, Any]]:
    messages = []
    while True:
        message = ws.receive_json()
        messages.append(message)
        if message["type"] == "status" and message["payload"]["session"] == "finished":
            return messages


def test_two_clients_see_the_same_stream(client: TestClient) -> None:
    client.post(
        "/matches",
        json={"scenario": "dominant_loser", "seed": 42, "speed": 1000, "autostart": False},
    )
    with (
        client.websocket_connect(f"/ws/match/{MATCH}") as first,
        client.websocket_connect(f"/ws/match/{MATCH}") as second,
    ):
        hello_a, hello_b = first.receive_json(), second.receive_json()
        assert hello_a["type"] == hello_b["type"] == "hello" and hello_a["n"] == hello_b["n"]
        assert hello_a["v"] == 1 and hello_a["match_id"] == MATCH
        # Day 3 adds the studio keys (only when the studio is on)
        assert set(hello_a["payload"]) == DAY2_HELLO_KEYS | (
            STUDIO_HELLO_KEYS if STUDIO_ON else set()
        )
        client.post(f"/matches/{MATCH}/start")
        stream_a, stream_b = read_until_finished(first), read_until_finished(second)
    assert stream_a == stream_b
    assert [m["n"] for m in stream_a] == list(
        range(hello_a["n"] + 1, hello_a["n"] + 1 + len(stream_a))
    )
    events = [m["payload"] for m in stream_a if m["type"] == "event"]
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    assert all(e["type"] != "player_physical" for e in events)
    kinds = {m["type"] for m in stream_a}
    assert {"status", "event", "snapshot", "moment"} <= kinds
    goals = [m for m in stream_a if m["type"] == "moment" and m["payload"]["kind"] == "goal"]
    assert [g["payload"]["display_minute"] for g in goals] == ["27'", "63'", "78'"]


def test_claims_are_broadcast_without_trace(client: TestClient) -> None:
    client.post(
        "/matches",
        json={"scenario": "dominant_loser", "seed": 42, "speed": 1000, "autostart": False},
    )
    with client.websocket_connect(f"/ws/match/{MATCH}") as ws:
        ws.receive_json()
        client.post(
            "/claims/verify",
            json={"match_id": MATCH, "text": "Trọng tài thiên vị Harbor", "include_trace": True},
        )
        message = ws.receive_json()
    assert message["type"] == "claim" and message["payload"]["trace"] is None
    assert message["payload"]["verdict"] == "INSUFFICIENT_DATA"


def test_late_client_gets_the_state_in_hello(client: TestClient) -> None:
    client.post(
        "/matches",
        json={"scenario": "comeback_var", "seed": 7, "speed": 1000, "autostart": False},
    )
    with client.websocket_connect("/ws/match/comeback_var-7") as ws:
        ws.receive_json()
        # started after the hello: an autostarted replay could finish before the client
        # subscribes, and the "finished" status would then never reach it (only the hello)
        client.post("/matches/comeback_var-7/start")
        read_until_finished(ws)
    client.post(
        "/claims/verify", json={"match_id": "comeback_var-7", "text": "Ironvale thắng trận"}
    )
    with client.websocket_connect("/ws/match/comeback_var-7") as late:
        hello = late.receive_json()
    payload = hello["payload"]
    assert payload["match"]["status"] == "finished"
    assert payload["snapshot"]["score"] == {"ironvale_united": 3, "saltmarsh_rovers": 2}
    assert any(m["kind"] == "goal_overturned" for m in payload["moments"])
    assert payload["claims"][0]["verdict"] == "TRUE"


@pytest.mark.parametrize("match_id", ["nope-1", "dominant_loser-42~e2eagain"])
def test_unknown_match_is_closed_with_4404(client: TestClient, match_id: str) -> None:
    """Accepted, then closed with 4404: the code a browser sees (Day 6, a private link the
    server no longer has; a close before the handshake would reach it as 1006)."""
    with (
        client.websocket_connect(f"/ws/match/{match_id}") as ws,
        pytest.raises(WebSocketDisconnect) as caught,
    ):
        ws.receive_json()
    assert caught.value.code == 4404
