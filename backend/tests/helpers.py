"""Shared test helpers: cached generated matches."""

from functools import cache

from pundit.engine.generator import generate
from pundit.engine.match import GeneratedMatch

SCENARIO_KEYS = ("dominant_loser", "comeback_var", "red_card_swing", "balanced")
SCRIPTED_KEYS = ("dominant_loser", "comeback_var", "red_card_swing")
SEEDS = tuple(range(8))


@cache
def generated(scenario: str, seed: int, physical: bool = True) -> GeneratedMatch:
    return generate(scenario, seed, physical=physical)
