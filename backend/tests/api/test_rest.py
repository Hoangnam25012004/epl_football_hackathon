"""REST API (design section 11.2) through FastAPI's TestClient."""

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import STUDIO_ON, api_settings, generated

from pundit.agents.moments import detect_all
from pundit.api.app import create_app
from pundit.engine.replay import SteppingClock

MATCH = "dominant_loser-42"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(api_settings(), clock_factory=SteppingClock)) as test_client:
        yield test_client


def wait_finished(client: TestClient, match_id: str = MATCH) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        info: dict[str, Any] = client.get(f"/matches/{match_id}").json()["info"]
        if info["status"] in ("finished", "error"):
            return info
        time.sleep(0.01)
    raise AssertionError("replay did not finish")


def create(client: TestClient, **body: Any) -> dict[str, Any]:
    payload = {"scenario": "dominant_loser", "seed": 42, "speed": 1000, **body}
    response = client.post("/matches", json=payload)
    assert response.status_code == 200, response.text
    info: dict[str, Any] = response.json()
    return info


def test_health(client: TestClient) -> None:
    body = client.get("/healthz").json()
    expected: dict[str, Any] = {
        "status": "ok",
        "llm_provider": "mock",
        "model": "mock",
        "degraded": [],
        "sessions": 0,
        "synthetic": True,
    }
    if STUDIO_ON:  # Day 3: the studio block; absent with STUDIO_ENABLED=false
        expected["studio"] = {
            "enabled": True,
            "queued": 0,
            "dropped": 0,
            "lag_ms_p95": None,
            "degraded": [],
        }
    assert body == expected


def test_match_lifecycle_and_moments(client: TestClient) -> None:
    info = create(client, autostart=False)
    assert info["status"] == "created" and info["events_applied"] == 0
    assert info["synthetic"] and "Synthetic data" in info["disclaimer"]
    assert create(client, autostart=False)["status"] == "created"  # idempotent
    assert client.post(f"/matches/{MATCH}/start").json()["status"] in ("running", "finished")
    final = wait_finished(client)
    match = generated("dominant_loser", 42)
    assert final["status"] == "finished" and final["events_applied"] == len(match.events)
    view = client.get(f"/matches/{MATCH}").json()
    assert view["snapshot"]["score"] == {"harbor_lions": 1, "kestrel_athletic": 2}
    moments = client.get(f"/matches/{MATCH}/moments").json()
    assert [m["id"] for m in moments] == [m.id for m in detect_all(match.meta, match.events)]
    later = client.get(f"/matches/{MATCH}/moments", params={"since": 1500}).json()
    assert later and all(m["seq"] > 1500 for m in later)


def test_restart_creates_a_new_session(client: TestClient) -> None:
    create(client)
    wait_finished(client)
    info = create(client, restart=True, autostart=False)
    assert info["status"] == "created" and info["events_applied"] == 0


def test_verify_claim(client: TestClient) -> None:
    create(client)
    wait_finished(client)
    body = {"match_id": MATCH, "text": "Đội nhà áp đảo hiệp 2"}
    result = client.post("/claims/verify", json=body).json()
    assert result["verdict"] == "TRUE" and result["trace"] is None
    assert result["claim_id"] == f"{MATCH}-c0001" and result["evidence_event_ids"]
    with_trace = client.post("/claims/verify", json={**body, "include_trace": True}).json()
    assert with_trace["trace"] and with_trace["claim_id"] == f"{MATCH}-c0002"
    stored = client.get(f"/claims/{MATCH}-c0001").json()
    assert stored["trace"] and stored["verdict"] == "TRUE"


def test_verify_while_running_is_pinned(client: TestClient) -> None:
    create(client, speed=50)
    result = client.post(
        "/claims/verify", json={"match_id": MATCH, "text": "HAR có nhiều đường chuyền hơn KES"}
    ).json()
    info = client.get(f"/matches/{MATCH}").json()["info"]
    assert result["as_of_seq"] < info["events_applied"]
    client.post(f"/matches/{MATCH}/stop")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/matches/nope-1", None),
        ("get", "/matches/nope-1/moments", None),
        ("post", "/matches/nope-1/start", None),
        ("post", "/matches/nope-1/stop", None),
        ("post", "/claims/verify", {"match_id": "nope-1", "text": "HAR áp đảo"}),
        ("get", "/claims/nope-1-c0001", None),
    ],
)
def test_not_found(client: TestClient, method: str, path: str, body: dict[str, Any] | None) -> None:
    response = client.request(method.upper(), path, json=body)
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/matches", {"scenario": "real_madrid", "seed": 1}),
        ("/matches", {"scenario": "balanced", "seed": -1}),
        ("/matches", {"scenario": "balanced", "speed": 0}),
        ("/matches", {"scenario": "balanced", "colour": "red"}),
        ("/claims/verify", {"match_id": MATCH, "text": "ab"}),
        ("/claims/verify", {"match_id": "Not A Match", "text": "HAR áp đảo"}),
        ("/claims/verify", {"match_id": MATCH, "text": "x" * 301}),
        ("/claims/verify", {"match_id": MATCH, "text": "HAR áp đảo", "language": "fr"}),
        ("/claims/verify", {"match_id": MATCH, "text": "HAR áp đảo", "as_of_seq": -1}),
    ],
)
def test_invalid_input(client: TestClient, path: str, body: dict[str, Any]) -> None:
    assert client.post(path, json=body).status_code == 422


def test_llm_failures_are_never_5xx() -> None:
    settings = api_settings(mock_llm_faults="analyst:error,explainer:error", llm_timeout_s=0.5)
    with TestClient(create_app(settings, clock_factory=SteppingClock)) as client:
        create(client)
        wait_finished(client)
        response = client.post(
            "/claims/verify", json={"match_id": MATCH, "text": "Đội nhà áp đảo hiệp 2"}
        )
        assert response.status_code == 200
        assert response.json()["degraded"] == ["analyst_fallback", "explainer_fallback"]


def test_shutdown_leaves_no_task() -> None:
    app = create_app(api_settings(), clock_factory=SteppingClock)
    with TestClient(app) as client:
        create(client, speed=1)  # slow replay, still running at shutdown
    sessions = app.state.manager.sessions
    assert sessions and all(s._task is None or s._task.done() for s in sessions.values())
    # Day 3: the studio and recap workers are cancelled too
    assert not any(s.director is not None and s.director.active() for s in sessions.values())


def test_cors_allows_the_dashboard() -> None:
    with TestClient(create_app(api_settings(), clock_factory=SteppingClock)) as client:
        response = client.options(
            "/healthz",
            headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
        )
        assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
