"""The MCP tools as functions (docs/day6-design.md sections 3.3-3.5 and 10.2)."""

from typing import Any

import pytest
from helpers import generated, run, view_at
from mcp_helpers import CV, DL, MATCHES, RC, static
from pydantic import BaseModel

from pundit.agents.moments import detect_all
from pundit.domain.matchtime import H2
from pundit.mcp_server import tools
from pundit.mcp_server.errors import ToolInputError
from pundit.mcp_server.outputs import DISCLAIMER, MAX_EVIDENCE, StatOut
from pundit.mcp_server.params import WindowArg
from pundit.stats import REGISTRY, MetricParams, compute
from pundit.stats.result import display

H2_ARG = WindowArg(type="second_half")
LIMIT = 64 * 1024  # the largest answer a tool may give (section 3.5)


def size(model: BaseModel) -> int:
    return len(model.model_dump_json().encode("utf-8"))


def stat(**kwargs: Any) -> StatOut:
    kwargs.setdefault("match_id", DL)
    return run(tools.get_stat(static(), **kwargs))


def visible(scenario: str, seed: int) -> list[Any]:
    return [e for e in generated(scenario, seed).events if e.type not in tools.HIDDEN_EVENTS]


def test_list_matches_offers_every_scenario_at_the_default_seed() -> None:
    out = run(tools.list_matches(static()))
    assert out.live == []
    assert [s.match_id for s in out.scenarios] == [
        "dominant_loser-42",
        "comeback_var-42",
        "red_card_swing-42",
        "balanced-42",
    ]
    assert (out.scenarios[0].home, out.scenarios[0].away) == ("Harbor Lions FC", "Kestrel Athletic")
    assert out.synthetic is True and out.disclaimer == DISCLAIMER


def test_list_metrics_is_the_whole_registry() -> None:
    out = tools.list_metrics()
    assert [m.name for m in out.metrics] == sorted(REGISTRY)
    assert all(m.definition and m.description_en and m.description_vi for m in out.metrics)
    players = {m.name for m in tools.list_metrics("player").metrics}
    assert players == {"player_distance", "player_top_speed"}
    assert {m.name for m in tools.list_metrics("match").metrics} == {
        "ball_in_play_time",
        "chaos_index",
    }


def test_list_metrics_refuses_an_unknown_scope() -> None:
    with pytest.raises(ToolInputError, match="team, match or player"):
        tools.list_metrics("league")


def test_get_stat_is_the_registry_value_with_its_evidence() -> None:
    out = stat(metric="possession", team="HAR", window=H2_ARG)
    expected = compute(view_at(), "possession", MetricParams(team="harbor_lions", window=H2))
    assert out.stat.display == "61%" == display(expected)
    assert out.stat.value == expected.value
    assert (out.stat.window, out.stat.team_name, out.stat.unit) == (
        "H2",
        "Harbor Lions FC",
        "percent",
    )
    assert out.stat.evidence_total == len(expected.evidence_event_ids) > MAX_EVIDENCE
    assert out.stat.evidence_event_ids == list(expected.evidence_event_ids[:MAX_EVIDENCE])
    assert (out.match_id, out.source, out.synthetic) == (DL, "replay", True)
    last = generated("dominant_loser", 42).events[-1]
    assert (out.as_of.seq, out.as_of.minute) == (last.seq, "90+6'")


def test_a_team_metric_on_a_player_counts_for_that_players_team() -> None:
    out = stat(metric="shots", player="J. Ellery")
    expected = compute(
        view_at(), "shots", MetricParams(team="kestrel_athletic", player="kestrel_athletic-11")
    )
    assert (out.stat.team, out.stat.player, out.stat.player_name) == (
        "kestrel_athletic",
        "kestrel_athletic-11",
        "Jordan Ellery",
    )
    assert out.stat.display == display(expected)


def test_a_player_metric_by_name() -> None:
    out = stat(metric="player_top_speed", player="Jordan Ellery")
    expected = compute(view_at(), "player_top_speed", MetricParams(player="kestrel_athletic-11"))
    assert out.stat.display == display(expected) == "34.4 km/h"


