"""Stats tools on hand-written fixtures with hand-calculated expectations (design section 12.3)."""

import pytest
from builders import MatchBuilder
from pydantic import ValidationError

from pundit.domain.errors import UnknownMetricError, UnknownPlayerError, UnknownTeamError
from pundit.domain.matchtime import FULL, H1, H2, MinuteWindow, RollingWindow
from pundit.stats import REGISTRY, MetricParams, StatResult, compare, compute, display
from pundit.stats.passing import pass_profile
from pundit.stats.select import voided_event_ids


def value(
    b: MatchBuilder, name: str, ref_team: str | None = None, **kwargs: object
) -> float | None:
    team = b.team_of(ref_team) if ref_team else None
    return compute(b.view(), name, MetricParams(team=team, **kwargs)).value  # type: ignore[arg-type]


# --------------------------------------------------------------------------- possession


def design_example() -> tuple[MatchBuilder, str, str]:
    """The example from docs/day1-design.md section 12.2."""
    b = MatchBuilder()
    ko = b.at("0:00").kick_off("H9", "H8")
    b.at("0:04").pass_("H8", "H6")
    b.at("0:12").pass_("H6", outcome="incomplete")
    b.at("0:15").interception("A4")
    b.at("0:18").pass_("A4", outcome="out", end_x=60.0, end_y=0.0)
    b.at("0:20").ball_dead("out_of_play")
    ti = b.at("0:30").throw_in("H3", "H7")
    b.at("0:40").pass_("H7", "H9")
    return b, ko.id, ti.id


def test_possession_is_share_of_ball_in_play_time() -> None:
    b, kick_off, throw_in = design_example()
    result = compute(b.view(), "possession", MetricParams(team=b.team_of("H9")))
    assert (result.numerator, result.denominator) == (25_000, 30_000)
    assert result.value == pytest.approx(250 / 3)
    assert result.evidence_event_ids == (kick_off, throw_in)
    away = compute(b.view(), "possession", MetricParams(team=b.team_of("A4")))
    assert away.value == pytest.approx(100 - 250 / 3)


def test_possession_respects_windows() -> None:
    b, _, _ = design_example()
    window = MinuteWindow(start_min=10 / 60, end_min=25 / 60)  # 0:10 - 0:25
    result = compute(b.view(), "possession", MetricParams(team=b.team_of("H9"), window=window))
    assert (result.numerator, result.denominator) == (5_000, 10_000)  # 5 s each; 0:20-0:25 dead
    rolling = RollingWindow(start_ms=35_000, end_ms=60_000)
    assert value(b, "possession", "H9", window=rolling) == 100.0
    assert value(b, "possession_time", "H9") == pytest.approx(25 / 60)
    assert value(b, "ball_in_play_time") == pytest.approx(0.5)


def test_possession_without_play_is_insufficient() -> None:
    b = MatchBuilder()
    result = compute(b.view(), "possession", MetricParams(team=b.team_of("H9")))
    assert result.status == "insufficient_data" and result.value is None


def test_pass_share() -> None:
    b, _, _ = design_example()
    # HAR: kick-off, 2 passes, throw-in, 1 pass = 5; KES: 1 pass
    assert value(b, "pass_share", "H9") == pytest.approx(500 / 6)


# --------------------------------------------------------------------------- counts, VAR


def scoring_match() -> MatchBuilder:
    b = MatchBuilder()
    b.at("0:00").kick_off("H9", "H8")
    b.at("0:05").pass_("H8", "H9", end_x=94.0, end_y=34.0)
    b.at("0:07").shot("H9", outcome="goal")
    b.at("0:07.400").goal(assist="H8")
    b.ball_dead("goal")
    b.at("1:30").kick_off("A9", "A8")
    b.at("1:33").pass_("A8", "A10", end_x=90.0, end_y=30.0)
    b.at("1:35").shot("A10", outcome="goal")
    b.at("1:35.500").goal()
    b.ball_dead("goal")
    b.at("3:00").var_check()
    b.offside("A10", via_var=True)
    b.at("3:20").free_kick("H4", "H6", x=12.0, y=36.0)
    return b


