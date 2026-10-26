"""Agent Ops (docs/day7-design.md section 2.5): the agents of a session as events (start, end,
handoff, shared state, breaker, guardrail codes), per-agent statistics, a light stream in
batches on its own WebSocket, and a listener that can never break a run."""

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import api_settings, run
from starlette.websockets import WebSocketDisconnect

from pundit.agents.contracts import AgentBreaker
from pundit.agents.trace import TraceRecorder
from pundit.api.agent_ops import FLUSH_S, MAX_BATCH, AgentOps
from pundit.api.app import create_app
from pundit.engine.replay import SteppingClock

MATCH = "dominant_loser-42"
CLAIM_ORDER = [
    "ClaimIntake", "ClaimAnalyst", "RuleAnalyst", "EvidenceCollector", "Judge", "Explainer",
    "Guardrail", "Publisher",
]  # fmt: skip


def ops(breaker: list[AgentBreaker] | None = None) -> AgentOps:
    state = breaker or ["closed"]
    return AgentOps(
        "m-1", display_of=lambda seq: f"{seq // 10}'", breakers={"claim": lambda: state[0]}
    )


def test_spans_become_events_with_handoff_shared_state_and_source() -> None:
    agent_ops = ops()
    trace = TraceRecorder("m-1-c0001", listener=agent_ops.listener("claim", "m-1-c0001", 630))
    intake = trace.start("ClaimIntake", "agent", "intake")
    intake.finish({"ok": True})
    analyst = trace.start("ClaimAnalyst", "agent", "analyze")
    trace.event("ClaimAnalyst", "llm", "analyze_claim", model="mock")
    analyst.finish()
    collect = trace.start("EvidenceCollector", "agent", "collect")
    trace.event("EvidenceCollector", "tool", "get_stat")  # counted, not streamed
    collect.finish()
    trace.event(
        "Guardrail", "guardrail", "check", status="rejected",
        output={"violations": [{"code": "WRONG_COMPARISON", "detail": "x"}]},
    )  # fmt: skip
    events = agent_ops.snapshot().recent
    shown = [(e.agent, e.kind, e.phase) for e in events]
    assert ("EvidenceCollector", "tool", "start") not in shown
    assert shown[:2] == [("ClaimIntake", "agent", "start"), ("ClaimIntake", "agent", "end")]
    end = {(e.agent, e.kind): e for e in events if e.phase == "end"}
    assert end[("ClaimIntake", "agent")].handoff == "ClaimContext"
    assert end[("ClaimIntake", "agent")].as_of_display == "63'"
    assert end[("ClaimAnalyst", "llm")].source == "llm_live"
    assert end[("ClaimAnalyst", "agent")].source == "llm_live"  # its model call answered
    assert end[("EvidenceCollector", "agent")].source == "rules"
    assert end[("Guardrail", "guardrail")].guardrail == ("WRONG_COMPARISON",)
    assert [e.seq for e in events] == sorted(e.seq for e in events)
    stats = {s.agent: s for s in agent_ops.snapshot().agents}
    assert stats["Guardrail"].rejected == 1 and stats["EvidenceCollector"].sources["tools"] == 1
    intake = stats["ClaimIntake"]
    assert intake.last_output == {"ok": True} and intake.p95_ms is not None


def test_a_writer_without_a_model_answer_is_a_template() -> None:
    agent_ops = ops()
    trace = TraceRecorder("t-1", listener=agent_ops.listener("studio", "t-1", 5))
    trace.event("TalkingPointWriter", "llm", "write", status="error")
    trace.event("TalkingPointWriter", "agent", "talking_point")
    last = agent_ops.snapshot().recent[-1]
    assert (last.agent, last.source, last.handoff) == (
        "TalkingPointWriter",
        "template",
        "BranchResult",
    )


def test_a_breaker_that_opens_is_an_event_of_its_own() -> None:
    state: list[AgentBreaker] = ["closed"]
    agent_ops = ops(state)
    listener = agent_ops.listener("claim", "c1", 1)
    trace = TraceRecorder("c1", listener=listener)
    trace.event("ClaimIntake", "agent", "intake")
    state[0] = "open"
    trace.event("ClaimIntake", "agent", "intake")
    agents = [(e.agent, e.breaker) for e in agent_ops.snapshot().recent]
    assert ("CircuitBreaker", "open") in agents
    assert agents.count(("CircuitBreaker", "open")) == 1  # once per change
    assert agent_ops.snapshot().breakers == {"claim": "open"}


def test_a_listener_that_raises_never_breaks_the_run() -> None:
    class Broken:
        def started(self, handle: Any) -> None:
            raise RuntimeError("boom")

        def finished(self, span: Any) -> None:
            raise RuntimeError("boom")

    trace = TraceRecorder("x", listener=Broken())
    trace.start("Judge", "agent", "judge").finish({"verdict": "TRUE"})
    assert [s.agent for s in trace.spans] == ["Judge"]


