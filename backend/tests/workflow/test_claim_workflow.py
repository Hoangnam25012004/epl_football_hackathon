"""The claim workflow on Microsoft Agent Framework (design sections 9 and 13.4-13.5)."""

import asyncio
import itertools
from typing import Any

import pytest
from helpers import DL, generated, mock_service, run, source, verify

from pundit.agents.analyst_rules import analyze_claim
from pundit.agents.contracts import ClaimContext, ClaimRequest, ClaimResult, ClaimSpec
from pundit.engine.replay import FakeClock
from pundit.llm.faults import FaultPlan
from pundit.llm.mock import MockClaimAnalyst, MockExplainer
from pundit.llm.settings import Settings
from pundit.orchestration import service as service_module
from pundit.orchestration.service import ClaimService
from pundit.stats import MetricParams, compute

CLAIM = "Đội nhà áp đảo hiệp 2"
TIMING_KEYS = {"timings_ms"}


def stable(result: ClaimResult) -> dict[str, Any]:
    """The result without measured times (C5 compares everything else)."""
    data = result.model_dump(mode="json", exclude=TIMING_KEYS)
    for span in data["trace"] or []:
        span.pop("start_ms")
        span.pop("duration_ms")
    return data


@pytest.fixture(scope="module", autouse=True)
def warm_up() -> None:
    """Compile regexes and build caches so the small test timeouts measure only the faults."""
    verify(CLAIM)


def test_happy_path_trace() -> None:
    result = verify(CLAIM)
    assert result.verdict == "TRUE" and result.degraded == () and result.guardrail.passed
    assert (result.analyst_source, result.explanation_source, result.model) == (
        "llm",
        "llm",
        "mock",
    )
    agents = [(s.agent, s.kind) for s in result.trace or ()]
    assert agents[:4] == [
        ("ClaimIntake", "agent"),
        ("ClaimAnalyst", "agent"),
        ("ClaimAnalyst", "llm"),
        ("RuleAnalyst", "agent"),
    ]
    assert agents[-4:] == [
        ("Judge", "agent"),
        ("Explainer", "llm"),
        ("Guardrail", "guardrail"),
        ("Publisher", "agent"),
    ]
    spans = result.trace or ()
    collector = next(s for s in spans if s.agent == "EvidenceCollector" and s.kind == "agent")
    tools = [s for s in spans if s.kind == "tool"]
    assert len(tools) == 9 and all(t.parent_id == collector.span_id for t in tools)
    assert [s.span_id for s in spans] == [f"test-c0001-s{k:02d}" for k in range(1, len(spans) + 1)]
    assert set(result.timings_ms) >= {
        "intake",
        "analyst",
        "collector",
        "judge",
        "guardrail",
        "total",
    }


def test_asyncio_runner_gives_the_same_result() -> None:
    via_maf = verify(CLAIM)
    via_loop = verify(CLAIM, service=ClaimService(Settings(), use_workflow=False))
    assert stable(via_maf) == stable(via_loop)


def test_determinism_c5() -> None:
    first, second = verify(CLAIM), verify(CLAIM)
    assert stable(first) == stable(second)


def test_as_of_out_of_range() -> None:
    request = ClaimRequest(match_id="dominant_loser-42", text=CLAIM, as_of_seq=99_999)
    result = run(mock_service().verify(request, source(), claim_id="c"))
    assert (result.verdict, result.reason_code) == ("INSUFFICIENT_DATA", "as_of_out_of_range")
    assert (
        result.explanation and result.explanation_source == "template" and result.guardrail.passed
    )
    assert result.as_of_seq == source().latest_seq()


def test_insufficient_claims_skip_the_llm_explainer() -> None:
    result = verify("HAR áp đảo hiệp 2", at="30'")
    assert result.reason_code == "future_window" and result.explanation_source == "template"
    assert result.degraded == ()
    assert not [s for s in result.trace or () if s.agent == "Explainer"]


ANALYST_FAULTS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "ok": ("", (), "llm"),
    "timeout": ("analyst:timeout", ("analyst_fallback",), "rule"),
    "error": ("analyst:error", ("analyst_fallback",), "rule"),
    "invalid_json_x1": ("analyst:invalid_json:1", (), "llm"),
    "invalid_json_x2": ("analyst:invalid_json:2", ("analyst_fallback",), "rule"),
    "wrong_entity": ("analyst:wrong_entity", ("analyst_disagreement",), "rule"),
}
EXPLAINER_FAULTS: dict[str, tuple[str, tuple[str, ...], str, int]] = {
    "ok": ("", (), "llm", 0),
    "timeout": ("explainer:timeout", ("explainer_fallback",), "template", 0),
    "error": ("explainer:error", ("explainer_fallback",), "template", 0),
    "hallucinate_x1": ("explainer:hallucinate:1", (), "llm", 1),
    "hallucinate_x2": ("explainer:hallucinate:2", ("explainer_fallback",), "template", 2),
    "unsafe": ("explainer:unsafe", (), "llm", 1),
}
MATRIX = list(itertools.product(ANALYST_FAULTS, EXPLAINER_FAULTS))