def test_var_overturn_voids_goal_and_shot_but_counts_offside() -> None:
    b = scoring_match()
    voided = voided_event_ids(b.view())
    assert b.last("goal").id in voided and b.last("shot").id in voided
    assert value(b, "score", "H9") == 1 and value(b, "score", "A9") == 0
    assert value(b, "shots", "A9") == 0 and value(b, "xg", "A9") == 0.0
    assert value(b, "offsides", "A9") == 1
    assert value(b, "var_overturns", "A9") == 1 and value(b, "var_overturns", "H9") == 0
    shots = compute(b.view(), "shots_on_target", MetricParams(team=b.team_of("H9")))
    assert shots.value == 1 and shots.evidence_event_ids == (b.events[4].id,)


def test_player_filter() -> None:
    b = scoring_match()
    assert value(b, "passes", "H9", player=b.p("H8")) == 1
    assert value(b, "score", "H9", player=b.p("H9")) == 1


def test_counts_by_window() -> None:
    b = scoring_match()
    assert value(b, "passes", "H9", window=MinuteWindow(end_min=1)) == 2
    assert value(b, "passes", "H9", window=MinuteWindow(start_min=1)) == 1


def test_pass_accuracy_and_entries() -> None:
    b = MatchBuilder()
    b.at("0:00").kick_off("H9", "H8")
    b.at("0:04").pass_("H8", "H7", x=44.0, y=34.0, end_x=75.0, end_y=20.0)  # final third
    b.at("0:08").pass_("H7", "H9", end_x=95.0, end_y=34.0)  # box entry
    b.at("0:10").pass_("H9", outcome="incomplete", end_x=100.0, end_y=40.0)
    assert value(b, "pass_accuracy", "H9") == 75.0
    assert value(b, "final_third_entries", "H9") == 1
    assert value(b, "box_entries", "H9") == 1
    assert value(b, "pass_accuracy", "A9") is None


def test_corners_fouls_cards() -> None:
    b = MatchBuilder()
    b.at("0:00").kick_off("H9", "H8")
    b.at("0:05").foul("A4")
    b.ball_dead("foul")
    b.at("0:10").card("A4")
    b.at("0:40").free_kick("H8", "H7", x=44.0, y=34.0)
    b.at("0:45").pass_("H7", outcome="out", end_x=105.0, end_y=50.0)
    b.at("0:46").ball_dead("out_of_play")
    b.at("1:10").corner("H11", "H5")
    assert value(b, "corners", "H9") == 1
    assert value(b, "fouls", "A9") == 1 and value(b, "cards", "A9") == 1
    assert value(b, "fouls", "H9") == 0


# --------------------------------------------------------------------------- passing


def test_pass_profile() -> None:
    b = MatchBuilder()
    kick = b.at("0:00").kick_off("H9", "H8")
    profile = pass_profile(kick)
    assert profile.length_m == pytest.approx(8.5)
    assert 1.0 <= profile.rating <= 10.0
    assert profile.speed_kmh == pytest.approx(8.5 / (kick.duration_ms / 1000) * 3.6)


def test_pass_quality_metrics() -> None:
    b = MatchBuilder()
    b.at("0:00").kick_off("H9", "H8")
    b.at("0:04").pass_("H8", "H11", x=44.0, y=34.0, end_x=85.0, end_y=60.0, height="high")
    b.at("0:09").pass_("H11", "H9", is_cross=True, height="high", end_x=97.0, end_y=34.0)
    assert value(b, "pass_length_avg", "H9") is not None
    assert value(b, "difficult_passes_completed", "H9") == 1  # the cross
    assert value(b, "pass_speed_avg", "H9") == pytest.approx(15 * 3.6, rel=0.05)
    assert 1.0 <= (value(b, "pass_difficulty_avg", "H9") or 0) <= 10.0


# --------------------------------------------------------------------------- physical


def test_player_distance_uses_increments_inside_the_window() -> None:
    b = MatchBuilder()
    b.at("0:00").kick_off("H9", "H8")
    b.at("5:00").physical("H8", 600.0, 28.0, 2)
    b.at("10:00").physical("H8", 1250.0, 34.0, 5)
    assert value(b, "player_distance", player=b.p("H8")) == 1250.0
    assert value(b, "player_distance", player=b.p("H8"), window=MinuteWindow(start_min=6)) == 650.0
    assert value(b, "player_top_speed", player=b.p("H8")) == 34.0
    assert value(b, "player_distance", player=b.p("H9")) is None
    assert value(b, "speed_alerts", "H9") == 1


def test_shot_speed() -> None:
    b = scoring_match()
    assert value(b, "shot_speed_max", "H9") == 90.0
    assert value(b, "shot_speed_max", "A9") is None  # the only shot was voided
    assert value(b, "speed_alerts", "H9") == 0


