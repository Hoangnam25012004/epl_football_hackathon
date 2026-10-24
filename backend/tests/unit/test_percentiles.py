"""The p95 of measured latencies stays within what was measured (Day 6, fault matrix C10)."""

import statistics

from hypothesis import given
from hypothesis import strategies as st

from pundit.domain.percentiles import p95


def test_no_value_and_one_value() -> None:
    assert p95([]) is None
    assert p95([42.0]) == 42.0


def test_one_outlier_among_few_jobs_is_not_extrapolated() -> None:
    """C10: the first job waits for its LLM budget, the 14 others run on templates."""
    lags = [120.0 + 25 * i for i in range(13)] + [520.0, 2562.0]
    exclusive = statistics.quantiles(lags, n=20)[18]
    assert exclusive > max(lags)  # the default method: a "p95" above every lag measured
    value = p95(lags)
    assert value is not None and max(lags[:-1]) <= value <= max(lags)


@given(st.lists(st.floats(min_value=0, max_value=1e6), min_size=1, max_size=200))
def test_within_the_measured_range(values: list[float]) -> None:
    value = p95(values)
    assert value is not None
    assert min(values) - 1e-6 <= value <= max(values) + 1e-6


@given(st.lists(st.floats(min_value=0, max_value=1e6), min_size=2, max_size=200))
def test_not_below_the_median(values: list[float]) -> None:
    value = p95(values)
    assert value is not None and value >= statistics.median(values) - 1e-6
