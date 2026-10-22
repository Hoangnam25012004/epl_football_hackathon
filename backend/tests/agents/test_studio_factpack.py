"""FactPackBuilder (docs/day3-design.md section 5): facts are Stats Tools results on the view
pinned at the trigger's seq (S-4)."""

import re

import pytest
from helpers import generated
from studio_helpers import SAMPLES, pack, triggers, view_at

from pundit.agents.collector import get_stat
from pundit.agents.factpack import build_pack, plan
from pundit.stats import MetricParams, compute

KEY = re.compile(r"^[a-z_]+:[a-z0-9_-]+:[^@]+@(\d+)$")


@pytest.mark.parametrize(("scenario", "seed"), SAMPLES)
def test_every_fact_is_the_stats_tool_result_at_the_trigger_seq(scenario: str, seed: int) -> None:
    for trigger in triggers(scenario, seed):
        view = view_at(scenario, seed, trigger.seq)
        built = pack(scenario, seed, trigger.id)
        assert built.as_of_seq == trigger.seq
        by_key = {fact.key: fact for fact in built.facts}
        assert len(by_key) == len(built.facts), "duplicate fact keys"
        for query in plan(trigger, view.meta):
            result = compute(
                view, query.metric, MetricParams(team=query.team, player=query.player,
                                                 window=query.window)
            )  # fmt: skip
            if result.value is None:
                continue  # nothing to say: left out of the pack
            key = f"{query.metric}:{query.player or query.team or 'match'}:"
            key += f"{result.window.label()}@{trigger.seq}"
            assert key in by_key, (trigger.id, key)
            assert by_key[key].value == result.value
        for fact in built.facts:
            if ":" in fact.key and "@" in fact.key:
                match = KEY.match(fact.key)
                assert match is not None, fact.key
                assert int(match.group(1)) <= trigger.seq
        assert set(built.headline) <= set(by_key)


def test_a_pack_is_pinned_even_when_the_match_went_on() -> None:
    """The same trigger on a later view gives other numbers: the pin matters (S-4)."""
    trigger = next(t for t in triggers("dominant_loser", 42) if t.kind == "stat_tick")
    pinned = pack("dominant_loser", 42, trigger.id)
    full = generated("dominant_loser", 42)
    late_view = view_at("dominant_loser", 42, len(full.events) - 1)
    calls = [get_stat(late_view, q) for q in plan(trigger, late_view.meta)]
    late = build_pack(trigger, late_view, calls)
    assert {f.display for f in late.facts} != {f.display for f in pinned.facts}


def test_first_rules_are_passed_through() -> None:
    trigger = next(t for t in triggers("dominant_loser", 42) if t.kind == "offside")
    view = view_at("dominant_loser", 42, trigger.seq)
    calls = [get_stat(view, q) for q in plan(trigger, view.meta)]
    built = build_pack(trigger, view, calls, first_rules=("offside",))
    assert built.first_rules == ("offside",)


def test_window_numbers_cover_rolling_windows_and_ticks() -> None:
    momentum = next(t for t in triggers("dominant_loser", 42) if t.kind == "momentum_shift")
    assert 10.0 in pack("dominant_loser", 42, momentum.id).window_numbers
    tick = next(t for t in triggers("dominant_loser", 42) if t.kind == "stat_tick")
    numbers = pack("dominant_loser", 42, tick.id).window_numbers
    assert float(tick.details["tick_minute"]) in numbers
