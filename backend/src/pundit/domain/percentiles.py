"""Percentiles of measured latencies, within the measured range.

`statistics.quantiles` uses its "exclusive" method by default, which extrapolates past the
largest value of a small sample: 15 studio jobs with one 2.6 s outlier gave a "p95" of 3.0 s,
above every lag measured (found by the Day 6 fault matrix, C10). The "inclusive" method
interpolates between measured values, so a percentile never exceeds the maximum.
"""

import statistics
from collections.abc import Iterable


def p95(values: Iterable[float]) -> float | None:
    """The 95th percentile, or None with no value; one value is its own percentile."""
    data = list(values)
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    return statistics.quantiles(data, n=20, method="inclusive")[18]
