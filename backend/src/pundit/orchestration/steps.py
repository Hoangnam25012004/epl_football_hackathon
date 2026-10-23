"""The steps of one claim, over a shared blackboard (`ClaimRun`).

Each step takes one typed message and returns the next one; the routing (which step handles
which message) is the workflow graph of claim_workflow.py. The same steps also run in
`run_pipeline`, a plain asyncio loop used when the workflow itself fails, so both paths share
every rule. No step raises on an LLM problem: it records it and routes to the fallback.
"""

import logging
from dataclasses import dataclass, field
from functools import partial
from typing import Literal

from pundit.agents.analyst_rules import analyze_claim, claim_language
from pundit.agents.collector import collect, get_stat
from pundit.agents.context import build_match_context
from pundit.agents.contracts import (
    AnalystOutcome,
    ClaimAnalysis,
    ClaimContext,
    ClaimRequest,
    ClaimResult,
    ClaimSpec,
    Degraded,
    DraftForReview,
    EvidenceBundle,
    ExplainRequest,
    ExplanationDraft,
    GuardrailReport,
    GuardrailViolation,
    JudgedClaim,
    Publication,
    SpanStatus,
    StatQuery,
    Subject,
    TemplateRequest,
    ToolCall,
)
from pundit.agents.explain_templates import template_draft
from pundit.agents.guardrail import (
    CONTENT_FILTER,
    EVENT_MINUTE_TYPES,
    check_judged,
    feedback_lines,
)
from pundit.agents.judge import judge
from pundit.agents.language import Text
from pundit.agents.spec_validation import validate_spec
from pundit.agents.trace import SpanHandle, TraceRecorder
from pundit.domain.log import LogView
from pundit.domain.matchtime import format_minute
from pundit.llm.ports import (
    ClaimAnalystPort,
    ContentFiltered,
    ExplainerPort,
    LlmError,
    LlmOutputError,
    LlmTimeout,
)
from pundit.llm.prompts import ANALYST_PROMPT_VERSION, EXPLAINER_PROMPT_VERSION
from pundit.orchestration.resilience import Budget, CircuitBreaker, with_timeout

log = logging.getLogger("pundit.claims")

MAX_LLM_ATTEMPTS = 2  # one retry per LLM step (design L5)
Message = (
    ClaimContext
    | AnalystOutcome
    | ClaimAnalysis
    | EvidenceBundle
    | ExplainRequest
    | TemplateRequest
    | DraftForReview
    | Publication
)


@dataclass
class ClaimRun:
    """Blackboard of one claim, shared by every agent of its workflow run."""

    claim_id: str
    request: ClaimRequest
    view: LogView  # pinned at as_of_seq: later events never change it
    out_of_range: bool
    analyst: ClaimAnalystPort | None  # None: deterministic only
    explainer: ExplainerPort | None
    breaker: CircuitBreaker
    budget: Budget
    llm_timeout_s: float
    trace: TraceRecorder
    model: str
    degraded: list[Degraded] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    analyst_source: Literal["llm", "rule"] = "rule"

    def flag(self, flag: Degraded) -> None:
        if flag not in self.degraded:
            self.degraded.append(flag)

    def time(self, step: str, span: SpanHandle) -> None:
        """Add the time since `span` started to the step's total."""
        elapsed = max(0.0, self.trace.since_ms(span))
        self.timings_ms[step] = round(self.timings_ms.get(step, 0.0) + elapsed, 3)


def _canonical(spec: ClaimSpec, run: ClaimRun) -> list[str]:
    """Meaning of a spec, ignoring wording (text spans, notes) and implicit defaults."""
    meta = run.view.meta
    items: list[str] = []
    for a in spec.assertions:
        other = a.other
        if other is None and a.kind == "compare" and a.subject.team is not None:
            other = Subject(kind="team", team=meta.opponent(a.subject.team))
        op = "==" if a.op == "~" else a.op
        value = None if a.value is None else round(a.value, 6)
        items.append(
            "|".join(
                str(part)
                for part in (
                    a.kind,
                    a.subject.model_dump_json(),
                    other.model_dump_json() if other is not None else None,
                    a.metric,
                    op,
                    value,
                    a.ratio,
                    a.window.model_dump_json(),
                    a.negated,
                    a.approx or a.op == "~",
                )
            )
        )
    return sorted(items)


