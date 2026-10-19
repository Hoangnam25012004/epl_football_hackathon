"""Stats tools: pure functions (LogView, MetricParams) -> StatResult, exposed via REGISTRY.

Importing this package registers every metric.
"""

from pundit.stats import passing, physical, possession, tactical, team  # noqa: F401
from pundit.stats.registry import REGISTRY, Comparison, MetricParams, compare, compute
from pundit.stats.result import StatResult, display

__all__ = ["REGISTRY", "Comparison", "MetricParams", "StatResult", "compare", "compute", "display"]