# --------------------------------------------------------------------------- tactical


def test_ppda() -> None:
    b = MatchBuilder()
    b.at("0:00").kick_off("H9", "H8")
    b.at("0:04").pass_("H8", "H6", x=44.0, y=34.0, end_x=40.0, end_y=20.0)  # in HAR's own 60%
    b.at("0:08").pass_("H6", "H5", end_x=30.0, end_y=30.0)
    b.at("0:10").pressure("A9")
    b.at("0:11").tackle("A9", won=True)
    result = compute(b.view(), "ppda", MetricParams(team=b.team_of("A9")))
    assert (result.numerator, result.denominator) == (3, 1)
    assert value(b, "ppda", "H9") is None


def test_momentum_sign_and_range() -> None:
    b = scoring_match()
    home = value(b, "momentum", "H9")
    away = value(b, "momentum", "A9")
    assert home is not None and away is not None
    assert -1.0 <= home <= 1.0 and home == pytest.approx(-away)
    assert home > 0  # HAR's goal counts, KES's overturned shot does not


def test_chaos_and_tempo() -> None:
    b, _, _ = design_example()
    assert compute(b.view(), "chaos_index", MetricParams()).status == "insufficient_data"
    tempo = compute(b.view(), "tempo", MetricParams(team=b.team_of("H9")))
    assert tempo.numerator == 5 and tempo.denominator == pytest.approx(25 / 60)


# --------------------------------------------------------------------------- registry


def test_registry_checks_params() -> None:
    b, _, _ = design_example()
    view = b.view()
    with pytest.raises(UnknownMetricError):
        compute(view, "goals_scored", MetricParams(team=b.team_of("H9")))
    with pytest.raises(ValueError):
        compute(view, "possession", MetricParams())
    with pytest.raises(ValueError):
        compute(view, "ball_in_play_time", MetricParams(team=b.team_of("H9")))
    with pytest.raises(ValueError):
        compute(view, "player_distance", MetricParams())
    with pytest.raises(ValueError):
        compute(view, "possession", MetricParams(team=b.team_of("H9"), player=b.p("H9")))
    with pytest.raises(ValueError):
        compute(view, "passes", MetricParams(team=b.team_of("H9"), player=b.p("A4")))
    with pytest.raises(UnknownTeamError):
        compute(view, "passes", MetricParams(team="nobody"))
    with pytest.raises(UnknownPlayerError):
        compute(view, "passes", MetricParams(team=b.team_of("H9"), player="harbor_lions-77"))


def test_every_metric_runs_on_an_empty_match() -> None:
    b = MatchBuilder()
    for name, spec in REGISTRY.items():
        params = {
            "team": MetricParams(team=b.team_of("H9")),
            "match": MetricParams(),
            "player": MetricParams(player=b.p("H9")),
        }[spec.scope]
        result = compute(b.view(), name, params)
        assert result.metric == name and result.unit == spec.unit


def test_compare() -> None:
    b = scoring_match()
    comparison = compare(b.view(), "passes", FULL)
    assert comparison.diff == comparison.home.value - comparison.away.value  # type: ignore[operator]
    assert compare(b.view(), "score", H2).ratio is None  # 0/0


def test_stat_result_rules() -> None:
    base = {
        "metric": "x",
        "team": None,
        "player": None,
        "window": H1,
        "unit": "count",
        "status": "ok",
        "as_of_seq": 3,
        "definition": "d",
    }
    StatResult(**base, value=2.0, evidence_event_ids=("m-1-00001", "m-1-00002"))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):  # count != evidence
        StatResult(**base, value=3.0, evidence_event_ids=("m-1-00001",))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):  # unsorted evidence
        StatResult(**base, value=2.0, evidence_event_ids=("m-1-00002", "m-1-00001"))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):  # ok without value
        StatResult(**base, value=None, evidence_event_ids=())  # type: ignore[arg-type]
    with pytest.raises(ValidationError):  # percent out of range
        StatResult(**{**base, "unit": "percent"}, value=101.0, evidence_event_ids=())  # type: ignore[arg-type]


def test_display() -> None:
    b, _, _ = design_example()
    result = compute(b.view(), "possession", MetricParams(team=b.team_of("H9")))
    assert display(result) == "83%"
    empty = compute(MatchBuilder().view(), "possession", MetricParams(team=b.team_of("H9")))
    assert display(empty) == "n/a"
