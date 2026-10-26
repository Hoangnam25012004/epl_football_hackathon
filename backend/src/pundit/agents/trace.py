"""Agent trace (docs/day2-design.md section 12): which agent called which tool, with what result.

Span ids are deterministic (`{claim_id}-s{k:02d}`, numbered when a span starts). Times come
from an injected monotonic clock, relative to the start of the claim. Inputs and outputs are
summaries of at most 2 KB with secrets removed; prompts are recorded by version and length only.
"""

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from pundit.agents.contracts import SpanKind, SpanStatus, TraceSpan

log = logging.getLogger("pundit.trace")


class TraceListener(Protocol):
    """Told when a span starts and ends (Day 7: the live Agent Ops view). It must not raise; a
    listener that does is logged and the run goes on."""

    def started(self, handle: "SpanHandle") -> None: ...

    def finished(self, span: TraceSpan) -> None: ...


MAX_SUMMARY_BYTES = 2048
SECRET_KEYS = frozenset(
    {
        "api_key", "apikey", "token", "access_token", "github_token", "foundry_api_key",
        "authorization", "headers", "password", "secret", "client_secret", "subscription_key",
    }
)  # fmt: skip


def redact(value: Any) -> Any:
    """Drop every mapping entry whose name looks like a secret, recursively."""
    if isinstance(value, Mapping):
        return {
            str(k): redact(v)
            for k, v in value.items()
            if str(k).lower().replace("-", "_") not in SECRET_KEYS
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


def summarize(value: Mapping[str, Any]) -> dict[str, Any]:
    clean = redact(value)
    text = json.dumps(clean, ensure_ascii=False, default=str, sort_keys=True)
    if len(text.encode("utf-8")) <= MAX_SUMMARY_BYTES:
        loaded: dict[str, Any] = json.loads(text)
        return loaded
    return {
        "truncated": True,
        "preview": text.encode("utf-8")[: MAX_SUMMARY_BYTES - 64].decode("utf-8", "ignore"),
    }


@dataclass
class SpanHandle:
    recorder: "TraceRecorder"
    span_id: str
    parent_id: str | None
    agent: str
    kind: SpanKind
    name: str
    input: dict[str, Any]
    attempt: int
    model: str | None
    started: float
    done: bool = field(default=False)

    def finish(
        self,
        output: Mapping[str, Any] | None = None,
        *,
        status: SpanStatus = "ok",
        error: str | None = None,
    ) -> TraceSpan:
        if self.done:
            raise RuntimeError(f"span {self.span_id} finished twice")
        self.done = True
        return self.recorder._close(self, summarize(output or {}), status, error)


class TraceRecorder:
    """Collects the spans of one claim. Not shared between claims."""

    def __init__(
        self,
        trace_id: str,
        *,
        clock: Callable[[], float] = time.perf_counter,
        listener: TraceListener | None = None,
    ) -> None:
        self.trace_id = trace_id
        self._clock = clock
        self._origin = clock()
        self._count = 0
        self._spans: dict[str, TraceSpan] = {}
        self._order: list[str] = []
        self._listener = listener

    def _tell(self, call: Callable[[], None]) -> None:
        try:
            call()
        except Exception:  # the view of a run never breaks the run
            log.exception("trace listener failed on %s", self.trace_id)

    def elapsed_ms(self) -> float:
        return (self._clock() - self._origin) * 1000

    def since_ms(self, handle: "SpanHandle") -> float:
        """Milliseconds since `handle` started (whether or not it is finished)."""
        return (self._clock() - handle.started) * 1000

    def start(
        self,
        agent: str,
        kind: SpanKind,
        name: str,
        input: Mapping[str, Any] | None = None,
        *,
        attempt: int = 1,
        model: str | None = None,
        parent: SpanHandle | None = None,
    ) -> SpanHandle:
        self._count += 1
        span_id = f"{self.trace_id}-s{self._count:02d}"
        self._order.append(span_id)
        handle = SpanHandle(
            recorder=self,
            span_id=span_id,
            parent_id=parent.span_id if parent is not None else None,
            agent=agent,
            kind=kind,
            name=name,
            input=summarize(input or {}),
            attempt=attempt,
            model=model,
            started=self._clock(),
        )
        if self._listener is not None:
            listener = self._listener
            self._tell(lambda: listener.started(handle))
        return handle

    def _close(
        self, handle: SpanHandle, output: dict[str, Any], status: SpanStatus, error: str | None
    ) -> TraceSpan:
        now = self._clock()
        span = TraceSpan(
            trace_id=self.trace_id,
            span_id=handle.span_id,
            parent_id=handle.parent_id,
            agent=handle.agent,
            kind=handle.kind,
            name=handle.name,
            input=handle.input,
            output=output,
            status=status,
            attempt=handle.attempt,
            start_ms=(handle.started - self._origin) * 1000,
            duration_ms=(now - handle.started) * 1000,
            model=handle.model,
            error=error,
        )
        self._spans[handle.span_id] = span
        if self._listener is not None:
            listener = self._listener
            self._tell(lambda: listener.finished(span))
        return span

    def event(
        self,
        agent: str,
        kind: SpanKind,
        name: str,
        *,
        input: Mapping[str, Any] | None = None,
        output: Mapping[str, Any] | None = None,
        status: SpanStatus = "ok",
        attempt: int = 1,
        model: str | None = None,
        error: str | None = None,
        parent: SpanHandle | None = None,
    ) -> TraceSpan:
        """A span that starts and ends now (a decision, a fallback switch)."""
        handle = self.start(agent, kind, name, input, attempt=attempt, model=model, parent=parent)
        return handle.finish(output, status=status, error=error)

    @property
    def spans(self) -> tuple[TraceSpan, ...]:
        """Finished spans in start order (unfinished spans are left out)."""
        return tuple(self._spans[s] for s in self._order if s in self._spans)
