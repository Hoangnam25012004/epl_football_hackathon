"""RecapService (docs/day3-design.md section 10): the template recap at once (revision 1), then
the versions a model wrote (revision 2) when they are ready and pass the Guardrail.

A version (language x register) replaces its template only when its title and every paragraph
pass; the others keep the template. The four versions are written one after the other, most
watched first (Vietnamese casual, Vietnamese analyst, English analyst, English casual), so a
slow local model spends its budget where it matters. The recap has its own breaker: a slow
recap must not switch off the real-time studio, and the reverse.
"""

import logging
import time
from collections.abc import Callable, Sequence
from functools import partial
from typing import Literal

from pundit.agents.contracts import (
    Degraded,
    GuardrailViolation,
    Lang,
    Recap,
    RecapDraft,
    RecapText,
    Register,
)
from pundit.agents.guardrail import feedback_lines
from pundit.agents.recap import RecapPack, build_recap, recap_scope, render_recap
from pundit.agents.studio_guard import StudioScope, check_studio
from pundit.agents.studio_text import score_line
from pundit.agents.trace import SpanHandle, TraceRecorder
from pundit.llm.faults import FaultPlan
from pundit.llm.mock import MockRecapWriter
from pundit.llm.ports import (
    ContentFiltered,
    LlmError,
    LlmOutputError,
    LlmTimeout,
    RecapWriterPort,
)
from pundit.llm.settings import Settings
from pundit.llm.studio_prompts import RECAP_PROMPT_VERSION
from pundit.orchestration.resilience import Budget, CircuitBreaker, Clock, with_timeout

log = logging.getLogger("pundit.recap")

RecapWriterFactory = Callable[[], RecapWriterPort | None]
VersionOutcome = Literal["skipped", "failed", "rejected"]
TEXT_ORDER: tuple[tuple[Lang, Register], ...] = (
    ("vi", "analyst"),
    ("vi", "casual"),
    ("en", "analyst"),
    ("en", "casual"),
)  # the order of Recap.texts, as in the commentary
PRIORITY: tuple[tuple[Lang, Register], ...] = (
    ("vi", "casual"),
    ("vi", "analyst"),
    ("en", "analyst"),
    ("en", "casual"),
)  # the order in which a model writes them
MAX_LLM_ATTEMPTS = 2
MIN_LLM_S = 0.5
FALLBACK_PARAGRAPH: dict[Lang, str] = {"vi": "Tỷ số: {score}.", "en": "Score: {score}."}


def recap_mock_writer(settings: Settings) -> RecapWriterFactory:
    plan = FaultPlan.parse(settings.mock_llm_faults)
    latency = settings.mock_llm_latency_ms / 1000
    return lambda: MockRecapWriter(plan.counter(), latency_s=latency)


def recap_llm_writer(settings: Settings) -> RecapWriterFactory:
    from pundit.llm.maf_backend import LlmRecapWriter, build_backend

    writer = LlmRecapWriter(build_backend(settings))
    return lambda: writer


def default_recap_writer(settings: Settings) -> RecapWriterFactory:
    return (
        recap_mock_writer(settings) if settings.provider == "mock" else recap_llm_writer(settings)
    )


def check_parts(
    parts: Sequence[str],
    scope: StudioScope,
    *,
    language: Lang,
    mode: Register,
    model_text: bool,
) -> list[GuardrailViolation]:
    """The title and the paragraphs of a recap version, each checked on its own."""
    found: list[GuardrailViolation] = []
    for part in parts:
        report = check_studio(
            part,
            scope,
            language=language,
            product="recap",
            mode=mode,
            part="paragraph",
            model_text=model_text,
        )
        found += report.violations
    return found