def test_a_match_metric_needs_no_team() -> None:
    out = stat(metric="chaos_index", window=H2_ARG)
    assert (out.stat.team, out.stat.player) == (None, None)
    assert out.stat.display == display(compute(view_at(), "chaos_index", MetricParams(window=H2)))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"metric": "shots"}, "give a team, or use compare_stat"),
        ({"metric": "player_distance", "team": "HAR"}, "needs a player"),
        ({"metric": "possession", "team": "HAR", "player": "Calder"}, "not support a player"),
        ({"metric": "shots", "team": "HAR", "player": "J. Ellery"}, "does not play for"),
        ({"metric": "chaos_index", "team": "HAR"}, "match-level metric"),
        ({"metric": "posession", "team": "HAR"}, "did you mean possession"),
        ({"metric": "shots", "team": "Ironvale"}, "unknown team"),
        ({"metric": "shots", "player": "Nobody Atall Zzyzx"}, "unknown player"),
        ({"metric": "shots", "team": "HAR", "window": WindowArg(type="last_minutes")}, "minutes"),
        ({"metric": "shots", "team": "HAR", "as_of": "95'"}, "use a minute such as"),
        ({"metric": "shots", "team": "HAR", "match_id": "nope-1"}, "unknown match"),
    ],
)
def test_get_stat_refuses_with_how_to_fix(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ToolInputError, match=message):
        stat(**kwargs)


def test_compare_stat_puts_the_home_team_first() -> None:
    out = run(tools.compare_stat(static(), match_id=DL, metric="possession", window=H2_ARG))
    assert (out.home.team, out.away.team) == ("harbor_lions", "kestrel_athletic")
    assert (out.home.display, out.away.display) == ("61%", "39%")
    assert out.home.value is not None and out.away.value is not None
    assert out.diff == pytest.approx(out.home.value - out.away.value)
    assert out.ratio == pytest.approx(out.home.value / out.away.value)
    assert (out.metric, out.window, out.source) == ("possession", "H2", "replay")


@pytest.mark.parametrize("metric", ["chaos_index", "player_top_speed"])
def test_compare_stat_compares_teams_only(metric: str) -> None:
    with pytest.raises(ToolInputError, match="compares the two teams"):
        run(tools.compare_stat(static(), match_id=DL, metric=metric))


@pytest.mark.parametrize(
    ("match_id", "as_of", "score", "period", "finished"),
    [
        (DL, None, (1, 2), 2, True),
        (CV, "HT", (0, 1), 1, False),
        (CV, "53'", (0, 2), 2, False),
        (CV, "FT", (3, 2), 2, True),
    ],
)
def test_scoreboard(
    match_id: str, as_of: str | None, score: tuple[int, int], period: int, finished: bool
) -> None:
    out = run(tools.get_scoreboard(static(), match_id=match_id, as_of=as_of))
    assert (out.home.goals, out.away.goals) == score
    assert (out.period, out.finished) == (period, finished)


def test_scoreboard_names_the_teams() -> None:
    out = run(tools.get_scoreboard(static(), match_id=CV, as_of="HT"))
    assert (out.home.id, out.home.short_name, out.away.name) == (
        "ironvale_united",
        "IRO",
        "Saltmarsh Rovers",
    )
    assert out.as_of.minute == "45+4'"


def test_get_events_lists_the_latest_twenty_in_match_order() -> None:
    out = run(tools.get_events(static(), match_id=DL))
    assert [e.id for e in out.events] == [e.id for e in visible("dominant_loser", 42)[-20:]]
    assert out.truncated is True and out.missing == []


def test_get_events_by_type() -> None:
    out = run(tools.get_events(static(), match_id=RC, types=["card"], last=5))
    cards = [e for e in generated("red_card_swing", 7).events if e.type == "card"]
    assert [e.id for e in out.events] == [e.id for e in cards[-5:]]
    assert out.truncated is (len(cards) > 5)
    red = [e for e in out.events if e.details.get("color") == "red"]
    assert [(e.minute, e.team, e.player_name) for e in red] == [
        ("52'", "kestrel_athletic", "Santi Arregui")
    ]


def test_an_event_says_what_happened_without_repeating_its_fields() -> None:
    out = run(tools.get_events(static(), match_id=RC, types=["card"]))
    yellow = next(e for e in out.events if e.details.get("color") == "yellow")
    assert yellow.player_name == "Trịnh Minh Khôi"
    for event in out.events:
        assert not set(event.details) & tools._BASE_FIELDS
        assert all(isinstance(v, str | int | float | bool) for v in event.details.values())


