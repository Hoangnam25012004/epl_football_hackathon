"""StudioDirector (docs/day3-design.md sections 14.4 and 14.5): the studio crew of one session.

The replay offers each trigger after it has broadcast the trigger's event and moment, and
`offer` never waits (put_nowait). One worker runs the jobs in trigger order on views pinned at
each trigger's seq, stores the items and hands them to the session for publishing, so an item
always follows the event that caused it (principle S8) and the items of one trigger keep a
fixed order (commentary, talking point, lower-third). Under load, new importance-1 triggers are
dropped once the queue reaches STUDIO_DROP_AT; importance >= 2 is always taken. Recaps have a
worker of their own: the template is published at once, a model's version when it is ready.
"""

import asyncio
import contextlib
import logging
import statistics
import time
from collections import Counter, OrderedDict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pundit.agents.contracts import (
    CommentaryItem,
    LowerThird,
    Moment,
    Recap,
    RecapScope,
    RuleId,
    StudioJob,
    TalkingPoint,
    TalkingPointStatus,
    TraceSpan,
    Trigger,
)
from pundit.agents.recap import RECAP_SCOPES, RecapPack, build_recap_pack
from pundit.domain.log import LogView
from pundit.orchestration.recap_service import RecapService
from pundit.orchestration.studio_service import StudioService

log = logging.getLogger("pundit.studio")

StudioKind = Literal["commentary", "talking_point", "lower_third", "recap"]
StudioItem = CommentaryItem | TalkingPoint | LowerThird | Recap
MAX_TALKING_POINTS = 200
MAX_LOWER_THIRDS = 100
MAX_COMMENTARY = 500
MAX_TRACES = 300
LAGS_KEPT = 500


class StudioHost(Protocol):
    """What the director needs from its session."""

    @property
    def moments(self) -> list[Moment]: ...

    def view_at(self, seq: int) -> LogView: ...

    def publish_studio(self, kind: StudioKind, item: StudioItem) -> None: ...


@dataclass
class StudioCrew:
    """The services every session's director shares (one breaker each for the whole app)."""

    service: StudioService
    recaps: RecapService
    queue_size: int = 512
    drop_at: int = 256


def _put[T](store: "OrderedDict[str, T]", key: str, item: T, limit: int) -> None:
    store[key] = item
    store.move_to_end(key)
    while len(store) > limit:
        store.popitem(last=False)


