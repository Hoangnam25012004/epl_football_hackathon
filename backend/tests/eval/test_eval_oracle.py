"""The independent counts behind the eval labels (docs/day6-design.md 4.3, 10.2): they agree
with the Stats Tools for every counted metric, team and window, at several moments of the
three eval matches. A difference would be either a Stats Tools bug or a definition to write on
the label sheet; there is none."""

import pytest
from helpers import generated

from pundit.domain.log import LogView
from pundit.domain.matchtime import MinuteWindow, parse_window
from pundit.orchestration.eval_oracle import COUNTED, count, score_at, span, voided
from pundit.orchestration.evaluation import seq_at
from pundit.stats import MetricParams, compute

MATCHES = (("dominant_loser", 42), ("comeback_var", 7), ("red_card_swing", 7))
WINDOWS = ("FULL", "H1", "H2", "-30", "60-", "30-75")


def moments(scenario: str, seed: int) -> list[int]:
    """Seqs to count at: half-time, 70' and the end."""
    events = generated(scenario, seed).events
    ht = next(e.seq for e in events if e.type == "period_end" and e.period == 1)
    return [ht, seq_at(events, "70'") or 0, events[-1].seq]


@pytest.mark.parametrize(("scenario", "seed"), MATCHES)
def test_every_count_agrees_with_the_stats_tools(scenario: str, seed: int) -> None:
    match = generated(scenario, seed)
    wrong: list[str] = []
    checked = 0
    for seq in moments(scenario, seed):
        prefix = match.events[: seq + 1]
        view = LogView.from_events(match.meta, prefix)
        for metric in COUNTED:
            for team in match.meta.team_ids:
                for window in WINDOWS:
                    params = MetricParams(team=team, window=parse_window(window))
                    expected = compute(view, metric, params).value
                    checked += 1
                    if count(prefix, metric, team, window) != expected:
                        wrong.append(f"{metric} {team} {window} at seq {seq}")
    assert wrong == []
    assert checked == 3 * len(COUNTED) * 2 * len(WINDOWS)


@pytest.mark.parametrize(("scenario", "seed"), MATCHES)
def test_player_counts_agree(scenario: str, seed: int) -> None:
    match = generated(scenario, seed)
    view = LogView.from_events(match.meta, match.events)
    shooters = {e.player for e in match.events if e.type == "shot" and e.player}
    for player in sorted(shooters):
        team = player.rsplit("-", 1)[0]
        for metric in ("shots", "score", "shots_on_target"):
            expected = compute(view, metric, MetricParams(team=team, player=player)).value
            assert count(match.events, metric, team, player=player) == expected, (metric, player)


def test_a_goal_overturned_by_var_does_not_count() -> None:
    match = generated("comeback_var", 7)
    home, away = match.meta.team_ids
    events = match.events
    goals_h1 = [e for e in events if e.type == "goal" and e.team == away and e.period == 1]
    assert len(goals_h1) == 2  # Saltmarsh scored twice before the break; VAR took one away
    assert count(events, "score", away, "H1") == 1
    assert count(events, "var_overturns", away) == 1 and count(events, "var_overturns", home) == 0
    gone = voided(events)
    assert goals_h1[1].id in gone and len(gone) == 2  # the goal and the shot that scored it
    # before the VAR decision the goal stands
    decision = next(e for e in events if e.type == "var_check" and e.decision == "overturned")
    before = events[: decision.seq]
    assert count(before, "score", away, "H1") == 2 and voided(before) == set()


def test_the_score_at_each_moment() -> None:
    match = generated("comeback_var", 7)
    teams = match.meta.team_ids
    at = {minute: seq_at(match.events, minute) for minute in ("45'", "55'", "70'", "85'")}
    scores = {m: score_at(match.events[: (s or 0) + 1], teams) for m, s in at.items()}
    assert scores == {"45'": (0, 1), "55'": (0, 2), "70'": (1, 2), "85'": (2, 2)}
    assert score_at(match.events, teams) == (3, 2)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("FULL", (None, None, None)),
        ("h1", (1, None, None)),
        ("H2", (2, None, None)),
        ("60-75", (None, 60.0, 75.0)),
        ("75-", (None, 75.0, None)),
        ("-30", (None, None, 30.0)),
    ],
)
def test_window_labels(label: str, expected: tuple[int | None, float | None, float | None]) -> None:
    found = span(label)
    assert (found.period, found.start_min, found.end_min) == expected
    mine, theirs = span(label), parse_window(label)
    if isinstance(theirs, MinuteWindow):
        assert (mine.start_min, mine.end_min) == (theirs.start_min, theirs.end_min)


@pytest.mark.parametrize("label", ["", "half", "-", "abc-def"])
def test_bad_window_labels(label: str) -> None:
    with pytest.raises(ValueError, match="not a window"):
        span(label)


def test_model_metrics_have_no_independent_count() -> None:
    events = generated("dominant_loser", 42).events
    with pytest.raises(ValueError, match="no independent count"):
        count(events, "possession", "harbor_lions")