def test_events_go_out_in_batches_never_more_than_ten_a_second() -> None:
    async def scenario() -> None:
        agent_ops = ops()
        queue = agent_ops.subscribe()
        trace = TraceRecorder("b", listener=agent_ops.listener("claim", "b", 1))
        for _ in range(60):  # 120 events at once
            trace.event("Judge", "agent", "judge")
        await asyncio.sleep(FLUSH_S * 2.5)
        assert queue.qsize() == 1
        batch = queue.get_nowait().payload
        assert len(batch.events) == MAX_BATCH and batch.dropped == 120 - MAX_BATCH  # type: ignore[union-attr]
        assert batch.events[-1].seq == 120  # type: ignore[union-attr]
        agent_ops.unsubscribe(queue)
        trace.event("Judge", "agent", "judge")  # nobody listens: recorded, not sent
        assert agent_ops.snapshot().recent[-1].seq == 122
        await agent_ops.aclose()

    run(scenario())


def test_a_studio_backlog_never_pushes_a_claim_out_of_a_batch() -> None:
    async def scenario() -> None:
        agent_ops = ops()
        queue = agent_ops.subscribe()
        studio = TraceRecorder("s", listener=agent_ops.listener("studio", "s", 1))
        claim = TraceRecorder("c", listener=agent_ops.listener("claim", "c", 1))
        for n in range(100):
            studio.event("FactPackBuilder", "agent", "build_pack")
            if n == 50:
                claim.event("Publisher", "agent", "publish")
        await asyncio.sleep(FLUSH_S * 2.5)
        batch = queue.get_nowait().payload
        events = batch.events  # type: ignore[union-attr]
        assert len(events) == MAX_BATCH and any(e.run_id == "c" for e in events)
        assert [e.seq for e in events] == sorted(e.seq for e in events)
        await agent_ops.aclose()

    run(scenario())


def test_a_narrator_cue_is_shown_once_per_line() -> None:
    agent_ops = ops()
    agent_ops.cued("m-1-m0001-c", as_of_seq=40, voice="vi-VN-HoaiMyNeural")
    agent_ops.cued("m-1-m0001-c", as_of_seq=40, voice="vi-VN-NamMinhNeural")  # another viewer
    events = [e for e in agent_ops.snapshot().recent if e.workflow == "narrator"]
    assert [(e.agent, e.handoff, e.as_of_display) for e in events] == [
        ("Narrator", "SpeechCue", "4'")
    ]


# --------------------------------------------------------------------------- through the API


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(api_settings(), clock_factory=SteppingClock)) as test_client:
        test_client.post("/matches", json={"scenario": "dominant_loser", "seed": 42, "speed": 1000})
        deadline = time.monotonic() + 30
        while test_client.get(f"/matches/{MATCH}").json()["info"]["status"] != "finished":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        yield test_client


def test_a_claim_lights_the_claim_workflow_in_order(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/agents/{MATCH}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["payload"]["synthetic"] is True
        claim = client.post(
            "/claims/verify", json={"match_id": MATCH, "text": "Đội nhà áp đảo hiệp 2"}
        ).json()
        ended: list[str] = []
        for _ in range(2000):  # bounded: a missing event fails the test, never hangs it
            if "Publisher" in ended:
                break
            message = ws.receive_json()
            assert message["type"] == "events"
            for event in message["payload"]["events"]:
                if event["run_id"] != claim["claim_id"] or event["phase"] != "end":
                    continue
                assert event["as_of_seq"] == claim["as_of_seq"]
                shown = event["kind"] == "agent" or event["agent"] in ("Explainer", "Guardrail")
                if shown and event["agent"] not in ended:
                    ended.append(event["agent"])
    assert ended == CLAIM_ORDER
    stats = {s["agent"]: s for s in client.get(f"/matches/{MATCH}/agents").json()["agents"]}
    assert stats["Publisher"]["calls"] >= 1 and stats["Explainer"]["sources"]["llm_live"] >= 1


def test_the_studio_lane_has_its_agents(client: TestClient) -> None:
    snapshot = client.get(f"/matches/{MATCH}/agents").json()
    studio = {s["agent"] for s in snapshot["agents"] if s["workflow"] == "studio"}
    assert {"StudioIntake", "FactPackBuilder", "StudioPublisher"} <= studio
    assert set(snapshot["breakers"]) == {"claim", "studio"}


def test_an_unknown_match_closes_with_4404(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        client.websocket_connect("/ws/agents/nope-1") as ws,
    ):
        ws.receive_json()
    assert closed.value.code == 4404
    assert client.get("/matches/nope-1/agents").status_code == 404


def test_progressive_runs_are_named_rules_and_model() -> None:
    settings = api_settings(claim_progressive=True, llm_timeout_s=5.0)
    with TestClient(create_app(settings, clock_factory=SteppingClock)) as client:
        client.post("/matches", json={"scenario": "dominant_loser", "seed": 42, "speed": 1000})
        claim = client.post("/claims/verify", json={"match_id": MATCH, "text": "HAR áp đảo"}).json()
        deadline = time.monotonic() + 20
        runs: set[str] = set()
        while time.monotonic() < deadline and len(runs) < 2:
            snapshot = client.get(f"/matches/{MATCH}/agents").json()
            runs = {e["run_id"] for e in snapshot["recent"] if e["workflow"] == "claim"}
            time.sleep(0.05)
    assert runs >= {f"{claim['claim_id']}#rules", f"{claim['claim_id']}#model"}
