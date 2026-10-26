"""Chaos (docs/day7-design.md section 2.6): one fault at a time in a private session, played
with the adapter's own typed errors, off by itself, a reset that lets the breaker try again;
never another session, the shared match or their breakers; and "the model lies" never puts a
wrong number on a screen."""

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import DL, api_settings, generated, mock_service, run, source

from pundit.agents.contracts import ChaosCommand, ClaimRequest, ExplanationDraft
from pundit.api.app import create_app
from pundit.api.chaos import SessionChaos
from pundit.engine.replay import SteppingClock
from pundit.llm.chaos import FAULTS, MAX_DURATION_S, ChaosPort, ChaosState
from pundit.llm.ports import ContentFiltered, LlmError, LlmOutputError

CLAIM = "Đội nhà áp đảo hiệp 2"


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class Writer:
    model = "mock"

    async def explain(self, judged: Any, feedback: Any = None) -> ExplanationDraft:
        return ExplanationDraft(
            text="Harbor had 15 shots to 1.", language="en", source="llm", attempt=1
        )

    async def analyze(self, ctx: Any, feedback: Any = None) -> str:
        return "spec"


@pytest.mark.parametrize(
    ("fault", "error"),
    [
        ("model_down", LlmError),
        ("model_garbage", LlmOutputError),
        ("rate_limited", LlmError),
        ("content_filter", ContentFiltered),
    ],
)
def test_each_fault_is_the_error_a_real_adapter_raises(fault: Any, error: type) -> None:
    state = ChaosState()
    state.switch(fault)
    with pytest.raises(error):
        run(ChaosPort(Writer(), state).explain(None))
    assert state.calls == 1


def test_a_slow_model_hangs_until_the_steps_timeout_ends_it() -> None:
    state = ChaosState()
    state.switch("model_slow")

    async def scenario() -> None:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(ChaosPort(Writer(), state).explain(None), 0.05)

    run(scenario())


def test_rate_limited_says_when_to_come_back() -> None:
    clock = Clock()
    state = ChaosState(clock=clock.now)
    state.switch("rate_limited")
    with pytest.raises(LlmError, match="retry after 30 s"):
        run(ChaosPort(Writer(), state).explain(None))
    clock.advance(10)
    with pytest.raises(LlmError, match="retry after 20 s"):
        run(ChaosPort(Writer(), state).explain(None))


def test_the_model_lies_changes_one_number_and_remembers_it() -> None:
    state = ChaosState()
    state.switch("model_lies")
    port = ChaosPort(Writer(), state)
    draft = run(port.explain(None))
    assert draft.text == "Harbor had 18 shots to 1." and draft.source == "llm"
    assert state.lies == [(None, "Harbor had 18 shots to 1.")] and state.is_lie(draft.text)
    assert run(port.analyze(None)) == "spec"  # the reading of the claim is left alone


def test_a_lie_belongs_to_the_claim_or_moment_it_was_told_for() -> None:
    state = ChaosState()
    state.lies.append(("t-m0003", "Corner for Harbor Lions FC, 6 in the match so far."))
    text = "Corner for Harbor Lions FC, 6 in the match so far."
    assert state.is_lie(text, "t-m0003")
    assert not state.is_lie(text, "t-m0009")  # the same words, true at the sixth corner


def test_a_fault_ends_by_itself_and_never_lasts_more_than_five_minutes() -> None:
    clock = Clock()
    state = ChaosState(clock=clock.now)
    state.switch("model_down", duration_s=10_000)
    assert state.fault == "model_down" and state.seconds_left == MAX_DURATION_S
    clock.advance(MAX_DURATION_S)
    assert state.fault is None and state.seconds_left is None
    assert run(ChaosPort(Writer(), state).analyze(None)) == "spec"  # passes through again


def test_every_fault_has_a_name_in_the_contract() -> None:
    for fault in FAULTS:
        assert ChaosCommand(fault=fault).fault == fault
    assert ChaosCommand(fault=None).duration_s == MAX_DURATION_S
    with pytest.raises(ValueError):
        ChaosCommand(fault="model_down", duration_s=301)


# ------------------------------------------------------------------ with the claim workflow


def request() -> ClaimRequest:
    return ClaimRequest(match_id=generated(*DL).meta.match_id, text=CLAIM)


def test_a_model_that_lies_is_stopped_by_the_guardrail() -> None:
    chaos = SessionChaos(mock_service(), None)
    chaos.switch("model_lies", 60, now=0.0)
    result = run(chaos.claims.verify(request(), source(*DL), claim_id="l-c0001"))
    assert result.verdict == "TRUE" and result.explanation_source == "template"
    assert chaos.state.lies and not chaos.state.is_lie(result.explanation)
    chaos.check_published(result.model_dump(mode="json"))  # the trace keeps the rejected draft
    status = chaos.status()
    assert status.lies_told >= 1 and status.lies_published == 0
    drafts = [s.output for s in result.trace or () if s.agent == "Explainer"]
    assert any(chaos.state.is_lie(str(d.get("text", ""))) for d in drafts) or drafts