def _event_minutes(judged: JudgedClaim, view: LogView) -> frozenset[str]:
    """The minutes of the events behind the facts that name a moment (a goal, a card, VAR...):
    the only minutes a model may say something happened at."""
    minutes: set[str] = set()
    for fact in judged.all_facts():
        for event_id in fact.evidence_event_ids:
            if view.has(event_id):
                event = view.get(event_id)
                if event.type in EVENT_MINUTE_TYPES:
                    minutes.add(format_minute(event.period, event.clock_ms))
    return frozenset(minutes)


def same_meaning(a: ClaimSpec, b: ClaimSpec, run: ClaimRun) -> bool:
    return _canonical(a, run) == _canonical(b, run)


class ClaimSteps:
    def __init__(self, run: ClaimRun) -> None:
        self.run = run

    # ------------------------------------------------------------------ ClaimIntake

    def intake(self, request: ClaimRequest) -> ClaimContext | TemplateRequest:
        run = self.run
        span = run.trace.start(
            "ClaimIntake",
            "agent",
            "intake",
            {"text": request.text, "language": request.language, "as_of_seq": request.as_of_seq},
        )
        match = build_match_context(run.view)
        language = (
            request.language if request.language != "auto" else claim_language(request.text, match)
        )
        ctx = ClaimContext(
            claim_id=run.claim_id,
            request=request,
            text=" ".join(request.text.split()),
            language=language,
            match=match,
        )
        run.time("intake", span)
        if run.out_of_range:
            span.finish({"as_of_out_of_range": True}, status="ok")
            spec = ClaimSpec(language=language, unsupported_reason="as_of_out_of_range")
            judged = JudgedClaim(
                claim_id=run.claim_id,
                text=ctx.text,
                context=match,
                spec=spec,
                verdict="INSUFFICIENT_DATA",
                reason_code="as_of_out_of_range",
                results=(),
                headline=(),
            )
            return TemplateRequest(judged=judged, reason="insufficient")
        span.finish(
            {"language": language, "as_of_seq": match.as_of_seq, "clock": match.clock_display}
        )
        return ctx

    # ------------------------------------------------------------------ ClaimAnalyst (LLM)

    async def analyze(self, ctx: ClaimContext) -> AnalystOutcome:
        run = self.run
        span = run.trace.start("ClaimAnalyst", "agent", "analyze", {"claim": ctx.text})
        try:
            return await self._analyze(ctx, span)
        finally:
            run.time("analyst", span)

    async def _analyze(self, ctx: ClaimContext, span: SpanHandle) -> AnalystOutcome:
        run = self.run
        if run.analyst is None:
            span.finish({"skipped": "deterministic mode"})
            return AnalystOutcome(context=ctx, spec=None, failure=None)
        if not run.breaker.allow():
            span.finish({"skipped": "circuit open"}, status="fallback")
            return AnalystOutcome(context=ctx, spec=None, failure="circuit_open")
        feedback: list[str] | None = None
        for attempt in range(1, MAX_LLM_ATTEMPTS + 1):
            timeout = run.budget.timeout_for(run.llm_timeout_s)
            call = run.trace.start(
                "ClaimAnalyst",
                "llm",
                "analyze_claim",
                {"prompt_version": ANALYST_PROMPT_VERSION, "feedback": feedback or []},
                attempt=attempt,
                model=run.analyst.model,
                parent=span,
            )
            try:
                spec = await with_timeout(partial(run.analyst.analyze, ctx, feedback), timeout)
            except LlmTimeout as exc:
                run.breaker.record_failure()
                call.finish(status="error", error=str(exc))
                span.finish({"failure": "timeout"}, status="fallback")
                return AnalystOutcome(
                    context=ctx, spec=None, failure="timeout" if timeout > 0 else "budget"
                )
            except LlmOutputError as exc:
                run.breaker.record_failure()
                feedback = exc.errors
                retry = attempt < MAX_LLM_ATTEMPTS
                call.finish(
                    {"errors": exc.errors}, status="retry" if retry else "error", error=str(exc)
                )
                continue
            except LlmError as exc:
                run.breaker.record_failure()
                call.finish(status="error", error=str(exc)[:300])
                span.finish({"failure": "error"}, status="fallback")
                return AnalystOutcome(context=ctx, spec=None, failure="error")
            except Exception as exc:  # an adapter bug must not take the claim down
                run.breaker.record_failure()
                log.exception("claim analyst crashed")
                call.finish(status="error", error=f"{type(exc).__name__}: {exc}"[:300])
                span.finish({"failure": "error"}, status="fallback")
                return AnalystOutcome(context=ctx, spec=None, failure="error")
            run.breaker.record_success()
            errors = validate_spec(spec, ctx.match, claim=Text.of(ctx.text))
            if errors:
                feedback = errors
                retry = attempt < MAX_LLM_ATTEMPTS
                call.finish(
                    {"errors": errors},
                    status="retry" if retry else "error",
                    error=f"invalid spec: {'; '.join(errors)}"[:300],
                )
                continue
            call.finish({"assertions": len(spec.assertions), "reason": spec.unsupported_reason})
            span.finish({"assertions": len(spec.assertions)})
            return AnalystOutcome(context=ctx, spec=spec)
        span.finish({"failure": "invalid_output"}, status="fallback")
        return AnalystOutcome(context=ctx, spec=None, failure="invalid_output")

    # ------------------------------------------------------------------ RuleAnalyst / consensus

    def reconcile(self, outcome: AnalystOutcome) -> ClaimAnalysis:
        """Always run the rule parser; prefer it when the LLM failed or disagrees."""
        run = self.run
        ctx = outcome.context
        span = run.trace.start(
            "RuleAnalyst", "agent", "reconcile", {"llm_failure": outcome.failure}
        )
        rule = analyze_claim(ctx)
        llm = outcome.spec
        chosen, source, decision = rule, "rule", "rule"
        if llm is None:
            if outcome.failure == "circuit_open":
                run.flag("circuit_open")
                decision = "fallback: circuit open"
            elif outcome.failure is not None:
                run.flag("analyst_fallback")
                decision = f"fallback: llm {outcome.failure}"
        elif not rule.assertions and "out of scope" not in rule.notes:
            chosen, source, decision = llm, "llm", "llm (rules could not parse)"
        elif same_meaning(llm, rule, run):
            chosen, source, decision = llm, "llm", "llm (agrees with rules)"
        else:
            run.flag("analyst_disagreement")
            decision = "rules (llm disagreed)"
        run.analyst_source = "llm" if source == "llm" else "rule"
        status: SpanStatus = (
            "fallback" if source == "rule" and (llm is not None or outcome.failure) else "ok"
        )
        span.finish(
            {
                "decision": decision,
                "assertions": [
                    a.model_dump(mode="json", exclude={"text_span"}) for a in chosen.assertions
                ],
                "unsupported_reason": chosen.unsupported_reason,
            },
            status=status,
        )
        run.time("rule_analyst", span)
        return ClaimAnalysis(context=ctx, spec=chosen, source=run.analyst_source)

    # ------------------------------------------------------------------ EvidenceCollector

    def collect(self, analysis: ClaimAnalysis) -> EvidenceBundle:
        run = self.run
        span = run.trace.start(
            "EvidenceCollector", "agent", "collect", {"assertions": len(analysis.spec.assertions)}
        )

        def traced(query: StatQuery) -> ToolCall:
            tool = run.trace.start(
                "EvidenceCollector",
                "tool",
                "get_stat",
                query.model_dump(mode="json", exclude_none=True),
                parent=span,
            )
            result = get_stat(run.view, query)
            tool.finish(
                {
                    "value": result.result.value,
                    "unit": result.result.unit,
                    "status": result.result.status,
                    "evidence_total": len(result.result.evidence_event_ids),
                    "evidence_sample": list(result.result.evidence_event_ids[:5]),
                }
            )
            return result

        bundle = collect(analysis, run.view, call=traced)
        span.finish({"tool_calls": len(bundle.tool_calls)})
        run.time("collector", span)
        return bundle

    # ------------------------------------------------------------------ Judge

    def judge(self, bundle: EvidenceBundle) -> ExplainRequest | TemplateRequest:
        run = self.run
        span = run.trace.start("Judge", "agent", "judge", {"tool_calls": len(bundle.tool_calls)})
        judged = judge(bundle)
        span.finish(
            {
                "verdict": judged.verdict,
                "reason_code": judged.reason_code,
                "outcomes": [r.outcome for r in judged.results],
                "rationales": [r.rationale_code for r in judged.results],
            }
        )
        run.time("judge", span)
        if judged.verdict == "INSUFFICIENT_DATA":
            return TemplateRequest(judged=judged, reason="insufficient")
        if run.explainer is None:
            return TemplateRequest(judged=judged, reason="llm_unavailable")
        return ExplainRequest(judged=judged, attempt=1)

    # ------------------------------------------------------------------ Explainer (LLM)

    async def explain(self, request: ExplainRequest) -> DraftForReview | TemplateRequest:
        run = self.run
        span = run.trace.start(
            "Explainer",
            "llm",
            "explain",
            {
                "prompt_version": EXPLAINER_PROMPT_VERSION,
                "feedback": feedback_lines(request.feedback),
            },
            attempt=request.attempt,
            model=run.explainer.model if run.explainer is not None else None,
        )
        try:
            return await self._explain(request, span)
        finally:
            run.time("explainer", span)

    async def _explain(
        self, request: ExplainRequest, span: SpanHandle
    ) -> DraftForReview | TemplateRequest:
        run = self.run
        judged = request.judged
        explainer = run.explainer
        if explainer is None:
            span.finish({"skipped": "deterministic mode"}, status="fallback")
            return TemplateRequest(judged=judged, reason="llm_unavailable")
        if not run.breaker.allow():
            run.flag("circuit_open")
            span.finish({"skipped": "circuit open"}, status="fallback")
            return TemplateRequest(judged=judged, reason="llm_unavailable")
        feedback = list(request.feedback) or None
        timeout = run.budget.timeout_for(run.llm_timeout_s)
        try:
            draft = await with_timeout(partial(explainer.explain, judged, feedback), timeout)
        except ContentFiltered as exc:
            run.breaker.record_success()  # the provider answered; the text was the problem
            span.finish(status="rejected", error=str(exc))
            empty = ExplanationDraft(
                text="", language=judged.language, source="llm", attempt=request.attempt
            )
            return DraftForReview(judged=judged, draft=empty, content_filtered=True)
        except LlmError as exc:  # timeout, provider error, invalid output
            run.breaker.record_failure()
            failure = (
                "timeout"
                if isinstance(exc, LlmTimeout)
                else "invalid_output"
                if isinstance(exc, LlmOutputError)
                else "error"
            )
            span.finish({"failure": failure}, status="error", error=str(exc)[:300])
            return TemplateRequest(judged=judged, reason="llm_failed")
        except Exception as exc:
            run.breaker.record_failure()
            log.exception("explainer crashed")
            span.finish(status="error", error=f"{type(exc).__name__}: {exc}"[:300])
            return TemplateRequest(judged=judged, reason="llm_failed")
        run.breaker.record_success()
        draft = draft.model_copy(update={"attempt": request.attempt, "source": "llm"})
        span.finish({"text": draft.text, "chars": len(draft.text)})
        return DraftForReview(judged=judged, draft=draft)

    # ------------------------------------------------------------------ TemplateExplainer

    def template(self, request: TemplateRequest) -> DraftForReview:
        run = self.run
        if request.reason != "insufficient":
            run.flag("explainer_fallback")
        span = run.trace.start(
            "TemplateExplainer",
            "fallback" if request.reason != "insufficient" else "agent",
            "template",
            {"reason": request.reason},
        )
        draft = template_draft(request.judged)
        span.finish(
            {"text": draft.text}, status="fallback" if request.reason != "insufficient" else "ok"
        )
        run.time("template", span)
        return DraftForReview(judged=request.judged, draft=draft)

    # ------------------------------------------------------------------ Guardrail

    def review(self, item: DraftForReview) -> Publication | ExplainRequest | TemplateRequest:
        run = self.run
        judged, draft = item.judged, item.draft
        span = run.trace.start(
            "Guardrail",
            "guardrail",
            "check",
            {"source": draft.source, "attempt": draft.attempt, "chars": len(draft.text)},
            attempt=draft.attempt,
        )
        if item.content_filtered:
            report = GuardrailReport(
                passed=False,
                violations=(GuardrailViolation(code="UNSAFE_CONTENT", detail=CONTENT_FILTER),),
            )
        else:
            report = check_judged(
                draft.text,
                judged,
                model_text=draft.source == "llm",
                event_minutes=_event_minutes(judged, run.view),
            )
        run.time("guardrail", span)
        if report.passed:
            span.finish({"passed": True})
            return Publication(judged=judged, draft=draft, report=report)
        violations = [v.model_dump(mode="json") for v in report.violations]
        span.finish({"passed": False, "violations": violations}, status="rejected")
        if draft.source == "llm" and draft.attempt < MAX_LLM_ATTEMPTS:
            return ExplainRequest(
                judged=judged, attempt=draft.attempt + 1, feedback=report.violations
            )
        if draft.source == "llm":
            return TemplateRequest(judged=judged, reason="guardrail_exhausted")
        # The template itself failed: a bug. Publish the verdict without an explanation.
        log.error("template explanation rejected by the guardrail: %s", violations)
        run.flag("explainer_fallback")
        empty = draft.model_copy(update={"text": ""})
        return Publication(judged=judged, draft=empty, report=report)

    # ------------------------------------------------------------------ Publisher

    def publish(self, item: Publication) -> ClaimResult:
        run = self.run
        judged = item.judged
        evidence = sorted(
            {
                eid
                for result in judged.results
                for fact in result.facts
                for eid in fact.evidence_event_ids
            }
        )
        run.trace.event(
            "Publisher",
            "agent",
            "publish",
            output={"verdict": judged.verdict, "degraded": list(run.degraded)},
        )
        run.timings_ms["total"] = round(run.trace.elapsed_ms(), 3)
        return ClaimResult(
            claim_id=run.claim_id,
            match_id=judged.context.match_id,
            as_of_seq=judged.context.as_of_seq,
            as_of_display=judged.context.clock_display,
            text=run.request.text,
            language=judged.language,
            verdict=judged.verdict,
            reason_code=judged.reason_code,
            assertions=judged.results,
            headline=judged.headline,
            explanation=item.draft.text,
            explanation_source=item.draft.source,
            analyst_source=run.analyst_source,
            model=run.model,
            evidence_event_ids=tuple(evidence),
            guardrail=item.report,
            degraded=tuple(run.degraded),
            timings_ms=dict(run.timings_ms),
            trace=run.trace.spans,
        )


async def run_pipeline(steps: ClaimSteps, request: ClaimRequest) -> ClaimResult:
    """The workflow graph as a plain asyncio loop (fallback runner, design section 9.5)."""
    message: Message = steps.intake(request)
    for _ in range(32):  # the graph is acyclic except the bounded explainer retry
        match message:
            case ClaimContext():
                message = await steps.analyze(message)
            case AnalystOutcome():
                message = steps.reconcile(message)
            case ClaimAnalysis():
                message = steps.collect(message)
            case EvidenceBundle():
                message = steps.judge(message)
            case ExplainRequest():
                message = await steps.explain(message)
            case TemplateRequest():
                message = steps.template(message)
            case DraftForReview():
                message = steps.review(message)
            case Publication():
                return steps.publish(message)
    raise RuntimeError("claim pipeline did not finish")
