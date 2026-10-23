"""The per-client rate limit of the MCP tools (docs/day6-design.md section 3.7)."""

import pytest

from pundit.mcp_server.errors import ToolInputError
from pundit.mcp_server.limits import RateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_bucket_starts_full_and_refills_every_second() -> None:
    clock = Clock()
    limiter = RateLimiter(60, clock=clock)
    for _ in range(60):
        limiter.take("a")
    with pytest.raises(ToolInputError, match="rate limited: retry in 1 s"):
        limiter.take("a")
    clock.now += 1.0
    limiter.take("a")  # one token a second at 60 a minute
    assert limiter.refused == 1


def test_a_call_may_cost_more_than_one() -> None:
    limiter = RateLimiter(6, clock=Clock())  # a token every 10 s
    limiter.take("a", 3)
    limiter.take("a", 3)
    with pytest.raises(ToolInputError, match="retry in 30 s"):
        limiter.take("a", 3)


def test_a_cost_above_the_bucket_is_a_full_bucket() -> None:
    clock = Clock()
    limiter = RateLimiter(2, clock=clock)
    limiter.take("a", 3)  # never forbidden for good
    with pytest.raises(ToolInputError):
        limiter.take("a", 3)
    clock.now += 60
    limiter.take("a", 3)


def test_clients_have_their_own_buckets() -> None:
    limiter = RateLimiter(1, clock=Clock())
    limiter.take("a")
    limiter.take("b")
    with pytest.raises(ToolInputError):
        limiter.take("a")


def test_a_refused_call_spends_nothing() -> None:
    clock = Clock()
    limiter = RateLimiter(2, clock=clock)
    limiter.take("a", 2)
    for _ in range(5):
        with pytest.raises(ToolInputError):
            limiter.take("a")
    clock.now += 30  # one token back, however many refusals
    limiter.take("a")
    assert limiter.refused == 5


def test_refilling_never_overflows_the_bucket() -> None:
    clock = Clock()
    limiter = RateLimiter(2, clock=clock)
    limiter.take("a")
    clock.now += 3600
    limiter.take("a")
    limiter.take("a")
    with pytest.raises(ToolInputError):
        limiter.take("a")


def test_the_table_of_clients_is_bounded() -> None:
    limiter = RateLimiter(1, clock=Clock(), max_clients=3)
    for client in "abcd":
        limiter.take(client)
    assert len(limiter) == 3
    limiter.take("a")  # "a" was the least recently seen: dropped, so a full bucket again
    with pytest.raises(ToolInputError):
        limiter.take("d")


def test_the_rate_must_be_positive() -> None:
    with pytest.raises(ValueError, match="per_minute"):
        RateLimiter(0)
