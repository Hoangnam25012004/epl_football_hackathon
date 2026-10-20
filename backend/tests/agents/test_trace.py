"""TraceRecorder (design section 12): deterministic ids, relative times, redaction, size."""

import pytest

from pundit.agents.trace import MAX_SUMMARY_BYTES, TraceRecorder, redact, summarize


class Tick:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_ids_parents_and_times() -> None:
    clock = Tick()
    trace = TraceRecorder("m-1-c0001", clock=clock)
    agent = trace.start("EvidenceCollector", "agent", "collect", {"n": 2})
    clock.t += 0.010
    tool = trace.start("EvidenceCollector", "tool", "get_stat", {"metric": "shots"}, parent=agent)
    clock.t += 0.005
    tool.finish({"value": 15})
    agent.finish({"tool_calls": 1})
    spans = trace.spans
    assert [s.span_id for s in spans] == ["m-1-c0001-s01", "m-1-c0001-s02"]
    assert spans[1].parent_id == "m-1-c0001-s01" and spans[0].parent_id is None
    assert spans[0].start_ms == 0 and spans[1].start_ms == pytest.approx(10)
    assert spans[1].duration_ms == pytest.approx(5) and spans[0].duration_ms == pytest.approx(15)
    assert spans[0].trace_id == "m-1-c0001"


def test_unfinished_spans_are_left_out_and_a_span_finishes_once() -> None:
    trace = TraceRecorder("c", clock=Tick())
    open_span = trace.start("Judge", "agent", "judge")
    done = trace.event("Publisher", "agent", "publish", output={"verdict": "TRUE"})
    assert trace.spans == (done,)
    open_span.finish()
    with pytest.raises(RuntimeError):
        open_span.finish()


def test_secrets_are_removed() -> None:
    cleaned = redact(
        {"model": "x", "api_key": "sk-1", "Authorization": "Bearer t", "nested": [{"token": "t"}]}
    )
    assert cleaned == {"model": "x", "nested": [{}]}
    trace = TraceRecorder("c", clock=Tick())
    span = trace.event(
        "ClaimAnalyst", "llm", "analyze", input={"github_token": "ghp_secret", "n": 1}
    )
    assert "ghp_secret" not in span.model_dump_json()


def test_large_summaries_are_truncated() -> None:
    big = {"text": "x" * 10_000}
    summary = summarize(big)
    assert summary["truncated"] is True
    assert len(str(summary["preview"]).encode()) < MAX_SUMMARY_BYTES
    assert summarize({"a": 1}) == {"a": 1}