class RecapService:
    def __init__(
        self,
        settings: Settings,
        *,
        writer: RecapWriterFactory | None = None,
        breaker: CircuitBreaker | None = None,
        budget_s: float | None = None,
        clock: Clock = time.monotonic,
        trace_clock: Clock = time.perf_counter,
    ) -> None:
        self.settings = settings
        self._writer = writer or default_recap_writer(settings)
        self.breaker = breaker or CircuitBreaker(clock=clock)
        self.budget_s = budget_s or settings.recap_budget_s
        self._clock = clock
        self._trace_clock = trace_clock

    def template(self, pack: RecapPack) -> Recap:
        """Revision 1. A template paragraph the Guardrail rejects (a bug) is left out."""
        scope = recap_scope(pack)
        texts: list[RecapText] = []
        for language, mode in TEXT_ORDER:
            text = render_recap(pack, language, mode)
            kept: list[str] = []
            for paragraph in text.paragraphs:
                bad = check_parts(
                    [paragraph], scope, language=language, mode=mode, model_text=False
                )
                if bad:
                    log.error("recap template paragraph rejected: %s", feedback_lines(bad))
                    continue
                kept.append(paragraph)
            if not kept:
                kept = [FALLBACK_PARAGRAPH[language].format(score=score_line(pack.match, language))]
            texts.append(text.model_copy(update={"paragraphs": tuple(kept)}))
        return build_recap(pack, texts)

    async def improve(self, pack: RecapPack, current: Recap) -> tuple[Recap | None, TraceRecorder]:
        """The next revision with every version a model wrote and the Guardrail passed, or None
        when no version passed (the template stays)."""
        trace = TraceRecorder(current.id, clock=self._trace_clock)
        root = trace.start("RecapWriter", "agent", "recap", {"scope": pack.scope})
        writer = self._writer()
        if writer is None:
            root.finish({"skipped": "deterministic mode"})
            return None, trace
        scope = recap_scope(pack)
        budget = Budget(self.budget_s, clock=self._clock)
        written: dict[tuple[Lang, Register], RecapText] = {}
        flags: list[Degraded] = []
        for language, mode in PRIORITY:
            text = await self._version(writer, pack, scope, language, mode, budget, trace, root)
            if isinstance(text, RecapText):
                written[(language, mode)] = text
                continue
            flag: Degraded = "llm_skipped" if text == "skipped" else "writer_fallback"
            if flag not in flags:
                flags.append(flag)
        if not written:
            root.finish({"published": False, "degraded": list(flags)}, status="fallback")
            return None, trace
        texts = [written.get((t.language, t.mode), t) for t in current.texts]
        recap = build_recap(pack, texts, revision=current.revision + 1, degraded=flags)
        root.finish(
            {"published": True, "llm_versions": [f"{lang}/{mode}" for lang, mode in written]},
            status="fallback" if flags else "ok",
        )
        return recap, trace

    async def _version(
        self,
        writer: RecapWriterPort,
        pack: RecapPack,
        scope: StudioScope,
        language: Lang,
        mode: Register,
        budget: Budget,
        trace: TraceRecorder,
        root: SpanHandle,
    ) -> RecapText | VersionOutcome:
        """One version that passed, or why there is none."""
        feedback: list[GuardrailViolation] | None = None
        for attempt in range(1, MAX_LLM_ATTEMPTS + 1):
            timeout = min(self.settings.llm_timeout_s, budget.remaining())
            reason = None
            if timeout < MIN_LLM_S:
                reason = "budget"
            elif not self.breaker.allow():
                reason = "circuit_open"
            if reason is not None:
                trace.event(
                    "RecapWriter",
                    "fallback",
                    "skip_llm",
                    input={"language": language, "mode": mode},
                    output={"reason": reason},
                    status="fallback",
                    parent=root,
                )
                return "skipped" if attempt == 1 else "rejected"
            span = trace.start(
                "RecapWriter",
                "llm",
                f"recap_{language}_{mode}",
                {
                    "prompt_version": RECAP_PROMPT_VERSION,
                    "feedback": feedback_lines(feedback or []),
                },
                attempt=attempt,
                model=writer.model,
                parent=root,
            )
            try:
                draft: RecapDraft = await with_timeout(
                    partial(writer.write, pack, language, mode, feedback), timeout
                )
            except ContentFiltered as exc:
                self.breaker.record_success()  # the provider answered; the text was the problem
                span.finish(status="rejected", error=str(exc))
                return "rejected"
            except LlmError as exc:
                self.breaker.record_failure()
                failure = "timeout" if isinstance(exc, LlmTimeout) else "error"
                details = exc.errors[:3] if isinstance(exc, LlmOutputError) else []
                span.finish(
                    {"failure": failure, "errors": details}, status="error", error=str(exc)[:300]
                )
                return "failed"
            except Exception as exc:  # an adapter bug must not lose the recap
                self.breaker.record_failure()
                log.exception("recap writer crashed")
                span.finish(status="error", error=f"{type(exc).__name__}: {exc}"[:300])
                return "failed"
            self.breaker.record_success()
            violations = check_parts(
                [draft.title, *draft.paragraphs],
                scope,
                language=language,
                mode=mode,
                model_text=True,
            )
            check = trace.start(
                "Guardrail",
                "guardrail",
                "check_studio",
                {
                    "agent": "RecapWriter",
                    "language": language,
                    "mode": mode,
                    "texts": [draft.title, *draft.paragraphs],
                },
                attempt=attempt,
                parent=span,
            )
            if not violations:
                check.finish({"passed": True})
                span.finish({"paragraphs": len(draft.paragraphs)})
                return RecapText(
                    language=language,
                    mode=mode,
                    title=draft.title,
                    paragraphs=draft.paragraphs,
                    source="llm",
                )
            check.finish(
                {"passed": False, "violations": [v.model_dump(mode="json") for v in violations]},
                status="rejected",
            )
            span.finish(
                {"rejected": len(violations)},
                status="retry" if attempt < MAX_LLM_ATTEMPTS else "rejected",
            )
            feedback = violations
        return "rejected"
