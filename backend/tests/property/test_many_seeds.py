"""Wide sweep over many seeds. Slow: run with `uv run pytest -m slow`."""

import pytest
from helpers import SCENARIO_KEYS

from pundit.engine.generator import generate
from pundit.engine.scenarios import get_scenario
from pundit.engine.validate import validate_match
from pundit.stats import MetricParams, compute


@pytest.mark.slow
@pytest.mark.parametrize("scenario", SCENARIO_KEYS)
def test_one_hundred_seeds(scenario: str) -> None:
    spec = get_scenario(scenario)
    attempts = []
    for seed in range(100):
        match = generate(scenario, seed)
        validate_match(match.meta, match.events)
        view = match.view()
        assert spec.failed_expectations(view) == []
        home, away = match.meta.team_ids
        h = compute(view, "possession", MetricParams(team=home)).value
        a = compute(view, "possession", MetricParams(team=away)).value
        assert h is not None and a is not None and h + a == pytest.approx(100.0)
        attempts.append(match.meta.attempt)
    assert sum(attempts) / len(attempts) <= 3
