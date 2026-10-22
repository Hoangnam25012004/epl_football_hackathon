"""The steps of one studio job, over a shared blackboard (`StudioRun`) (docs/day3-design.md
section 14).

StudioIntake plans the branches of a trigger; FactPackBuilder calls the Stats Tools on the view
pinned at the trigger's seq; three branches (talking point, lower-third, commentary) work on the
same FactPack concurrently, each with its own time budget, Guardrail and template fallback;
StudioPublisher merges the branch results in a fixed order. The same steps run in the MAF
workflow (studio_workflow.py) and in `run_studio_pipeline`, the plain asyncio runner used when
the workflow itself fails. No step raises on an LLM problem: it records it, flags it and
publishes the template.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Literal, TypeVar

from pundit.agents.collector import get_stat
from pundit.agents.commentary import FALLBACK, VERSIONS, excitement, render_texts, speak
from pundit.agents.contracts import (
    BranchResult,
    CommentaryDraft,
    CommentaryItem,
    CommentaryText,
    Degraded,
    Fact,
    FactPack,
    GraphicsChoice,
    GuardrailViolation,
    Lang,
    Register,
    StatQuery,
    StudioBranch,
    StudioJob,
    StudioOutput,
    StudioPlan,
    TalkingDraft,
    TalkingLine,
    TalkingPoint,
    ToolCall,
)
from pundit.agents.factpack import build_pack, plan
from pundit.agents.graphics import (
    build_lower_third,
    options,
    rule_choice,
    validate_choice,
    wants_graphic,
)
from pundit.agents.guardrail import feedback_lines
from pundit.agents.phrasebank import fill
from pundit.agents.rules_library import mentioned_rules
from pundit.agents.studio_guard import (
    StudioProduct,
    StudioScope,
    check_studio,
    cited_keys,
    scope_of,
)
from pundit.agents.studio_values import pack_values
from pundit.agents.talking_points import ANGLES, line_from_text, render_lines, wants_talking_point
from pundit.agents.trace import SpanHandle, TraceRecorder
from pundit.domain.log import LogView
from pundit.llm.ports import (
    CommentatorPort,
    ContentFiltered,
    GraphicsDesignerPort,
    LlmError,
    LlmOutputError,
    LlmTimeout,
    TalkingPointWriterPort,
)
from pundit.llm.settings import Settings
from pundit.llm.studio_prompts import (
    COMMENTARY_PROMPT_VERSION,
    GRAPHICS_PROMPT_VERSION,
    TALKING_PROMPT_VERSION,
)
from pundit.orchestration.resilience import Budget, CircuitBreaker, Clock, with_timeout

log = logging.getLogger("pundit.studio")

D = TypeVar("D")
MAX_LLM_ATTEMPTS = 2  # one retry with the Guardrail's feedback (design L5)
MIN_TALKING_LINES, MAX_TALKING_LINES = 2, 3
OFFLINE_REQUESTS_AT_ONCE = 4
MIN_LLM_S = 0.5  # an LLM call starts only with at least this much budget left (section 14.3)
LlmOutcome = Literal["ok", "rejected", "failed", "skipped"]
Gate = Literal["call", "deterministic", "importance", "budget"]


@dataclass(frozen=True)
class StudioBudgets:
    """Seconds per branch, per job and per model call (section 14.3)."""

    talking_point_s: float
    commentary_s: float
    graphics_s: float
    job_s: float
    llm_timeout_s: float
    min_llm_s: float = MIN_LLM_S
    commentary_attempts: int = 1  # live commentary is never retried: a retry misses the moment

    @classmethod
    def live(cls, settings: Settings) -> "StudioBudgets":
        """Real time: the replay never waits for a model."""
        return cls(
            talking_point_s=settings.studio_tp_budget_s,
            commentary_s=settings.studio_commentary_budget_s,
            graphics_s=settings.studio_graphics_budget_s,
            job_s=settings.studio_job_budget_s,
            llm_timeout_s=min(settings.llm_timeout_s, settings.studio_job_budget_s),
        )

    @classmethod
    def offline(cls, settings: Settings) -> "StudioBudgets":
        """`pundit studio`: no replay to keep up with, so every call gets the provider's
        timeout and one retry. A job sends up to four requests at once (two commentary
        languages, the talking point, the graphics); a local server answers them one after the
        other, so a call may wait for the three others before its own turn."""
        call = settings.llm_timeout_s * OFFLINE_REQUESTS_AT_ONCE
        return cls(
            talking_point_s=2 * call + 1,
            commentary_s=2 * call + 1,
            graphics_s=call + 1,
            job_s=2 * call + 2,
            llm_timeout_s=call,
            commentary_attempts=MAX_LLM_ATTEMPTS,
        )


@dataclass
class StudioPorts:
    """The models of one job; None means "template only" for that role."""

    talker: TalkingPointWriterPort | None = None
    commentator: CommentatorPort | None = None
    designer: GraphicsDesignerPort | None = None


@dataclass
class StudioRun:
    """Blackboard of one job, shared by every agent of its workflow run."""

    job: StudioJob
    view: LogView  # pinned at trigger.seq: later events never change it
    ports: StudioPorts
    breaker: CircuitBreaker  # the studio's own breaker (never the claims')
    budgets: StudioBudgets
    trace: TraceRecorder
    model: str
    clock: Clock
    llm_min_importance: int = 1
    llm_languages: tuple[Lang, ...] = ("vi", "en")  # commentary versions a model may write
    degraded: list[Degraded] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    branches: tuple[StudioBranch, ...] = ()
    job_budget: Budget = field(init=False)

    def __post_init__(self) -> None:
        self.job_budget = Budget(self.budgets.job_s, clock=self.clock)

    def flag(self, flag: Degraded, into: list[Degraded] | None = None) -> None:
        for target in (self.degraded, into):
            if target is not None and flag not in target:
                target.append(flag)

    def time(self, step: str, span: SpanHandle) -> None:
        elapsed = max(0.0, self.trace.since_ms(span))
        self.timings_ms[step] = round(self.timings_ms.get(step, 0.0) + elapsed, 3)

    def branch_budget(self, seconds: float) -> Budget:
        """A branch budget never outlives the job budget."""
        return Budget(min(seconds, self.job_budget.remaining()), clock=self.clock)


def _evidence(event_ids: Sequence[str], facts: Sequence[Fact]) -> tuple[str, ...]:
    return tuple(sorted({*event_ids, *(e for f in facts for e in f.evidence_event_ids)}))


def _facts_by_key(pack: FactPack, keys: Sequence[str]) -> tuple[Fact, ...]:
    wanted = set(keys)
    return tuple(f for f in pack.facts if f.key in wanted)


def _violations(items: Sequence[GuardrailViolation]) -> list[dict[str, object]]:
    return [v.model_dump(mode="json", exclude_none=True) for v in items]


class StudioSteps:
    def __init__(self, run: StudioRun) -> None:
        self.run = run

    # ------------------------------------------------------------------ StudioIntake

    def intake(self, job: StudioJob) -> StudioPlan:
        run = self.run
        trigger = job.trigger
        span = run.trace.start(
            "StudioIntake",
            "agent",
            "intake",
            {
                "trigger": trigger.id,
                "kind": trigger.kind,
                "importance": trigger.importance,
                "seq": trigger.seq,
                "minute": trigger.display_minute,
            },
        )
        branches: list[StudioBranch] = []
        if wants_talking_point(trigger):
            branches.append("talking_point")
        if wants_graphic(trigger):
            branches.append("lower_third")
        branches.append("commentary")
        run.branches = tuple(branches)
        span.finish({"branches": branches, "first_rules": list(job.first_rules)})
        run.time("intake", span)
        return StudioPlan(job=job, branches=run.branches)

    # ------------------------------------------------------------------ FactPackBuilder

    def build_pack(self, studio_plan: StudioPlan) -> FactPack:
        run = self.run
        trigger = studio_plan.job.trigger
        span = run.trace.start("FactPackBuilder", "agent", "build_pack", {"trigger": trigger.id})

        def traced(query: StatQuery) -> ToolCall:
            tool = run.trace.start(
                "FactPackBuilder",
                "tool",
                "get_stat",
                query.model_dump(mode="json", exclude_none=True),
                parent=span,
            )
            call = get_stat(run.view, query)
            result = call.result
            tool.finish(
                {
                    "value": result.value,
                    "unit": result.unit,
                    "status": result.status,
                    "evidence_total": len(result.evidence_event_ids),
                    "evidence_sample": list(result.evidence_event_ids[:5]),
                }
            )
            return call

        calls = [traced(query) for query in plan(trigger, run.view.meta)]
        pack = build_pack(trigger, run.view, calls, first_rules=studio_plan.job.first_rules)
        span.finish(
            {
                "facts": len(pack.facts),
                "headline": list(pack.headline),
                "tool_calls": len(calls),
                "as_of_seq": pack.as_of_seq,
            }
        )
        run.time("factpack", span)
        return pack

    # ------------------------------------------------------------------ shared LLM loop

    def _gate(self, pack: FactPack, port: object | None, budget: Budget) -> Gate:
        """Whether a branch may call its model (the breaker is asked right before the call)."""
        run = self.run
        if port is None:
            return "deterministic"
        if pack.trigger.importance < run.llm_min_importance:
            return "importance"
        if budget.remaining() < run.budgets.min_llm_s:
            return "budget"
        return "call"

    def _skip(self, agent: str, reason: str, parent: SpanHandle, flags: list[Degraded]) -> None:
        run = self.run
        if reason in ("budget", "circuit_open"):
            run.flag("llm_skipped", flags)
        if reason == "circuit_open":
            run.flag("circuit_open", flags)
        run.trace.event(
            agent,
            "fallback",
            "skip_llm",
            output={"reason": reason},
            status="fallback" if reason in ("budget", "circuit_open") else "ok",
            parent=parent,
        )

    async def _llm_loop(
        self,
        *,
        agent: str,
        name: str,
        version: str,
        model: str,
        budget: Budget,
        call: Callable[[list[GuardrailViolation] | None], Awaitable[D]],
        review: Callable[[D, int], list[GuardrailViolation]],
        parent: SpanHandle,
        flags: list[Degraded],
        attempts: int = MAX_LLM_ATTEMPTS,
    ) -> LlmOutcome:
        """Call a model, review its draft, retry once with the Guardrail's feedback.
        Timeouts and errors are never retried; every failure counts for the studio breaker."""
        run = self.run
        feedback: list[GuardrailViolation] | None = None
        for attempt in range(1, attempts + 1):
            timeout = min(run.budgets.llm_timeout_s, budget.remaining())
            reason = None
            if timeout < run.budgets.min_llm_s:
                reason = "budget"
            elif not run.breaker.allow():
                reason = "circuit_open"
            if reason is not None and attempt == 1:
                self._skip(agent, reason, parent, flags)
                return "skipped"
            if reason is not None:  # no retry left: the rejected draft falls back to the template
                run.trace.event(
                    agent, "fallback", "skip_retry", output={"reason": reason}, parent=parent
                )
                return "rejected"
            span = run.trace.start(
                agent,
                "llm",
                name,
                {"prompt_version": version, "feedback": feedback_lines(feedback or [])},
                attempt=attempt,
                model=model,
                parent=parent,
            )
            try:
                draft = await with_timeout(partial(call, feedback), timeout)
            except ContentFiltered as exc:
                run.breaker.record_success()  # the provider answered; the text was the problem
                span.finish(status="rejected", error=str(exc))
                return "rejected"
            except LlmError as exc:
                run.breaker.record_failure()
                failure = (
                    "timeout"
                    if isinstance(exc, LlmTimeout)
                    else "invalid_output"
                    if isinstance(exc, LlmOutputError)
                    else "error"
                )
                details = exc.errors[:3] if isinstance(exc, LlmOutputError) else []
                span.finish(
                    {"failure": failure, "errors": details}, status="error", error=str(exc)[:300]
                )
                return "failed"
            except Exception as exc:  # an adapter bug must not take the job down
                run.breaker.record_failure()
                log.exception("%s crashed", agent)
                span.finish(status="error", error=f"{type(exc).__name__}: {exc}"[:300])
                return "failed"
            run.breaker.record_success()
            span.finish({"answered": True})
            violations = review(draft, attempt)
            if not violations:
                return "ok"
            feedback = violations
        return "rejected"

    def _guard(
        self,
        texts: Sequence[str],
        scope: StudioScope,
        *,
        agent: str,
        language: Lang,
        product: StudioProduct,
        mode: Register | None = None,
        model_text: bool,
        attempt: int,
        parent: SpanHandle,
    ) -> list[list[GuardrailViolation]]:
        """Check texts of one language, one Guardrail span for all of them; the violations of
        each text (an empty list: the text passed)."""
        span = self.run.trace.start(
            "Guardrail",
            "guardrail",
            "check_studio",
            {"agent": agent, "language": language, "mode": mode, "texts": list(texts)},
            attempt=attempt,
            parent=parent,
        )
        per_text: list[list[GuardrailViolation]] = []
        for text in texts:
            report = check_studio(
                text,
                scope,
                language=language,
                product=product,
                mode=mode,
                model_text=model_text,
            )
            per_text.append(list(report.violations))
        found = [v for violations in per_text for v in violations]
        if found:
            passed = sum(1 for violations in per_text if not violations)
            span.finish(
                {"passed": False, "texts_passed": passed, "violations": _violations(found)},
                status="rejected",
            )
        else:
            span.finish({"passed": True})
        return per_text

    # ------------------------------------------------------------------ TalkingPointWriter

    async def talking_point(self, pack: FactPack) -> BranchResult:
        run = self.run
        if "talking_point" not in run.branches:
            return BranchResult(branch="talking_point")
        span = run.trace.start(
            "TalkingPointWriter", "agent", "talking_point", {"trigger": pack.trigger.id}
        )
        try:
            return await self._talking_point(pack, span)
        except Exception as exc:  # a bug in one branch must not take the others down
            log.exception("talking-point branch failed")
            if not span.done:
                span.finish(status="error", error=f"{type(exc).__name__}: {exc}"[:300])
            return BranchResult(branch="talking_point")
        finally:
            run.time("talking_point", span)

    async def _talking_point(self, pack: FactPack, span: SpanHandle) -> BranchResult:
        run = self.run
        scope = scope_of(pack)
        budget = run.branch_budget(run.budgets.talking_point_s)
        flags: list[Degraded] = []
        accepted: dict[Lang, tuple[TalkingLine, ...]] = {}
        writer = run.ports.talker
        gate = self._gate(pack, writer, budget)
        outcome: LlmOutcome | None = None
        if gate != "call":
            self._skip("TalkingPointWriter", gate, span, flags)
        else:
            assert writer is not None

            def review(draft: TalkingDraft, attempt: int) -> list[GuardrailViolation]:
                """A language is taken when at least 2 of its lines pass (section 6); the
                lines that failed are left out."""
                found: list[GuardrailViolation] = []
                for language in ("en", "vi"):
                    if language in accepted:
                        continue
                    lines = draft.en if language == "en" else draft.vi
                    per_line = self._guard(
                        lines,
                        scope,
                        agent="TalkingPointWriter",
                        language=language,
                        product="talking_point",
                        model_text=True,
                        attempt=attempt,
                        parent=span,
                    )
                    passed = [
                        line_from_text(text, ANGLES[index], pack)
                        for index, (text, bad) in enumerate(zip(lines, per_line, strict=True))
                        if not bad
                    ]
                    if len(passed) >= MIN_TALKING_LINES:
                        accepted[language] = tuple(passed[:MAX_TALKING_LINES])
                    else:
                        found += [v for bad in per_line for v in bad]
                return found

            outcome = await self._llm_loop(
                agent="TalkingPointWriter",
                name="write_talking_point",
                version=TALKING_PROMPT_VERSION,
                model=writer.model,
                budget=budget,
                call=partial(writer.write, pack),
                review=review,
                parent=span,
                flags=flags,
            )
        lines_by_language: dict[Lang, tuple[TalkingLine, ...]] = {}
        sources: dict[Lang, Literal["llm", "template"]] = {}
        for language in ("en", "vi"):
            if language in accepted:
                lines_by_language[language] = accepted[language]
                sources[language] = "llm"
                continue
            if outcome is not None and outcome != "skipped":
                run.flag("writer_fallback", flags)
            template = render_lines(pack, language)
            per_line = self._guard(
                [line.text for line in template],
                scope,
                agent="TemplateTalkingPoint",
                language=language,
                product="talking_point",
                model_text=False,
                attempt=1,
                parent=span,
            )
            bad = [v for violations in per_line for v in violations]
            if bad:  # a template bug: never put a rejected line in front of the studio
                log.error("talking-point template rejected: %s", feedback_lines(bad))
                span.finish({"published": False, "language": language}, status="error")
                return BranchResult(branch="talking_point")
            lines_by_language[language] = template
            sources[language] = "template"
        trigger = pack.trigger
        keys = [k for lines in lines_by_language.values() for line in lines for k in line.fact_keys]
        facts = _facts_by_key(pack, keys)
        assert trigger.importance >= 2
        item = TalkingPoint(
            id=f"{trigger.id}-tp",
            match_id=trigger.match_id,
            trigger_id=trigger.id,
            trigger_kind=trigger.kind,
            importance=2 if trigger.importance == 2 else 3,
            period=trigger.period,
            clock_ms=trigger.clock_ms,
            display_minute=trigger.display_minute,
            as_of_seq=pack.as_of_seq,
            en=lines_by_language["en"],
            vi=lines_by_language["vi"],
            source_en=sources["en"],
            source_vi=sources["vi"],
            model=run.model,
            facts=facts,
            evidence_event_ids=_evidence(trigger.trigger_event_ids, facts),
            degraded=tuple(flags),
        )
        span.finish(
            {"source_en": sources["en"], "source_vi": sources["vi"], "llm": outcome or "none"},
            status="fallback" if "writer_fallback" in flags else "ok",
        )
        return BranchResult(branch="talking_point", talking_point=item)

    # ------------------------------------------------------------------ GraphicsDesigner

    async def lower_third(self, pack: FactPack) -> BranchResult:
        run = self.run
        if "lower_third" not in run.branches:
            return BranchResult(branch="lower_third")
        span = run.trace.start(
            "GraphicsDesigner", "agent", "lower_third", {"trigger": pack.trigger.id}
        )
        try:
            return await self._lower_third(pack, span)
        except Exception as exc:  # a bug in one branch must not take the others down
            log.exception("lower-third branch failed")
            if not span.done:
                span.finish(status="error", error=f"{type(exc).__name__}: {exc}"[:300])
            return BranchResult(branch="lower_third")
        finally:
            run.time("lower_third", span)

    async def _choose(
        self, pack: FactPack, budget: Budget, span: SpanHandle, flags: list[Degraded]
    ) -> GraphicsChoice | None:
        """The model's choice of template and stats, if it is one of the candidates."""
        run = self.run
        designer = run.ports.designer
        opts = options(pack)
        gate = self._gate(pack, designer, budget)
        if gate != "call" or not opts.stat_keys:
            self._skip("GraphicsDesigner", gate if gate != "call" else "no_candidates", span, flags)
            return None
        assert designer is not None
        chosen: list[GraphicsChoice] = []

        def review(choice: GraphicsChoice, attempt: int) -> list[GuardrailViolation]:
            errors = validate_choice(choice, opts)
            check = run.trace.start(
                "Guardrail",
                "guardrail",
                "validate_choice",
                {"template": choice.template, "stat_keys": list(choice.stat_keys)},
                attempt=attempt,
                parent=span,
            )
            if errors:  # a stat that is not a candidate has no fact behind it
                check.finish({"passed": False, "errors": errors}, status="rejected")
                return [GuardrailViolation(code="UNGROUNDED", detail=e) for e in errors]
            check.finish({"passed": True})
            chosen.append(choice)
            return []

        outcome = await self._llm_loop(
            agent="GraphicsDesigner",
            name="choose_stats",
            version=GRAPHICS_PROMPT_VERSION,
            model=designer.model,
            budget=budget,
            call=lambda feedback: designer.choose(pack, opts),
            review=review,
            parent=span,
            flags=flags,
            attempts=1,
        )
        if outcome != "ok":
            if outcome != "skipped":
                run.flag("writer_fallback", flags)
            return None
        return chosen[0]

    async def _lower_third(self, pack: FactPack, span: SpanHandle) -> BranchResult:
        run = self.run
        trigger = pack.trigger
        flags: list[Degraded] = []
        budget = run.branch_budget(run.budgets.graphics_s)
        choice = await self._choose(pack, budget, span, flags)
        source = "llm" if choice is not None else "rule"
        choice = choice or rule_choice(pack)
        if choice is None:
            span.finish({"published": False, "reason": "no stats to show"})
            return BranchResult(branch="lower_third")
        supersedes = (
            tuple(f"{rid}-lt" for rid in trigger.related_ids)
            if trigger.kind == "goal_overturned"
            else ()
        )
        item = build_lower_third(pack, run.view.meta, choice, source=source, supersedes=supersedes)
        scope = scope_of(pack)
        bad: list[GuardrailViolation] = []
        words: tuple[tuple[Lang, str, str], ...] = (
            ("vi", item.title_vi, item.subtitle_vi),
            ("en", item.title_en, item.subtitle_en),
        )
        for language, title, subtitle in words:
            for part, text in (("title", title), ("subtitle", subtitle)):
                report = check_studio(text, scope, language=language, product="graphic", part=part)
                bad += report.violations
        check = run.trace.start(
            "Guardrail",
            "guardrail",
            "check_studio",
            {"agent": "GraphicsDesigner", "title": item.title_en, "subtitle": item.subtitle_en},
            parent=span,
        )
        if bad:  # nothing rejected goes on air
            check.finish({"passed": False, "violations": _violations(bad)}, status="rejected")
            log.error("lower-third rejected: %s", feedback_lines(bad))
            span.finish({"published": False}, status="error")
            return BranchResult(branch="lower_third")
        check.finish({"passed": True})
        span.finish(
            {"template": item.template, "stats": [s.key for s in item.stats], "source": source},
            status="fallback" if flags else "ok",
        )
        return BranchResult(branch="lower_third", lower_third=item)

    # ------------------------------------------------------------------ Commentator

    async def commentary(self, pack: FactPack) -> BranchResult:
        run = self.run
        span = run.trace.start("Commentator", "agent", "commentary", {"trigger": pack.trigger.id})
        try:
            return await self._commentary(pack, span)
        finally:
            run.time("commentary", span)

    async def _model_versions(
        self,
        pack: FactPack,
        language: Lang,
        budget: Budget,
        span: SpanHandle,
        flags: list[Degraded],
    ) -> tuple[dict[Register, str], LlmOutcome]:
        run = self.run
        commentator = run.ports.commentator
        assert commentator is not None
        scope = scope_of(pack)
        accepted: dict[Register, str] = {}

        def review(draft: CommentaryDraft, attempt: int) -> list[GuardrailViolation]:
            """Each register on its own: a version that fails keeps its template."""
            found: list[GuardrailViolation] = []
            for mode in ("analyst", "casual"):
                if mode in accepted:
                    continue
                text = draft.analyst if mode == "analyst" else draft.casual
                bad = self._guard(
                    [text],
                    scope,
                    agent="Commentator",
                    language=language,
                    product="commentary",
                    mode=mode,
                    model_text=True,
                    attempt=attempt,
                    parent=span,
                )[0]
                if bad:
                    found += bad
                else:
                    accepted[mode] = text
            return found

        outcome = await self._llm_loop(
            agent="Commentator",
            name=f"comment_{language}",
            version=COMMENTARY_PROMPT_VERSION,
            model=commentator.model,
            budget=budget,
            call=lambda feedback: commentator.comment(pack, language, feedback),
            review=review,
            parent=span,
            flags=flags,
            attempts=run.budgets.commentary_attempts,
        )
        return accepted, outcome

    def _template_text(
        self, pack: FactPack, language: Lang, mode: Register, text: str, span: SpanHandle
    ) -> str:
        """The template version, checked; a rejected template falls back to the plain line."""
        scope = scope_of(pack)
        for candidate in (text, fill(FALLBACK[(language, mode)], pack_values(pack, language))):
            report = check_studio(
                candidate, scope, language=language, product="commentary", mode=mode
            )
            if report.passed:
                return candidate
            log.error(
                "commentary template rejected (%s %s %s): %s",
                pack.trigger.kind,
                language,
                mode,
                feedback_lines(list(report.violations)),
            )
            self.run.trace.event(
                "Guardrail",
                "guardrail",
                "check_studio",
                input={"agent": "TemplateCommentator", "text": candidate},
                output={"passed": False, "violations": _violations(report.violations)},
                status="rejected",
                parent=span,
            )
        return ""

    async def _commentary(self, pack: FactPack, span: SpanHandle) -> BranchResult:
        run = self.run
        trigger = pack.trigger
        flags: list[Degraded] = []
        budget = run.branch_budget(run.budgets.commentary_s)
        written: dict[tuple[Lang, Register], str] = {}
        gate = self._gate(pack, run.ports.commentator, budget)
        if gate != "call":
            self._skip("Commentator", gate, span, flags)
        else:
            results = await asyncio.gather(
                *(
                    self._model_versions(pack, language, budget, span, flags)
                    for language in run.llm_languages
                )
            )
            for language, (accepted, outcome) in zip(run.llm_languages, results, strict=True):
                for mode in ("analyst", "casual"):
                    if mode in accepted:
                        written[(language, mode)] = accepted[mode]
                    elif outcome != "skipped":
                        run.flag("writer_fallback", flags)
        templates = {(t.language, t.mode): t.text for t in render_texts(pack)}
        texts: list[CommentaryText] = []
        for language, mode in VERSIONS:
            if (language, mode) in written:
                texts.append(
                    CommentaryText(
                        language=language, mode=mode, text=written[(language, mode)], source="llm"
                    )
                )
                continue
            text = self._template_text(pack, language, mode, templates[(language, mode)], span)
            texts.append(CommentaryText(language=language, mode=mode, text=text, source="template"))
        keys = list(pack.headline)
        for item_text in texts:
            keys += [k for k in cited_keys(item_text.text, pack.facts) if k not in keys]
        facts = _facts_by_key(pack, keys)
        rules = mentioned_rules(trigger.rules, [t.text for t in texts])
        item = CommentaryItem(
            id=f"{trigger.id}-c",
            match_id=trigger.match_id,
            trigger_id=trigger.id,
            trigger_kind=trigger.kind,
            importance=trigger.importance,
            period=trigger.period,
            clock_ms=trigger.clock_ms,
            display_minute=trigger.display_minute,
            as_of_seq=pack.as_of_seq,
            team=trigger.team,
            player=trigger.player,
            involved_players=trigger.involved_players,
            texts=tuple(texts),
            facts=facts,
            evidence_event_ids=_evidence(trigger.trigger_event_ids, facts),
            rules=rules,
            first_rules=tuple(rule for rule in pack.first_rules if rule in rules),
            excitement=excitement(pack),
            speak=speak(pack),
            degraded=tuple(flags),
        )
        span.finish(
            {"sources": {f"{t.language}/{t.mode}": t.source for t in texts}},
            status="fallback" if "writer_fallback" in flags else "ok",
        )
        return BranchResult(branch="commentary", commentary=item)

    # ------------------------------------------------------------------ StudioPublisher

    def publish(self, results: Sequence[BranchResult]) -> StudioOutput:
        """Merge the branch results in a fixed order, whichever branch finished first."""
        run = self.run
        by_branch = {result.branch: result for result in results}
        commentary = by_branch["commentary"].commentary
        if commentary is None:
            raise RuntimeError("the commentary branch produced no item")
        talking_point = by_branch.get("talking_point", BranchResult(branch="talking_point"))
        lower_third = by_branch.get("lower_third", BranchResult(branch="lower_third"))
        run.trace.event(
            "StudioPublisher",
            "agent",
            "publish",
            output={
                "commentary": commentary.id,
                "talking_point": talking_point.talking_point is not None,
                "lower_third": lower_third.lower_third is not None,
                "degraded": list(run.degraded),
            },
        )
        run.timings_ms["total"] = round(run.trace.elapsed_ms(), 3)
        return StudioOutput(
            trigger_id=run.job.trigger.id,
            commentary=commentary,
            talking_point=talking_point.talking_point,
            lower_third=lower_third.lower_third,
            degraded=tuple(run.degraded),
            timings_ms=dict(run.timings_ms),
            trace=run.trace.spans,
        )


async def run_studio_pipeline(steps: StudioSteps, job: StudioJob) -> StudioOutput:
    """The workflow graph as a plain asyncio program (fallback runner, section 14.1)."""
    pack = steps.build_pack(steps.intake(job))
    results = await asyncio.gather(
        steps.talking_point(pack), steps.lower_third(pack), steps.commentary(pack)
    )
    return steps.publish(results)
