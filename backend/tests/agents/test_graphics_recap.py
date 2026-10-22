"""Graphics agent (docs/day3-design.md section 7) and recap agent (section 10)."""

import pytest
from helpers import generated
from studio_helpers import SAMPLES, offline, pack, triggers

from pundit.agents.contracts import GraphicsChoice, LowerThird
from pundit.agents.graphics import (
    DURATION_MS,
    RULES,
    build_lower_third,
    options,
    rule_choice,
    validate_choice,
    wants_graphic,
)
from pundit.agents.recap import RECAP_SCOPES, build_recap_pack, render_recap, template_recap
from pundit.domain.events import CardEvent, GoalEvent, VarCheckEvent

# table 7.1
EXPECTED_TEMPLATE = {
    "goal": "score_update",
    "goal_overturned": "score_update",
    "red_card": "player_card",
    "momentum_shift": "momentum",
    "period_end": "team_compare",
    "full_time": "team_compare",
    "stat_tick": "team_compare",
    "big_chance": "player_card",
    "rocket_shot": "player_card",
}


def lower_thirds(scenario: str, seed: int) -> list[LowerThird]:
    return [o.lower_third for o in offline(scenario, seed).outputs if o.lower_third is not None]


@pytest.mark.parametrize(("scenario", "seed"), SAMPLES)
def test_lower_thirds_follow_table_7_1(scenario: str, seed: int) -> None:
    by_trigger = {t.id: t for t in triggers(scenario, seed)}
    made = lower_thirds(scenario, seed)
    assert len(made) == sum(1 for t in by_trigger.values() if wants_graphic(t))
    for item in made:
        trigger = by_trigger[item.trigger_id]
        built = pack(scenario, seed, trigger.id)
        assert item.template == EXPECTED_TEMPLATE[trigger.kind]
        assert (
            item.show_at_match_ms == trigger.match_ms and item.match_clock == trigger.display_minute
        )
        assert item.duration_ms >= DURATION_MS[item.template]
        assert item.priority == trigger.importance
        assert item.synthetic and item.disclaimer
        assert item.home.primary_color.startswith("#") and item.away.primary_color.startswith("#")
        displays = {f.display for f in built.facts}
        for stat in item.stats:
            shown = [v for v in (stat.home, stat.away, stat.value) if v is not None]
            assert shown and set(shown) <= displays, (item.id, stat)
        assert set(item.evidence_event_ids) == {e for f in item.facts for e in f.evidence_event_ids}


def test_stat_ticks_have_a_graphic_at_30_and_75_only() -> None:
    ticks = [t for t in triggers("dominant_loser", 42) if t.kind == "stat_tick"]
    assert [t.details["tick_minute"] for t in ticks if wants_graphic(t)] == ["30", "75"]


def test_var_graphic_replaces_the_goal_graphic() -> None:
    made = {lt.trigger_id: lt for lt in lower_thirds("comeback_var", 7)}
    overturned = next(t for t in triggers("comeback_var", 7) if t.kind == "goal_overturned")
    var = made[overturned.id]
    assert var.title_vi == "VAR: KHÔNG CÔNG NHẬN BÀN THẮNG"
    (goal_id,) = overturned.related_ids
    assert var.supersedes == (f"{goal_id}-lt",) and f"{goal_id}-lt" == made[goal_id].id


def test_choices_are_validated_against_the_candidates() -> None:
    goal = next(t for t in triggers("dominant_loser", 42) if t.kind == "goal")
    built = pack("dominant_loser", 42, goal.id)
    opts = options(built)
    choice = rule_choice(built)
    assert choice is not None and not validate_choice(choice, opts)
    assert RULES["goal"][0] in opts.templates
    bad = GraphicsChoice(template="momentum", stat_keys=("invented",))
    errors = validate_choice(bad, opts)
    assert len(errors) == 2
    assert validate_choice(GraphicsChoice(template=choice.template, stat_keys=()), opts)
    lt = build_lower_third(built, generated("dominant_loser", 42).meta, choice, source="llm")
    assert lt.source == "llm" and len(lt.stats) == len(choice.stat_keys)


# --------------------------------------------------------------------------- recap


def _goals_that_count(scenario: str, seed: int, until_seq: int) -> list[GoalEvent]:
    events = generated(scenario, seed).events[: until_seq + 1]
    overturned = {
        rid
        for e in events
        if isinstance(e, VarCheckEvent) and e.decision == "overturned" and e.check_type == "goal"
        for rid in e.related_ids
    }
    return [e for e in events if isinstance(e, GoalEvent) and e.id not in overturned]


@pytest.mark.parametrize(("scenario", "seed"), SAMPLES)
def test_recap_goals_and_cards_match_the_events(scenario: str, seed: int) -> None:
    run = offline(scenario, seed)
    for kind, scope in RECAP_SCOPES.items():
        trigger = next(t for t in run.triggers if t.kind == kind)
        built = run.recap_packs[scope]
        goals = _goals_that_count(scenario, seed, trigger.seq)
        assert [g.event_id for g in built.goals] == [g.id for g in goals]
        cards = [
            e
            for e in generated(scenario, seed).events[: trigger.seq + 1]
            if isinstance(e, CardEvent)
        ]
        assert [c.event_id for c in built.cards] == [c.id for c in cards]
        home, away = built.match.teams[0].id, built.match.teams[1].id
        assert built.match.score[home] + built.match.score[away] == len(goals)


def test_the_overturned_goal_is_not_counted() -> None:
    built = offline("comeback_var", 7).recap_packs["FULL"]
    assert len(built.overturned) == 1
    assert built.overturned[0].event_id not in {g.event_id for g in built.goals}
    for language in ("vi", "en"):
        text = render_recap(built, language, "analyst")
        assert any("VAR" in paragraph for paragraph in text.paragraphs)


@pytest.mark.parametrize(("scenario", "seed"), SAMPLES)
def test_recap_lengths(scenario: str, seed: int) -> None:
    built = offline(scenario, seed).recap_packs["FULL"]
    recap = template_recap(built)
    for text in recap.texts:
        words = sum(len(p.split()) for p in text.paragraphs)
        low, high = (60, 220) if text.mode == "analyst" else (25, 120)
        assert low <= words <= high, (text.language, text.mode, words)
        assert 3 <= len(text.paragraphs) <= 6
        assert all(len(p) <= 300 for p in text.paragraphs)
    assert recap.revision == 1 and recap.id.endswith("-recap-FULL")


def test_h1_recap_only_covers_the_first_half() -> None:
    run = offline("dominant_loser", 42)
    first_half = run.recap_packs["H1"]
    events = {e.id: e for e in run.match.events}
    assert all(events[g.event_id].period == 1 for g in first_half.goals)
    assert {m.period for m in first_half.key_moments} <= {1}
    full = build_recap_pack("FULL", run.view_at(len(run.match.events) - 1), run.moments)
    assert len(full.goals) >= len(first_half.goals)
