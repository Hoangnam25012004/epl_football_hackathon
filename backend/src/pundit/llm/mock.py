"""Mock LLM (docs/day2-design.md section 8.2): the rule analyst and the templates behind the
LLM ports, plus deterministic fault injection to exercise every recovery path.

The same code is the fallback when a real model fails (principle L7), so the most-tested path
is the one the system falls back to.
"""

import asyncio

from pundit.agents.analyst_rules import analyze_claim
from pundit.agents.contracts import (
    Assertion,
    ClaimContext,
    ClaimSpec,
    ExplanationDraft,
    GuardrailViolation,
    JudgedClaim,
    MatchContext,
    Subject,
)
from pundit.agents.explain_templates import render
from pundit.agents.guardrail import rounding_match
from pundit.agents.language import Text
from pundit.agents.numbers import extract_numbers
from pundit.llm.faults import FaultCounter, FaultKind, FaultPlan
from pundit.llm.ports import LlmError, LlmOutputError

MOCK_MODEL = "mock"
MOCK_HANG_S = 3600.0  # a "timeout" fault sleeps until the orchestrator's timeout cancels it
UNSAFE_SUFFIX = {"vi": " Thật là vô dụng.", "en": " Simply useless."}
INVALID_JSON = '{"language": "vi", "assertions": "not-a-list"'


async def _misbehave(fault: FaultKind | None, role: str) -> None:
    if fault == "timeout":
        await asyncio.sleep(MOCK_HANG_S)
    if fault == "error":
        raise LlmError(f"mock {role}: injected provider error")
    if fault == "invalid_json":
        raise LlmOutputError(
            f"mock {role}: injected invalid JSON",
            errors=["the output is not valid JSON for the schema"],
            raw=INVALID_JSON,
        )


def _swap(subject: Subject | None, match: MatchContext) -> Subject | None:
    if subject is None or subject.kind != "team" or subject.team is None:
        return subject
    return Subject(kind="team", team=match.opponent(subject.team))


def swap_teams(spec: ClaimSpec, match: MatchContext) -> ClaimSpec:
    """The `wrong_entity` fault: every team subject replaced by its opponent."""
    swapped: list[Assertion] = []
    for a in spec.assertions:
        subject = _swap(a.subject, match)
        assert subject is not None
        swapped.append(a.model_copy(update={"subject": subject, "other": _swap(a.other, match)}))
    return spec.model_copy(update={"assertions": tuple(swapped)})


def hallucinate(text: str, judged: JudgedClaim) -> str:
    """The `hallucinate` fault: one number replaced by a value that matches no fact."""
    values = [f.value for f in judged.all_facts()]
    claim = Text(text)
    for number in extract_numbers(claim):
        if number.minute or number.period or number.pair is not None:
            continue
        unit = (
            1.0 if number.decimals == 0 else 10.0 ** (1 - number.decimals)
        )  # 61 -> 65, 2.24 -> 2.64
        for bump in (4, 7, 11, 23, 37, 51):
            candidate = number.signed + bump * unit
            if not any(rounding_match(candidate, number.decimals, v) for v in values):
                written = f"{candidate:.{number.decimals}f}"
                start = number.start - (1 if number.negative else 0)
                return text[:start] + written + text[number.end :]
    extra = 97.0
    while any(rounding_match(extra, 0, v) for v in values):
        extra += 1
    sentence = (
        f" Có tổng cộng {extra:.0f} cú sút."
        if judged.language == "vi"
        else f" There were {extra:.0f} shots."
    )
    return text + sentence


class MockClaimAnalyst:
    """ClaimAnalystPort: the rule analyst, with optional injected faults."""

    model = MOCK_MODEL

    def __init__(self, faults: FaultCounter | None = None, *, latency_s: float = 0.0) -> None:
        self._faults = faults or FaultPlan().counter()
        self._latency_s = latency_s

    async def analyze(self, ctx: ClaimContext, feedback: list[str] | None = None) -> ClaimSpec:
        del feedback  # the rules do not learn from feedback
        if self._latency_s:
            await asyncio.sleep(self._latency_s)
        fault = self._faults.take("analyst")
        await _misbehave(fault, "analyst")
        spec = analyze_claim(ctx)
        if fault == "wrong_entity":
            spec = swap_teams(spec, ctx.match)
        return spec


class MockExplainer:
    """ExplainerPort: the templates, with optional injected faults."""

    model = MOCK_MODEL

    def __init__(self, faults: FaultCounter | None = None, *, latency_s: float = 0.0) -> None:
        self._faults = faults or FaultPlan().counter()
        self._latency_s = latency_s

    async def explain(
        self, judged: JudgedClaim, feedback: list[GuardrailViolation] | None = None
    ) -> ExplanationDraft:
        del feedback
        if self._latency_s:
            await asyncio.sleep(self._latency_s)
        fault = self._faults.take("explainer")
        await _misbehave(fault, "explainer")
        text = render(judged)
        if fault == "hallucinate":
            text = hallucinate(text, judged)
        elif fault == "unsafe":
            text += UNSAFE_SUFFIX[judged.language]
        return ExplanationDraft(text=text, language=judged.language, source="llm", attempt=1)