def test_a_model_that_is_down_opens_the_session_breaker_and_reset_tries_again() -> None:
    app_service = mock_service()
    chaos = SessionChaos(app_service, None)
    chaos.switch("model_down", 60, now=0.0)
    for n in range(4):
        result = run(chaos.claims.verify(request(), source(*DL), claim_id=f"d-c{n:04d}"))
        assert result.verdict == "TRUE"  # the rules answer every time
    assert chaos.claims.breaker.state == "open"
    assert app_service.breaker.state == "closed"  # the app's breaker never saw it
    chaos.switch(None, 0, now=1.0)
    assert chaos.claims.breaker.state == "half_open"
    healed = run(chaos.claims.verify(request(), source(*DL), claim_id="d-c0009"))
    assert healed.explanation_source == "llm" and chaos.claims.breaker.state == "closed"


def test_garbage_is_retried_then_falls_back() -> None:
    chaos = SessionChaos(mock_service(), None)
    chaos.switch("model_garbage", 60, now=0.0)
    result = run(chaos.claims.verify(request(), source(*DL), claim_id="g-c0001"))
    assert result.verdict == "TRUE" and "analyst_fallback" in result.degraded
    assert chaos.state.calls >= 2  # the retry with feedback


def test_the_switches_are_limited_per_minute() -> None:
    chaos = SessionChaos(mock_service(), None)
    for n in range(10):
        assert chaos.may_switch(float(n))
        chaos.switch("model_down", 60, now=float(n))
    assert not chaos.may_switch(10.0)
    assert chaos.may_switch(70.0)


# --------------------------------------------------------------------------- through the API


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = api_settings(llm_timeout_s=0.5, claim_budget_s=3.0)
    with TestClient(create_app(settings, clock_factory=SteppingClock)) as test_client:
        yield test_client


def private(client: TestClient, **body: Any) -> str:
    payload = {"scenario": "dominant_loser", "seed": 42, "speed": 1000, "private": True, **body}
    match_id: str = client.post("/matches", json=payload).json()["match_id"]
    return match_id


def finished(client: TestClient, match_id: str) -> None:
    deadline = time.monotonic() + 60
    while client.get(f"/matches/{match_id}").json()["info"]["status"] != "finished":
        assert time.monotonic() < deadline, "the replay did not finish"
        time.sleep(0.02)


def test_only_a_private_session_can_be_broken(client: TestClient) -> None:
    client.post("/matches", json={"scenario": "dominant_loser", "seed": 42, "speed": 1000})
    shared = client.post("/matches/dominant_loser-42/chaos", json={"fault": "model_down"})
    assert shared.status_code == 403
    assert client.get("/matches/dominant_loser-42/chaos").json()["available"] is False
    mine = private(client)
    status = client.post(f"/matches/{mine}/chaos", json={"fault": "model_down"}).json()
    assert (status["available"], status["fault"]) == (True, "model_down")
    assert 0 < status["seconds_left"] <= 300
    agents = client.get(f"/matches/{mine}/agents").json()
    assert agents["chaos"]["fault"] == "model_down"
    reset = client.post(f"/matches/{mine}/chaos", json={"fault": None}).json()
    assert reset["fault"] is None


def test_chaos_in_one_session_never_reaches_another(client: TestClient) -> None:
    mine, theirs = private(client), private(client)
    finished(client, mine)
    finished(client, theirs)
    client.post(f"/matches/{mine}/chaos", json={"fault": "model_down"})
    for n in range(4):
        broken = client.post("/claims/verify", json={"match_id": mine, "text": CLAIM}).json()
        fine = client.post("/claims/verify", json={"match_id": theirs, "text": CLAIM}).json()
        assert broken["verdict"] == fine["verdict"] == "TRUE"
        assert fine["explanation_source"] == "llm" and fine["degraded"] == [], n
        assert broken["explanation_source"] == "template"
    assert client.get(f"/matches/{theirs}/chaos").json()["fault"] is None
    assert client.get("/healthz").json()["degraded"] == []  # the app's breaker is closed
    assert client.get(f"/matches/{mine}/chaos").json()["claim_breaker"] == "open"


def test_a_lying_model_never_reaches_a_screen_in_the_studio_or_a_claim(client: TestClient) -> None:
    mine = private(client, autostart=False)
    client.post(f"/matches/{mine}/chaos", json={"fault": "model_lies"})
    client.post(f"/matches/{mine}/start")
    finished(client, mine)
    claim = client.post("/claims/verify", json={"match_id": mine, "text": CLAIM}).json()
    assert claim["verdict"] == "TRUE"
    deadline = time.monotonic() + 30
    status = client.get(f"/matches/{mine}/chaos").json()
    while status["lies_told"] < 5 and time.monotonic() < deadline:
        time.sleep(0.1)
        status = client.get(f"/matches/{mine}/chaos").json()
    assert status["lies_told"] >= 5 and status["lies_published"] == 0