@pytest.mark.parametrize(("analyst", "explainer"), MATRIX, ids=[f"{a}+{e}" for a, e in MATRIX])
def test_fault_matrix(analyst: str, explainer: str) -> None:
    a_plan, a_flags, a_source = ANALYST_FAULTS[analyst]
    e_plan, e_flags, e_source, rejected = EXPLAINER_FAULTS[explainer]
    plan = ",".join(p for p in (a_plan, e_plan) if p)
    service = mock_service(mock_llm_faults=plan, llm_timeout_s=0.4, claim_budget_s=4.0)
    result = verify(CLAIM, service=service)
    assert result.verdict == "TRUE"  # the verdict never depends on an LLM failure
    assert result.guardrail.passed and result.explanation
    assert result.degraded == a_flags + e_flags
    assert (result.analyst_source, result.explanation_source) == (a_source, e_source)
    spans = result.trace or ()
    guard = [s.status for s in spans if s.agent == "Guardrail"]
    assert guard.count("rejected") == rejected and guard[-1] == "ok"
    if analyst == "invalid_json_x1":
        assert [s.status for s in spans if s.kind == "llm" and s.agent == "ClaimAnalyst"] == [
            "retry",
            "ok",
        ]
    if a_source == "rule":
        assert next(s for s in spans if s.agent == "RuleAnalyst").status == "fallback"
    if e_source == "template":
        assert any(s.agent == "TemplateExplainer" and s.status == "fallback" for s in spans)
    assert result.timings_ms["total"] < 4000


def test_circuit_breaker_across_claims() -> None:
    plans = iter(["analyst:error,explainer:error", "analyst:error", "", "", ""])
    clock = FakeClock()

    def ports() -> tuple[MockClaimAnalyst, MockExplainer]:
        counter = FaultPlan.parse(next(plans)).counter()
        return MockClaimAnalyst(counter), MockExplainer(counter)

    service = ClaimService(Settings(), ports=ports, clock=clock.now)
    first = verify(CLAIM, service=service)
    assert first.degraded == ("analyst_fallback", "explainer_fallback")
    assert service.breaker.state == "closed"
    second = verify(CLAIM, service=service)  # third consecutive failure opens the circuit
    assert second.degraded == ("analyst_fallback", "circuit_open", "explainer_fallback")
    assert service.breaker.state == "open" and service.degraded_now() == ["circuit_open"]
    third = verify(CLAIM, service=service)  # no LLM call at all while open
    assert third.degraded == ("circuit_open", "explainer_fallback")
    assert not [s for s in third.trace or () if s.kind == "llm" and s.status == "ok"]
    clock.t += 60
    fourth = verify(CLAIM, service=service)  # half-open trial succeeds and closes it
    assert fourth.degraded == () and service.breaker.state == "closed"
    assert {first.verdict, second.verdict, third.verdict, fourth.verdict} == {"TRUE"}


def test_claim_budget_caps_llm_time() -> None:
    service = mock_service(claim_budget_s=0.15, llm_timeout_s=1.0, mock_llm_latency_ms=400)
    result = verify(CLAIM, service=service)
    assert result.verdict == "TRUE"
    assert result.degraded == ("analyst_fallback", "explainer_fallback")
    assert result.timings_ms["total"] < 1000


def test_orchestrator_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: object) -> object:
        raise RuntimeError("workflow engine down")

    monkeypatch.setattr(service_module, "build_claim_workflow", broken)
    result = verify(CLAIM)
    assert result.verdict == "TRUE"
    assert result.degraded == ("orchestrator_fallback", "explainer_fallback")
    assert (result.analyst_source, result.explanation_source) == ("rule", "template")
    assert result.guardrail.passed


def test_concurrent_claims_are_pinned_to_their_own_as_of() -> None:
    match = generated(*DL)
    service = mock_service()
    seqs = list(range(300, 2065, 90))[:20]

    async def all_claims() -> list[ClaimResult]:
        tasks = [
            service.verify(
                ClaimRequest(
                    match_id=match.meta.match_id, text="HAR có nhiều cú sút hơn KES", as_of_seq=seq
                ),
                source(),
                claim_id=f"c-{index:04d}",
            )
            for index, seq in enumerate(seqs)
        ]
        return await asyncio.gather(*tasks)

    results = run(all_claims())
    assert len(results) == 20
    for seq, result in zip(seqs, results, strict=True):
        assert result.as_of_seq == seq
        view = source().view_at(seq)
        for fact in result.assertions[0].facts:
            if fact.derived:
                continue
            assert fact.metric is not None
            expected = compute(
                view,
                fact.metric,
                MetricParams(team=fact.team, window=result.assertions[0].assertion.window),
            )
            assert fact.value == expected.value


class DominanceReader:
    """An analyst that reads every claim as Kestrel dominating the match: what a small model did
    with claims the rules could not parse (Day 6, the Ollama eval)."""

    model = "stub"

    async def analyze(self, ctx: ClaimContext, feedback: list[str] | None = None) -> ClaimSpec:
        del feedback
        reading = ctx.model_copy(update={"text": "Kestrel áp đảo cả trận", "language": "vi"})
        return analyze_claim(reading)


@pytest.mark.parametrize(
    "text", ["Kestrel không trụ nổi đến cuối trận", "Ten-man Kestrel ran out of steam"]
)
def test_an_llm_dominance_the_claim_does_not_name_is_refused(text: str) -> None:
    settings = Settings()
    service = ClaimService(settings, ports=lambda: (DominanceReader(), MockExplainer()))
    result = verify(text, service=service, scenario="red_card_swing", seed=7)
    assert result.verdict == "INSUFFICIENT_DATA"
    assert result.analyst_source == "rule"
    assert "analyst_disagreement" in result.degraded
    decision = next(s for s in result.trace or () if s.agent == "RuleAnalyst").output["decision"]
    assert "does not name" in decision


def test_an_llm_dominance_the_claim_names_is_used() -> None:
    """The rules read this one too; a claim they cannot parse but that says "áp đảo" keeps the
    LLM's reading (the check only asks for the word)."""
    settings = Settings()
    service = ClaimService(settings, ports=lambda: (DominanceReader(), MockExplainer()))
    result = verify("Kestrel áp đảo cả trận", service=service, scenario="red_card_swing", seed=7)
    assert result.analyst_source == "llm" and result.verdict != "INSUFFICIENT_DATA"
