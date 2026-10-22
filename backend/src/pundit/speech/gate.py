"""RateGate: how many syntheses may start, and in which order (docs/day5-design.md 8.5).

A token bucket (TTS_MAX_RPM per minute, the Free tier allows 20 per 60 s), a limit of concurrent
calls, and a priority queue: a goal goes before a stat tick, a recap waits longer than a live
line. A 429 closes the gate until its Retry-After has passed. `acquire` returns False when the
request waited longer than it may (the caller marks the cue `throttled`).
"""

import asyncio
import contextlib
import heapq
import itertools
import time
from collections.abc import Callable
from typing import Any

MIN_WAIT_S = 0.005
RECHECK_S = 0.25


class RateGate:
    def __init__(
        self, per_minute: int, concurrency: int, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.per_minute = per_minute
        self.concurrency = concurrency
        self._clock = clock
        self._rate = per_minute / 60.0
        self._tokens = float(per_minute)
        self._updated = clock()
        self._cooldown_until = 0.0
        self._active = 0
        self._queue: list[tuple[int, int]] = []
        self._tickets = itertools.count()
        self._condition = asyncio.Condition()
        self.admitted = 0
        self.throttled = 0

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(
            float(self.per_minute), self._tokens + (now - self._updated) * self._rate
        )
        self._updated = now

    def _ready_in(self, entry: tuple[int, int]) -> float | None:
        """Seconds until `entry` may start; None when it waits for another caller."""
        now = self._clock()
        if self._queue[0] != entry or self._active >= self.concurrency:
            return None
        waits = [self._cooldown_until - now]
        if self._tokens < 1:
            waits.append((1 - self._tokens) / self._rate)
        return max(0.0, *waits)

    def _drop(self, entry: tuple[int, int]) -> None:
        if entry in self._queue:
            self._queue.remove(entry)
            heapq.heapify(self._queue)

    async def acquire(self, priority: int, max_wait_s: float) -> bool:
        entry = (-priority, next(self._tickets))
        deadline = self._clock() + max_wait_s
        async with self._condition:
            heapq.heappush(self._queue, entry)
            try:
                while True:
                    self._refill()
                    ready = self._ready_in(entry)
                    if ready == 0.0:
                        heapq.heappop(self._queue)
                        self._tokens -= 1
                        self._active += 1
                        self.admitted += 1
                        self._condition.notify_all()
                        return True
                    left = deadline - self._clock()
                    if left <= 0:
                        self._drop(entry)
                        self.throttled += 1
                        self._condition.notify_all()
                        return False
                    wait = min(left, ready if ready is not None else RECHECK_S)
                    with contextlib.suppress(TimeoutError):  # time to look at the clock again
                        await asyncio.wait_for(self._condition.wait(), max(MIN_WAIT_S, wait))
            except asyncio.CancelledError:
                self._drop(entry)
                self._condition.notify_all()
                raise

    async def release(self) -> None:
        async with self._condition:
            self._active = max(0, self._active - 1)
            self._condition.notify_all()

    def cooldown(self, seconds: float) -> None:
        """No call starts for `seconds` (a 429 and its Retry-After)."""
        self._cooldown_until = max(self._cooldown_until, self._clock() + seconds)

    def stats(self) -> dict[str, Any]:
        self._refill()
        return {
            "per_minute": self.per_minute,
            "tokens": round(self._tokens, 2),
            "active": self._active,
            "waiting": len(self._queue),
            "admitted": self.admitted,
            "throttled": self.throttled,
            "cooldown_s": round(max(0.0, self._cooldown_until - self._clock()), 2),
        }
