"""The progressive claim through the API (docs/day7-design.md section 2.4): POST answers rev.1
from the rules at once; the WebSocket carries the same claim as it moves on (queued, running,
rev.2); GET returns the newest revision. CLAIM_PROGRESSIVE=false keeps the one-pass answer."""

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import api_settings

from pundit.api.app import create_app
from pundit.engine.replay import SteppingClock

MATCH = "dominant_loser-42"
BODY = {"match_id": MATCH, "text": "Đội nhà áp đảo hiệp 2"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = api_settings(
        claim_progressive=True, mock_llm_latency_ms=300, llm_timeout_s=5.0, claim_budget_s=10.0
    )
    with TestClient(create_app(settings, clock_factory=SteppingClock)) as test_client:
        start(test_client)
        yield test_client


def start(client: TestClient) -> None:
    """The whole match, replayed: the claims are about the second half."""
    client.post("/matches", json={"scenario": "dominant_loser", "seed": 42, "speed": 1000})
    deadline = time.monotonic() + 30
    while client.get(f"/matches/{MATCH}").json()["info"]["status"] != "finished":
        assert time.monotonic() < deadline, "the replay did not finish"
        time.sleep(0.01)


def newest(client: TestClient, claim_id: str, timeout_s: float = 20.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        claim: dict[str, Any] = client.get(f"/claims/{claim_id}").json()
        if claim["revision"] == 2:
            return claim
        time.sleep(0.05)
    raise AssertionError("no rev.2")


def test_post_answers_rev1_from_the_rules_without_waiting_for_the_model(
    client: TestClient,
) -> None:
    began = time.monotonic()
    rev1 = client.post("/claims/verify", json=BODY).json()
    took = time.monotonic() - began
    assert (rev1["revision"], rev1["model_pass"]) == (1, "queued")
    assert (rev1["verdict"], rev1["analyst_source"], rev1["explanation_source"]) == (
        "TRUE",
        "rule",
        "template",
    )
    assert took < 0.3 * 2  # well under two model calls of 300 ms each
    rev2 = newest(client, rev1["claim_id"])
    assert (rev2["model_pass"], rev2["rev1_verdict"], rev2["verdict"]) == ("done", "TRUE", "TRUE")
    assert rev2["explanation_source"] == "llm" and rev2["trace"]  # GET keeps the trace


def test_the_websocket_carries_queued_running_and_rev2_under_one_id(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/match/{MATCH}") as ws:
        ws.receive_json()  # hello
        claim_id = client.post("/claims/verify", json=BODY).json()["claim_id"]
        states = []
        while not states or states[-1] != (2, "done"):
            message = ws.receive_json()
            if message["type"] != "claim":
                continue
            payload = message["payload"]
            assert payload["claim_id"] == claim_id and payload["trace"] is None
            states.append((payload["revision"], payload["model_pass"]))
    assert states == [(1, "queued"), (1, "running"), (2, "done")]


def test_a_late_client_gets_the_newest_revision_in_hello(client: TestClient) -> None:
    claim_id = client.post("/claims/verify", json=BODY).json()["claim_id"]
    newest(client, claim_id)
    with client.websocket_connect(f"/ws/match/{MATCH}") as ws:
        hello = ws.receive_json()
    claims = {c["claim_id"]: c for c in hello["payload"]["claims"]}
    assert claims[claim_id]["revision"] == 2


def test_the_health_page_and_the_log_name_the_progressive_settings(client: TestClient) -> None:
    describe = client.app.state.settings.describe()  # type: ignore[attr-defined]
    assert describe["claim_progressive"] == {
        "enabled": True,
        "model_pass_budget_s": 60.0,
        "model_queue_max": 4,
    }


def test_one_pass_without_the_setting() -> None:
    with TestClient(create_app(api_settings(), clock_factory=SteppingClock)) as client:
        start(client)
        result = client.post("/claims/verify", json=BODY).json()
    assert (result["revision"], result["model_pass"], result["explanation_source"]) == (
        1,
        None,
        "llm",
    )
