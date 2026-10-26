"""Agent Ops (docs/day7-design.md section 2.5): the agents of a session, live.

Every run of the session (a claim's rules pass and model pass, a studio job, a cue of the
narrator) gives this object a trace listener. Each span that starts or ends becomes an
`AgentEvent`: which agent, the typed message it hands on, the shared state the run reads
(`as_of`), the breaker, the guardrail's codes when it rejected a text. Tool calls are counted,
not streamed. The events feed per-agent statistics (`GET /matches/{id}/agents`) and the clients
of `/ws/agents/{id}`, in batches at most every 100 ms: a busy match never floods a viewer, and
the match's own WebSocket (and its message numbers) is untouched.
"""

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pundit.agents.contracts import (
    AgentBreaker,
    AgentEvent,
    AgentEventBatch,
    AgentOpsMessage,
    AgentOpsSnapshot,
    AgentSource,
    AgentStat,
    AgentWorkflow,
    ChaosStatus,
    SpanStatus,
    TraceSpan,
)
from pundit.agents.trace import SpanHandle
from pundit.domain.percentiles import p95

log = logging.getLogger("pundit.agent_ops")

FLUSH_S = 0.1  # at most 10 batches a second per client
MAX_BATCH = 40  # the newest events of a batch; the rest are counted as dropped
RECENT = 60  # per workflow
DURATIONS = 200
CLIENT_QUEUE = 64
SHOWN_KINDS = frozenset({"agent", "llm", "guardrail", "fallback"})
# the typed message each agent hands on (the edges of the workflows)
HANDOFFS: dict[tuple[AgentWorkflow, str], str] = {
    ("claim", "ClaimIntake"): "ClaimContext",
    ("claim", "ClaimAnalyst"): "AnalystOutcome",
    ("claim", "RuleAnalyst"): "ClaimAnalysis",
    ("claim", "EvidenceCollector"): "EvidenceBundle",
    ("claim", "Judge"): "ExplainRequest | TemplateRequest",
    ("claim", "Explainer"): "DraftForReview",
    ("claim", "TemplateExplainer"): "DraftForReview",
    ("claim", "Guardrail"): "Publication",
    ("claim", "Publisher"): "ClaimResult",
    ("studio", "StudioIntake"): "StudioPlan",
    ("studio", "FactPackBuilder"): "FactPack",
    ("studio", "TalkingPointWriter"): "BranchResult",
    ("studio", "GraphicsDesigner"): "BranchResult",
    ("studio", "Commentator"): "BranchResult",
    ("studio", "StudioPublisher"): "StudioOutput",
    ("narrator", "Narrator"): "SpeechCue",
}
WRITERS = frozenset(
    {"Explainer", "TemplateExplainer", "TalkingPointWriter", "GraphicsDesigner", "Commentator"}
)


@dataclass
class _Stat:
    workflow: AgentWorkflow
    agent: str
    calls: int = 0
    fallbacks: int = 0
    rejected: int = 0
    errors: int = 0
    durations: deque[float] = field(default_factory=lambda: deque(maxlen=DURATIONS))
    sources: dict[str, int] = field(default_factory=dict)
    last_status: SpanStatus | None = None
    last_run_id: str | None = None
    last_input: dict[str, Any] = field(default_factory=dict)
    last_output: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> AgentStat:
        return AgentStat(
            workflow=self.workflow,
            agent=self.agent,
            calls=self.calls,
            fallbacks=self.fallbacks,
            rejected=self.rejected,
            errors=self.errors,
            p95_ms=None if not self.durations else round(p95(self.durations) or 0.0, 1),
            sources=dict(self.sources),
            last_status=self.last_status,
            last_run_id=self.last_run_id,
            last_input=self.last_input,
            last_output=self.last_output,
        )


def guardrail_codes(span: TraceSpan) -> tuple[str, ...]:
    if span.status != "rejected":
        return ()
    found = span.output.get("violations") or span.output.get("codes") or ()
    codes = []
    for item in found if isinstance(found, list | tuple) else ():
        code = item.get("code") if isinstance(item, dict) else item
        if isinstance(code, str) and code not in codes:
            codes.append(code)
    return tuple(codes)


def _seq(event: AgentEvent) -> int:
    return event.seq


