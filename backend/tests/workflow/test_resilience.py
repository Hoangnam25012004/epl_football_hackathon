"""Timeouts, budget and circuit breaker (design section 9.2) with injected clocks."""

import asyncio

import pytest
from helpers import run

from pundit.llm.ports import LlmTimeout
from pundit.orchestration.resilience import Budget, CircuitBreaker, with_timeout


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_budget() -> None:
    clock = Clock()
    budget = Budget(8.0, clock=clock)
    assert budget.timeout_for(4.0) == 4.0
    clock.t = 6.5
    assert budget.remaining() == pytest.approx(1.5) and budget.timeout_for(4.0) == pytest.approx(
        1.5
    )
    clock.t = 9.0
    assert budget.remaining() == 0 and budget.timeout_for(4.0) == 0


def test_with_timeout() -> None:
    async def fast() -> int:
        return 7

    async def hang() -> int:
        await asyncio.sleep(3600)
        return 0

    assert run(with_timeout(fast, 1.0)) == 7
    with pytest.raises(LlmTimeout):
        run(with_timeout(hang, 0.01))
    with pytest.raises(LlmTimeout, match="no time left"):
        run(with_timeout(fast, 0.0))


def test_breaker_opens_after_three_failures_and_recovers() -> None:
    clock = Clock()
    breaker = CircuitBreaker(clock=clock)
    for _ in range(2):
        assert breaker.allow()
        breaker.record_failure()
    assert breaker.state == "closed"
    breaker.record_failure()
    assert breaker.state == "open" and not breaker.allow()
    clock.t = 59.9
    assert not breaker.allow()
    clock.t = 60.0
    assert breaker.state == "half_open"
    assert breaker.allow()  # the single trial
    assert not breaker.allow()  # no second call while the trial runs
    breaker.record_success()
    assert breaker.state == "closed" and breaker.allow() and breaker.consecutive_failures == 0


def test_a_failed_trial_opens_again() -> None:
    clock = Clock()
    breaker = CircuitBreaker(threshold=1, cooldown_s=10, clock=clock)
    breaker.record_failure()
    clock.t = 10.0
    assert breaker.allow()
    breaker.record_failure()
    assert breaker.state == "open"
    clock.t = 19.9
    assert not breaker.allow()
    clock.t = 20.0
    assert breaker.allow()


def test_success_resets_the_count() -> None:
    breaker = CircuitBreaker(clock=Clock())
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.state == "closed"
