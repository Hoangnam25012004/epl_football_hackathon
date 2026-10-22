"""Timeouts, the per-claim time budget and the circuit breaker (design section 9.2).

Clocks are injected, so every rule is tested without waiting. Timeouts use asyncio's own
clock: a hung call is cancelled, never awaited forever.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Literal

from pundit.llm.ports import LlmTimeout

Clock = Callable[[], float]
BreakerState = Literal["closed", "open", "half_open"]

BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN_S = 60.0


class Budget:
    """Time left for one claim; LLM steps get min(per-call timeout, what is left)."""

    def __init__(self, total_s: float, *, clock: Clock = time.monotonic) -> None:
        self.total_s = total_s
        self._clock = clock
        self._started = clock()

    def elapsed(self) -> float:
        return self._clock() - self._started

    def remaining(self) -> float:
        return max(0.0, self.total_s - self.elapsed())

    def timeout_for(self, per_call_s: float) -> float:
        return min(per_call_s, self.remaining())


async def with_timeout[T](call: Callable[[], Awaitable[T]], seconds: float) -> T:
    """Run `call()`; raise LlmTimeout if it takes longer than `seconds` (or none are left)."""
    if seconds <= 0:
        raise LlmTimeout("no time left in the claim budget")
    try:
        async with asyncio.timeout(seconds):
            return await call()
    except TimeoutError as exc:
        raise LlmTimeout(f"no answer within {seconds:.2f} s") from exc


class CircuitBreaker:
    """Opens after `threshold` consecutive LLM failures; after `cooldown_s` lets one trial call
    through (half-open): success closes it, failure opens it again for another cooldown."""

    def __init__(
        self,
        *,
        threshold: int = BREAKER_THRESHOLD,
        cooldown_s: float = BREAKER_COOLDOWN_S,
        clock: Clock = time.monotonic,
    ) -> None:
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_since: float | None = None

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.cooldown_s:
            return "half_open"
        return "open"

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    def allow(self) -> bool:
        """May an LLM call start now? In half-open, only one trial at a time."""
        state = self.state
        if state == "closed":
            return True
        if state == "open":
            return False
        now = self._clock()
        if self._trial_since is not None and now - self._trial_since < self.cooldown_s:
            return False  # a trial is already in flight
        self._trial_since = now
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._trial_since = None

    def record_failure(self) -> None:
        now = self._clock()
        if self._trial_since is not None:  # the half-open trial failed
            self._trial_since = None
            self._opened_at = now
            return
        self._failures += 1
        if self._failures >= self.threshold and self._opened_at is None:
            self._opened_at = now

    def trip(self, cooldown_s: float | None = None) -> None:
        """Open now, for `cooldown_s` (default: the usual cooldown): a failure no retry can fix,
        such as bad credentials (Day 5 voice)."""
        wait = self.cooldown_s if cooldown_s is None else cooldown_s
        self._failures = max(self._failures, self.threshold)
        self._trial_since = None
        self._opened_at = self._clock() + wait - self.cooldown_s