def trim(pending: list[AgentEvent]) -> tuple[AgentEvent, ...]:
    """At most MAX_BATCH events, in order. The studio makes most of them (a backlog after a fast
    replay); a claim or a cue the viewer just asked for is never the one left out."""
    if len(pending) <= MAX_BATCH:
        return tuple(pending)
    kept = [e for e in pending if e.workflow != "studio"][-MAX_BATCH:]
    room = MAX_BATCH - len(kept)
    studio = [e for e in pending if e.workflow == "studio"]
    keep = {id(e) for e in kept} | {id(e) for e in (studio[-room:] if room else [])}
    return tuple(e for e in pending if id(e) in keep)


class RunListener:
    """The trace listener of one run."""

    def __init__(
        self,
        ops: "AgentOps",
        workflow: AgentWorkflow,
        run_id: str,
        as_of_seq: int | None,
        breaker: Callable[[], AgentBreaker] | None,
    ) -> None:
        self.ops = ops
        self.workflow = workflow
        self.run_id = run_id
        self.as_of_seq = as_of_seq
        self.breaker = breaker
        self.model_answered: set[str] = set()  # agents whose model call ended well

    def started(self, handle: SpanHandle) -> None:
        if handle.kind in SHOWN_KINDS:
            self.ops.emit(self, handle.agent, handle.kind, handle.name, "start", model=handle.model)

    def finished(self, span: TraceSpan) -> None:
        if span.kind == "llm" and span.status == "ok":
            self.model_answered.add(span.agent)
        if span.kind not in SHOWN_KINDS:
            self.ops.count_tool(self.workflow, span)
            return
        source = self.source_of(span)
        self.ops.record(self.workflow, span, source)
        self.ops.emit(
            self, span.agent, span.kind, span.name, "end",
            status=span.status, source=source, duration_ms=round(span.duration_ms, 1),
            guardrail=guardrail_codes(span), model=span.model,
        )  # fmt: skip

    def source_of(self, span: TraceSpan) -> AgentSource | None:
        if span.kind == "llm":
            return "llm_live"
        if span.kind == "fallback" or span.agent == "TemplateExplainer":
            return "template"
        if span.kind == "guardrail":
            return None
        if span.agent in WRITERS:
            return "llm_live" if span.agent in self.model_answered else "template"
        if span.agent == "ClaimAnalyst":
            return "llm_live" if span.agent in self.model_answered else "rules"
        return "rules"