class StudioDirector:
    def __init__(
        self, crew: StudioCrew, host: StudioHost, *, clock: Callable[[], float] = time.perf_counter
    ) -> None:
        self.crew = crew
        self.host = host
        self._clock = clock
        self._queue: asyncio.Queue[tuple[Trigger, float]] = asyncio.Queue(crew.queue_size)
        self._recap_queue: asyncio.Queue[tuple[RecapPack, Recap]] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._recap_worker: asyncio.Task[None] | None = None
        self.seen_rules: set[RuleId] = set()
        self.talking_points: OrderedDict[str, TalkingPoint] = OrderedDict()
        self.lower_thirds: OrderedDict[str, LowerThird] = OrderedDict()
        self.commentary: OrderedDict[str, CommentaryItem] = OrderedDict()
        self.recaps: dict[RecapScope, Recap] = {}
        self.traces: OrderedDict[str, tuple[TraceSpan, ...]] = OrderedDict()
        self.processed = 0
        self.dropped = 0
        self.errors = 0
        self.degraded: Counter[str] = Counter()
        self._lags_ms: deque[float] = deque(maxlen=LAGS_KEPT)

    # ------------------------------------------------------------------ intake (replay side)

    def offer(self, trigger: Trigger) -> bool:
        """Queue a job without waiting; False when the trigger was dropped."""
        if trigger.importance == 1 and self._queue.qsize() >= self.crew.drop_at:
            self.dropped += 1
            return False
        try:
            self._queue.put_nowait((trigger, self._clock()))
        except asyncio.QueueFull:
            self.dropped += 1
            log.warning(
                "studio queue full: dropped %s (importance %d)", trigger.id, trigger.importance
            )
            return False
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._work(), name="studio-worker")
        return True

    # ------------------------------------------------------------------ workers

    async def _work(self) -> None:
        while True:
            trigger, offered = await self._queue.get()
            try:
                await self._process(trigger, offered)
            except Exception:  # one broken job must not stop the commentary of the match
                self.errors += 1
                log.exception("studio job %s failed", trigger.id)
            finally:
                self._queue.task_done()

    async def _process(self, trigger: Trigger, offered: float) -> None:
        first = tuple(rule for rule in trigger.rules if rule not in self.seen_rules)
        view = self.host.view_at(trigger.seq)
        output = await self.crew.service.run(StudioJob(trigger=trigger, first_rules=first), view)
        # a rule is met once an item carried it (a statistics rule only where a text names it)
        self.seen_rules.update(output.commentary.rules)
        _put(self.commentary, output.commentary.id, output.commentary, MAX_COMMENTARY)
        self.host.publish_studio("commentary", output.commentary)
        if output.talking_point is not None:
            item = output.talking_point
            _put(self.talking_points, item.id, item, MAX_TALKING_POINTS)
            self.host.publish_studio("talking_point", item)
        if output.lower_third is not None:
            _put(self.lower_thirds, output.lower_third.id, output.lower_third, MAX_LOWER_THIRDS)
            self.host.publish_studio("lower_third", output.lower_third)
        _put(self.traces, trigger.id, output.trace or (), MAX_TRACES)
        self.processed += 1
        self.degraded.update(output.degraded)
        self._lags_ms.append((self._clock() - offered) * 1000)
        scope = RECAP_SCOPES.get(trigger.kind)
        if scope is not None:
            self._start_recap(scope, trigger, view)

    def _start_recap(self, scope: RecapScope, trigger: Trigger, view: LogView) -> None:
        moments = [m for m in self.host.moments if m.seq <= trigger.seq]
        pack = build_recap_pack(scope, view, moments)
        recap = self.crew.recaps.template(pack)
        self._store_recap(recap)
        self._recap_queue.put_nowait((pack, recap))
        if self._recap_worker is None or self._recap_worker.done():
            self._recap_worker = asyncio.create_task(self._work_recaps(), name="recap-worker")

    def _store_recap(self, recap: Recap) -> None:
        self.recaps[recap.scope] = recap
        self.host.publish_studio("recap", recap)

    async def _work_recaps(self) -> None:
        while True:
            pack, current = await self._recap_queue.get()
            try:
                improved, trace = await self.crew.recaps.improve(pack, current)
                _put(self.traces, current.id, trace.spans, MAX_TRACES)
                if improved is not None:
                    self._store_recap(improved)
            except Exception:
                self.errors += 1
                log.exception("recap %s failed", current.id)
            finally:
                self._recap_queue.task_done()

    # ------------------------------------------------------------------ control

    def _busy(self, worker: asyncio.Task[None] | None) -> bool:
        return worker is not None and not worker.done()

    def active(self) -> bool:
        """Is a worker task alive (tests: nothing may be left after shutdown)?"""
        return self._busy(self._worker) or self._busy(self._recap_worker)

    async def idle(self) -> None:
        """Until every queued job and recap is done (tests and the CLI)."""
        if self._busy(self._worker):
            await self._queue.join()
        if self._busy(self._recap_worker):
            await self._recap_queue.join()

    async def stop(self) -> None:
        """Cancel the workers; jobs still waiting are dropped (and counted)."""
        for worker in (self._worker, self._recap_worker):
            if worker is not None and not worker.done():
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker
        self._worker = self._recap_worker = None
        for queue in (self._queue, self._recap_queue):
            while not queue.empty():
                queue.get_nowait()
                queue.task_done()
                if queue is self._queue:
                    self.dropped += 1

    # ------------------------------------------------------------------ editor actions

    def set_status(self, item_id: str, status: TalkingPointStatus) -> TalkingPoint | None:
        item = self.talking_points.get(item_id)
        if item is None:
            return None
        updated = item.model_copy(update={"status": status})
        self.talking_points[item_id] = updated
        return updated

    # ------------------------------------------------------------------ reads

    def find(self, item_id: str) -> tuple[StudioKind, StudioItem] | None:
        stores: tuple[tuple[StudioKind, OrderedDict[str, Any]], ...] = (
            ("commentary", self.commentary),
            ("talking_point", self.talking_points),
            ("lower_third", self.lower_thirds),
        )
        for kind, store in stores:
            found: StudioItem | None = store.get(item_id)
            if found is not None:
                return kind, found
        for recap in self.recaps.values():
            if recap.id == item_id:
                return "recap", recap
        return None

    def trace_of(self, item_id: str) -> tuple[TraceSpan, ...]:
        """The trace of the job that made an item ("<trigger>-c" -> trace of "<trigger>")."""
        if item_id in self.traces:
            return self.traces[item_id]
        for suffix in ("-c", "-tp", "-lt"):
            if item_id.endswith(suffix):
                return self.traces.get(item_id.removesuffix(suffix), ())
        return ()

    def lag_p95(self) -> float | None:
        lags = list(self._lags_ms)
        if not lags:
            return None
        if len(lags) == 1:
            return round(lags[0], 1)
        return round(statistics.quantiles(lags, n=20)[18], 1)

    def stats(self) -> dict[str, Any]:
        return {
            "queued": self._queue.qsize(),
            "processed": self.processed,
            "dropped": self.dropped,
            "errors": self.errors,
            "lag_ms_p95": self.lag_p95(),
            "degraded": dict(sorted(self.degraded.items())),
            "commentary": len(self.commentary),
            "talking_points": len(self.talking_points),
            "lower_thirds": len(self.lower_thirds),
            "recaps": sorted(f"{r.scope}@{r.revision}" for r in self.recaps.values()),
        }

    def lags(self) -> Sequence[float]:
        return tuple(self._lags_ms)
