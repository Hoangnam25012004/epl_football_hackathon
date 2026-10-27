"""The progressive claim (docs/day7-design.md section 2.4).

rev.1 comes from the rules at once: the claim workflow without a model (`verify_rules`). The
model pass then runs the one-pass workflow (`verify`) on a single worker, so a CPU model never
runs two claims at once, and its result becomes rev.2. rev.2 is therefore exactly what the
one-pass workflow answers, which the eval measures: the rules win whenever they read the claim
(Day 2), and only a claim the rules could not read may change its verdict.

A full queue, an open breaker or a model that gives nothing usable never changes the answer:
the claim says why the model did not add to it (`skip_reason`).
"""

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pundit.agents.contracts import ClaimRequest, ClaimResult, SkipReason
from pundit.orchestration.resilience import Clock
from pundit.orchestration.service import ClaimService, Listen, MatchSource

log = logging.getLogger("pundit.claims")

Publish = Callable[[ClaimResult], None]

# INSUFFICIENT_DATA because the rule parser could not read the claim: the model may read it
READ_FAILURES = frozenset({"unsupported_claim", "unknown_entity", "ambiguous_entity"})


def rules_could_not_read(rev1: ClaimResult) -> bool:
    return rev1.verdict == "INSUFFICIENT_DATA" and rev1.reason_code in READ_FAILURES


def skipped(rev1: ClaimResult, reason: SkipReason) -> ClaimResult:
    """rev.1 when no model pass will run."""
    return rev1.model_copy(update={"model_pass": "skipped", "skip_reason": reason})


def skip_reason(passed: ClaimResult) -> SkipReason:
    """Why a model pass added nothing, in the words a viewer reads (never "too slow" for a
    model whose texts the guardrail refused)."""
    if "circuit_open" in passed.degraded:
        return "circuit_open"
    spans = passed.trace or ()
    if any(s.kind == "guardrail" and s.status == "rejected" for s in spans):
        return "rejected"
    return "no_answer"


def second_revision(rev1: ClaimResult, passed: ClaimResult | None, pass_ms: float) -> ClaimResult:
    """rev.2 from the one-pass result of the model pass (None: the pass itself failed)."""
    base: dict[str, Any] = {"revision": 2, "rev1_verdict": rev1.verdict}
    if passed is None:
        return rev1.model_copy(update={**base, "model_pass": "skipped", "skip_reason": "no_answer"})
    timings = {**passed.timings_ms, "model_pass": round(pass_ms, 1)}
    if passed.verdict != rev1.verdict and not rules_could_not_read(rev1):
        # cannot happen while the workflow keeps the Day 2 rule; if it ever does, the rules win
        log.warning("model pass of %s changed a verdict the rules had read", rev1.claim_id)
        degraded = (*rev1.degraded, "analyst_disagreement")
        return rev1.model_copy(
            update={**base, "model_pass": "done", "degraded": degraded, "timings_ms": timings}
        )
    model_added = (
        passed.analyst_source == "llm"
        or passed.explanation_source == "llm"
        or "analyst_disagreement" in passed.degraded
    )
    if not model_added:  # the model failed, was too slow or was rejected: rev.1 stands
        return rev1.model_copy(
            update={
                **base,
                "model_pass": "skipped",
                "skip_reason": skip_reason(passed),
                "timings_ms": timings,
                "trace": passed.trace,  # the drawer shows what the model pass did
            }
        )
    return passed.model_copy(
        update={**base, "model_pass": "done", "skip_reason": None, "timings_ms": timings}
    )


@dataclass
class _Job:
    request: ClaimRequest
    source: MatchSource
    rev1: ClaimResult
    publish: Publish
    listen: Listen | None = None
    service: ClaimService | None = None  # a session's own (Chaos), else the queue's


class ModelPassQueue:
    """One worker for every session: claims wait their turn, at most `max_waiting` of them."""

    def __init__(
        self,
        service: ClaimService,
        *,
        max_waiting: int,
        budget_s: float,
        clock: Clock = time.monotonic,
    ) -> None:
        self.service = service
        self.max_waiting = max_waiting
        self.budget_s = budget_s
        self._clock = clock
        self._waiting: deque[_Job] = deque()
        self._busy = False
        self._task: asyncio.Task[None] | None = None
        self.counts = {"queued": 0, "done": 0, "skipped": 0, "busy": 0}

    @property
    def waiting(self) -> int:
        return len(self._waiting)

    def submit(
        self,
        rev1: ClaimResult,
        request: ClaimRequest,
        source: MatchSource,
        publish: Publish,
        listen: Listen | None = None,
        service: ClaimService | None = None,
    ) -> ClaimResult:
        """rev.1 as it is published now: queued, or skipped with the reason."""
        if (service or self.service).breaker.state == "open":
            self.counts["skipped"] += 1
            return skipped(rev1, "circuit_open")
        if self._busy and len(self._waiting) >= self.max_waiting:
            self.counts["busy"] += 1
            return skipped(rev1, "busy")
        # the model reads the same shared state as the rules (pinned at rev.1's event), and its
        # trace is kept: rev.2 shows what the pass did, even when it adds nothing
        pinned = request.model_copy(
            update={"as_of_seq": rev1.as_of_seq if request.as_of_seq is None else request.as_of_seq,
                    "include_trace": True}
        )  # fmt: skip
        first = rev1.model_copy(update={"model_pass": "queued"})
        self._waiting.append(_Job(pinned, source, first, publish, listen, service))
        self.counts["queued"] += 1
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._drain(), name="model-pass")
        return first

    async def _drain(self) -> None:
        while self._waiting:
            job = self._waiting.popleft()
            self._busy = True
            try:
                await self._run(job)
            finally:
                self._busy = False

    async def _run(self, job: _Job) -> None:
        rev1 = job.rev1
        job.publish(rev1.model_copy(update={"model_pass": "running"}))
        began = self._clock()
        passed: ClaimResult | None
        try:
            passed = await (job.service or self.service).verify(
                job.request,
                job.source,
                claim_id=rev1.claim_id,
                budget_s=self.budget_s,
                listen=job.listen,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # verify answers every failure itself; this is a bug, not a model
            log.exception("model pass of %s failed", rev1.claim_id)
            passed = None
        rev2 = second_revision(rev1, passed, (self._clock() - began) * 1000)
        self.counts["done" if rev2.model_pass == "done" else "skipped"] += 1
        job.publish(rev2)

    def stats(self) -> dict[str, Any]:
        return {"waiting": len(self._waiting), "running": self._busy, **self.counts}

    async def aclose(self) -> None:
        self._waiting.clear()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