def test_get_events_by_id_reports_the_missing_ones() -> None:
    events = generated("comeback_var", 7).events
    goal = next(e for e in events if e.type == "goal")
    after = events[-1]  # not played yet at half-time
    out = run(
        tools.get_events(
            static(), match_id=CV, ids=[goal.id, "nope-1", goal.id, after.id], as_of="HT"
        )
    )
    assert [e.id for e in out.events] == [goal.id]
    assert out.missing == ["nope-1", after.id]


def test_physical_samples_are_never_listed() -> None:
    physical = next(
        e for e in generated("dominant_loser", 42).events if e.type == "player_physical"
    )
    by_id = run(tools.get_events(static(), match_id=DL, ids=[physical.id]))
    by_type = run(tools.get_events(static(), match_id=DL, types=["player_physical"]))
    assert by_id.events == [] and by_id.missing == [physical.id]
    assert by_type.events == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"ids": ["a"], "last": 3}, "not both"),
        ({"types": ["goal", "dance"]}, r"unknown event types \['dance'\]"),
        ({"ids": [f"x-{n}" for n in range(51)]}, "at most 50 ids"),
        ({"last": 0}, "between 1 and 50"),
        ({"last": 51}, "between 1 and 50"),
    ],
)
def test_get_events_refuses(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ToolInputError, match=message):
        run(tools.get_events(static(), match_id=DL, **kwargs))


def test_get_moments_keeps_the_latest_in_match_order() -> None:
    every = run(tools.get_moments(static(), match_id=CV, min_importance=1, limit=50))
    some = run(tools.get_moments(static(), match_id=CV, min_importance=1, limit=3))
    assert [m.id for m in some.moments] == [m.id for m in every.moments][-3:]
    assert some.truncated is True


def test_get_moments_by_importance_and_minute() -> None:
    match = generated("comeback_var", 7)
    top = run(tools.get_moments(static(), match_id=CV, min_importance=3))
    assert {m.importance for m in top.moments} == {3}
    assert ("36'", "goal_overturned") in [(m.minute, m.kind) for m in top.moments]
    at_ht = run(tools.get_moments(static(), match_id=CV, min_importance=3, as_of="HT"))
    ht = next(e.seq for e in match.events if e.type == "period_end" and e.period == 1)
    prefix = [e for e in match.events if e.seq <= ht]
    expected = [m.id for m in detect_all(match.meta, prefix) if m.importance >= 3]
    assert [m.id for m in at_ht.moments] == expected


def test_a_moment_carries_its_facts_and_events() -> None:
    top = run(tools.get_moments(static(), match_id=CV, min_importance=3))
    goal = next(m for m in top.moments if m.kind == "goal")
    assert goal.trigger_event_ids and goal.player_name
    for fact in (f for m in top.moments for f in m.facts):
        assert fact.display and len(fact.evidence_event_ids) <= fact.evidence_total


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"min_importance": 0}, "min_importance"),
        ({"min_importance": 4}, "min_importance"),
        ({"limit": 0}, "between 1 and 50"),
        ({"limit": 51}, "between 1 and 50"),
    ],
)
def test_get_moments_refuses(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ToolInputError, match=message):
        run(tools.get_moments(static(), match_id=CV, **kwargs))


def test_list_players_by_team() -> None:
    both = run(tools.list_players(static(), match_id=DL))
    away = run(tools.list_players(static(), match_id=DL, team="KES"))
    assert len(both.players) == 40
    assert len(away.players) == 20 and {p.team for p in away.players} == {"kestrel_athletic"}
    ellery = next(p for p in away.players if p.id == "kestrel_athletic-11")
    assert (ellery.name, ellery.short_name, ellery.shirt) == ("Jordan Ellery", "J. Ellery", 11)


@pytest.mark.parametrize("match_id", MATCHES)
def test_every_answer_stays_under_64_kb(match_id: str) -> None:
    view = run(static().resolve(match_id, None)).view
    evidence = compute(view, "passes", MetricParams(team=view.meta.team_ids[0])).evidence_event_ids
    answers: list[BaseModel] = [
        run(tools.get_events(static(), match_id=match_id, last=50)),
        run(tools.get_events(static(), match_id=match_id, ids=list(evidence[:50]))),
        run(tools.get_moments(static(), match_id=match_id, min_importance=1, limit=50)),
        run(tools.list_players(static(), match_id=match_id)),
        run(tools.list_matches(static())),
        run(tools.compare_stat(static(), match_id=match_id, metric="passes")),
        tools.list_metrics(),
    ]
    assert max(size(answer) for answer in answers) < LIMIT