class AgentOps:
    def __init__(
        self,
        match_id: str,
        *,
        display_of: Callable[[int], str],
        breakers: dict[str, Callable[[], AgentBreaker]] | None = None,
        chaos: Callable[[], ChaosStatus] | None = None,
    ) -> None:
        self.match_id = match_id
        self._display_of = display_of
        self._breakers = breakers or {}
        self._chaos = chaos
        self._seq = 0
        self._stats: dict[tuple[AgentWorkflow, str], _Stat] = {}
        # per workflow: a busy studio never pushes the claims out of the snapshot
        self._recent: dict[AgentWorkflow, deque[AgentEvent]] = {}
        self._pending: list[AgentEvent] = []
        self._clients: set[asyncio.Queue[AgentOpsMessage]] = set()
        self._flush: asyncio.Task[None] | None = None
        self._last_breaker: dict[str, AgentBreaker] = {}
        self._cued: deque[str] = deque(maxlen=512)

    # ------------------------------------------------------------------ runs

    def listener(
        self, workflow: AgentWorkflow, run_id: str, as_of_seq: int | None = None
    ) -> RunListener:
        return RunListener(self, workflow, run_id, as_of_seq, self._breakers.get(workflow))

    def cued(self, item_id: str, *, as_of_seq: int | None, voice: str) -> None:
        """The narrator was given a line of this session (once per item)."""
        if item_id in self._cued:
            return
        self._cued.append(item_id)
        run = self.listener("narrator", item_id, as_of_seq)
        span = TraceSpan(
            trace_id=item_id, span_id=f"{item_id}-cue", parent_id=None, agent="Narrator",
            kind="agent", name="cue", input={"item": item_id}, output={"voice": voice},
            status="ok", attempt=1, start_ms=0.0, duration_ms=0.0, model=None, error=None,
        )  # fmt: skip
        self.record("narrator", span, "template")
        self.emit(run, "Narrator", "agent", "cue", "end", status="ok", source="template")

    # ------------------------------------------------------------------ events

    def emit(
        self,
        run: RunListener,
        agent: str,
        kind: Any,
        name: str,
        phase: Any,
        **values: Any,
    ) -> None:
        breaker = run.breaker() if run.breaker is not None else None
        if breaker is not None and self._last_breaker.get(run.workflow) != breaker:
            known = run.workflow in self._last_breaker
            self._last_breaker[run.workflow] = breaker
            if known:  # a change, not the first sight
                self._push(
                    AgentEvent(
                        seq=self._next(),
                        run_id=run.run_id,
                        workflow=run.workflow,
                        agent="CircuitBreaker",
                        kind="fallback",
                        name=breaker,
                        phase="end",
                        status="ok" if breaker == "closed" else "fallback",
                        breaker=breaker,
                    )
                )
        as_of = run.as_of_seq
        self._push(
            AgentEvent(
                seq=self._next(),
                run_id=run.run_id,
                workflow=run.workflow,
                agent=agent,
                kind=kind,
                name=name,
                phase=phase,
                handoff=HANDOFFS.get((run.workflow, agent)) if phase == "end" else None,
                as_of_seq=as_of,
                as_of_display=self._display_of(as_of) if as_of is not None else None,
                breaker=breaker,
                **values,
            )
        )

    def record(self, workflow: AgentWorkflow, span: TraceSpan, source: AgentSource | None) -> None:
        stat = self._stat(workflow, span.agent)
        stat.calls += 1
        stat.durations.append(span.duration_ms)
        stat.fallbacks += span.status == "fallback"
        stat.rejected += span.status == "rejected"
        stat.errors += span.status == "error"
        if source is not None:
            stat.sources[source] = stat.sources.get(source, 0) + 1
        stat.last_status, stat.last_run_id = span.status, span.trace_id
        stat.last_input, stat.last_output = span.input, span.output

    def count_tool(self, workflow: AgentWorkflow, span: TraceSpan) -> None:
        """A tool call (a Stats Tool): counted on its agent, not streamed. It ends before its
        agent's span does, so the agent's row may not exist yet."""
        stat = self._stat(workflow, span.agent)
        stat.sources["tools"] = stat.sources.get("tools", 0) + 1

    def _stat(self, workflow: AgentWorkflow, agent: str) -> _Stat:
        stat = self._stats.get((workflow, agent))
        if stat is None:
            stat = self._stats[(workflow, agent)] = _Stat(workflow, agent)
        return stat

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    def _push(self, event: AgentEvent) -> None:
        recent = self._recent.get(event.workflow)
        if recent is None:
            recent = self._recent[event.workflow] = deque(maxlen=RECENT)
        recent.append(event)
        if not self._clients:
            return
        self._pending.append(event)
        if self._flush is None or self._flush.done():
            with contextlib.suppress(RuntimeError):  # no running loop: nobody to send to
                self._flush = asyncio.get_running_loop().create_task(self._send_soon())

    async def _send_soon(self) -> None:
        await asyncio.sleep(FLUSH_S)
        pending, self._pending = self._pending, []
        events = trim(pending)
        batch = AgentOpsMessage(
            type="events",
            payload=AgentEventBatch(events=events, dropped=len(pending) - len(events)),
        )
        for queue in list(self._clients):
            if queue.full():  # a slow viewer loses its oldest batch, never blocks the match
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(batch)

    # ------------------------------------------------------------------ clients

    def subscribe(self) -> asyncio.Queue[AgentOpsMessage]:
        queue: asyncio.Queue[AgentOpsMessage] = asyncio.Queue(maxsize=CLIENT_QUEUE)
        self._clients.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[AgentOpsMessage]) -> None:
        self._clients.discard(queue)

    @property
    def clients(self) -> int:
        return len(self._clients)

    def snapshot(self) -> AgentOpsSnapshot:
        return AgentOpsSnapshot(
            match_id=self.match_id,
            agents=tuple(stat.snapshot() for stat in self._stats.values()),
            breakers={name: state() for name, state in self._breakers.items()},
            chaos=self._chaos() if self._chaos is not None else None,
            recent=tuple(sorted((e for r in self._recent.values() for e in r), key=_seq)),
        )

    async def aclose(self) -> None:
        if self._flush is not None and not self._flush.done():
            self._flush.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush
