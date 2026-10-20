"""Rare failure paths of the claim workflow: provider content filter, adapter crashes, and a
template rejected by the Guardrail (a bug that must still not break the verdict)."""

import pytest
from helpers import verify

from pundit.agents.contracts import (
    ClaimContext,
    ClaimSpec,
    ExplanationDraft,
    GuardrailViolation,
    JudgedClaim,
)
from pundit.agents.explain_templates import render
from pundit.llm.mock import MockClaimAnalyst
from pundit.llm.ports import ContentFiltered
from pundit.llm.settings import Settings
from pundit.orchestration import steps as steps_module
from pundit.orchestration.service import ClaimService

CLAIM = "Đội nhà áp đảo hiệp 2"


class FilteredOnce:
    """Explainer whose first answer is blocked by the provider's content filter."""

    model = "fake-llm"

    def __init__(self) -> None:
        self.calls = 0
        self.feedback: list[list[GuardrailViolation] | None] = []

    async def explain(
        self, judged: JudgedClaim, feedback: list[GuardrailViolation] | None = None
    ) -> ExplanationDraft:
        self.calls += 1
        self.feedback.append(feedback)
        if self.calls == 1:
            raise ContentFiltered("blocked")
        return ExplanationDraft(
            text=render(judged), language=judged.language, source="llm", attempt=1
        )


class Crashing:
    model = "fake-llm"

    async def analyze(self, ctx: ClaimContext, feedback: list[str] | None = None) -> ClaimSpec:
        raise ZeroDivisionError("adapter bug")

    async def explain(
        self, judged: JudgedClaim, feedback: list[GuardrailViolation] | None = None
    ) -> ExplanationDraft:
        raise KeyError("adapter bug")


def test_content_filter_counts_as_unsafe_and_is_retried() -> None:
    explainer = FilteredOnce()
    service = ClaimService(Settings(), ports=lambda: (MockClaimAnalyst(), explainer))
    result = verify(CLAIM, service=service)
    assert result.verdict == "TRUE" and result.explanation_source == "llm"
    assert explainer.calls == 2
    retry_feedback = explainer.feedback[1]
    assert retry_feedback is not None and retry_feedback[0].code == "UNSAFE_CONTENT"
    guard = [s for s in result.trace or () if s.agent == "Guardrail"]
    assert [s.status for s in guard] == ["rejected", "ok"]
    assert service.breaker.consecutive_failures == 0  # the provider answered


def test_adapter_crashes_fall_back() -> None:
    crashing = Crashing()
    service = ClaimService(Settings(), ports=lambda: (crashing, crashing))  # type: ignore[arg-type, return-value]
    result = verify(CLAIM, service=service)
    assert result.verdict == "TRUE"
    assert result.degraded == ("analyst_fallback", "explainer_fallback")
    errors = [s.error for s in result.trace or () if s.status == "error"]
    assert any("ZeroDivisionError" in (e or "") for e in errors)
    assert any("KeyError" in (e or "") for e in errors)


def test_a_rejected_template_publishes_the_verdict_without_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_template(judged: JudgedClaim, attempt: int = 1) -> ExplanationDraft:
        return ExplanationDraft(
            text="Harbor Lions FC 99%.",
            language=judged.language,
            source="template",
            attempt=attempt,
        )

    monkeypatch.setattr(steps_module, "template_draft", broken_template)
    result = verify("HAR áp đảo hiệp 2", at="30'")  # INSUFFICIENT: straight to the template
    assert result.verdict == "INSUFFICIENT_DATA"
    assert result.explanation == "" and not result.guardrail.passed
    assert "explainer_fallback" in result.degraded
