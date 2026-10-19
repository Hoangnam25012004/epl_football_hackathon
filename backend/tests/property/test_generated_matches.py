"""Property tests over many generated matches (design section 12.4: M1-M13, INV-xx)."""

import pytest
from helpers import SCENARIO_KEYS, SEEDS, generated

from pundit.domain.events import PlayerPhysicalEvent
from pundit.domain.log import LogView
from pundit.domain.matchtime import FULL, H1, H2, MinuteWindow, RollingWindow
from pundit.engine.rosters import load_rosters
from pundit.engine.validate import validate_match
from pundit.state.match_state import MatchState
from pundit.stats import REGISTRY, MetricParams, compute
from pundit.stats.possession import possession_timeline

CASES = [(scenario, seed) for scenario in SCENARIO_KEYS for seed in SEEDS]
COUNT_METRICS = [n for n, s in REGISTRY.items() if s.unit == "count" and s.scope == "team"]


@pytest.mark.parametrize(("scenario", "seed"), CASES)
def test_every_invariant_holds(scenario: str, seed: int) -> None:  # M10
    match = generated(scenario, seed)
    validate_match(match.meta, match.events)
    names = {(t.id, p.shirt): p.name for t in load_rosters().teams for p in t.players}
    for info in match.meta.players.values():
        assert names[(info.team_id, info.shirt)] == info.name  # INV-21


@pytest.mark.parametrize(("scenario", "seed"), CASES)
def test_possession_adds_up_to_100(scenario: str, seed: int) -> None:  # M1
    match = generated(scenario, seed)
    view = match.view()
    home, away = match.meta.team_ids
    for window in (FULL, H1, H2, MinuteWindow(start_min=30, end_min=60)):
        h = compute(view, "possession", MetricParams(team=home, window=window)).value
        a = compute(view, "possession", MetricParams(team=away, window=window)).value
        assert h is not None and a is not None
        assert h + a == pytest.approx(100.0)


def _amount(view: LogView, name: str, team: str, window: MinuteWindow) -> float:
    """Additive quantity of a metric: the count, or the milliseconds for possession_time."""
    result = compute(view, name, MetricParams(team=team, window=window))
    raw = result.numerator if name == "possession_time" else result.value
    return raw or 0.0


@pytest.mark.parametrize(("scenario", "seed"), CASES)
def test_counts_and_time_are_additive_over_windows(scenario: str, seed: int) -> None:  # M2, M11
    match = generated(scenario, seed)
    view = match.view()
    thirds = (
        MinuteWindow(end_min=30),
        MinuteWindow(start_min=30, end_min=60),
        MinuteWindow(start_min=60),
    )
    for team in match.meta.team_ids:
        for name in [*COUNT_METRICS, "possession_time"]:
            whole = _amount(view, name, team, FULL)
            halves = sum(_amount(view, name, team, w) for w in (H1, H2))
            parts = sum(_amount(view, name, team, w) for w in thirds)
            assert halves == pytest.approx(whole) == pytest.approx(parts), name


@pytest.mark.parametrize(("scenario", "seed"), CASES[::4])
def test_bigger_windows_never_count_less(scenario: str, seed: int) -> None:  # M3
    view = generated(scenario, seed).view()
    team = view.meta.team_ids[0]
    for name in COUNT_METRICS:
        small = compute(
            view, name, MetricParams(team=team, window=MinuteWindow(start_min=20, end_min=40))
        )
        big = compute(
            view, name, MetricParams(team=team, window=MinuteWindow(start_min=10, end_min=70))
        )
        assert (small.value or 0) <= (big.value or 0), name


@pytest.mark.parametrize(("scenario", "seed"), CASES[::2])
def test_evidence_is_consistent(scenario: str, seed: int) -> None:  # M4
    view = generated(scenario, seed).view()
    window = MinuteWindow(start_min=15, end_min=75)
    for team in view.meta.team_ids:
        for name, spec in REGISTRY.items():
            if spec.scope != "team":
                continue
            result = compute(view, name, MetricParams(team=team, window=window))
            for event_id in result.evidence_event_ids:
                assert view.has(event_id)
            if spec.unit == "count":
                assert result.value == len(result.evidence_event_ids)
                for event_id in result.evidence_event_ids:
                    event = view.get(event_id)
                    assert window.contains(event.period, event.clock_ms, event.match_ms), name


@pytest.mark.parametrize(("scenario", "seed"), CASES[::4])
def test_home_and_away_labels_do_not_matter(scenario: str, seed: int) -> None:  # M5
    match = generated(scenario, seed)
    swapped_meta = match.meta.model_copy(update={"home": match.meta.away, "away": match.meta.home})
    original = match.view()
    swapped = LogView.from_events(swapped_meta, match.events)
    for team in match.meta.team_ids:
        for name, spec in REGISTRY.items():
            if spec.scope == "team":
                a = compute(original, name, MetricParams(team=team, window=H2)).value
                b = compute(swapped, name, MetricParams(team=team, window=H2)).value
                assert a == b, name


@pytest.mark.parametrize(("scenario", "seed"), CASES[::4])
def test_live_state_matches_batch_stats_at_every_prefix(scenario: str, seed: int) -> None:  # M6, M7
    match = generated(scenario, seed)
    state = MatchState(match.meta)
    checkpoints = set(range(50, len(match.events), 211))
    for index, event in enumerate(match.events, start=1):
        state.apply(event)
        if index not in checkpoints:
            continue
        snapshot = state.snapshot()
        prefix = LogView.from_events(match.meta, match.events[:index])
        for team in match.meta.team_ids:
            score = compute(prefix, "score", MetricParams(team=team)).value
            possession = compute(prefix, "possession", MetricParams(team=team))
            assert snapshot.score[team] == score
            assert snapshot.possession_ms[team] == possession.numerator
            live = compute(state.view(), "possession", MetricParams(team=team))
            assert live == possession


@pytest.mark.parametrize(("scenario", "seed"), CASES)
def test_ball_in_play_plus_dead_time_equals_period_length(scenario: str, seed: int) -> None:  # M12
    view = generated(scenario, seed).view()
    intervals = possession_timeline(view)
    for period in (1, 2):
        events = [e for e in view if e.period == period and e.type != "match_end"]
        start = next(e.match_ms for e in events if e.type == "period_start")
        end = next(e.match_ms for e in events if e.type == "period_end")
        in_play = sum(i.duration_ms for i in intervals if i.period == period)
        dead = 0
        dead_since: int | None = start
        for event in events:
            restart = (
                event.type == "pass" and getattr(event, "play_type", "open_play") != "open_play"
            )
            if dead_since is not None and restart:
                dead += event.match_ms - dead_since
                dead_since = None
            elif dead_since is None and event.type == "ball_dead":
                dead_since = event.match_ms
        if dead_since is not None:
            dead += end - dead_since
        assert in_play + dead == end - start


@pytest.mark.parametrize("seed", SEEDS)
def test_dominant_side_controls_by_time_and_by_passes(seed: int) -> None:  # M13
    view = generated("dominant_loser", seed).view()
    home = view.meta.team_ids[0]
    assert (compute(view, "possession", MetricParams(team=home, window=H2)).value or 0) >= 55
    assert (compute(view, "pass_share", MetricParams(team=home, window=H2)).value or 0) >= 55


@pytest.mark.parametrize(("scenario", "seed"), CASES[::3])
def test_physical_snapshots_are_plausible(scenario: str, seed: int) -> None:
    match = generated(scenario, seed)
    snapshots = [e for e in match.events if isinstance(e, PlayerPhysicalEvent)]
    assert snapshots
    finals: dict[str, float] = {}
    for snap in snapshots:
        assert snap.top_speed_kmh <= 37.0
        finals[snap.player or ""] = snap.distance_m
    starters = {p for t in match.meta.team_ids for p in match.meta.sheet(t).starting_xi}
    full_game = [d for p, d in finals.items() if p in starters]
    assert max(full_game) < 14_500 and min(full_game) > 3_000


@pytest.mark.parametrize(("scenario", "seed"), CASES[::4])
def test_rolling_momentum_stays_in_range(scenario: str, seed: int) -> None:
    from pundit.stats.tactical import momentum_series

    view = generated(scenario, seed).view()
    series = momentum_series(view, view.meta.team_ids[0])
    assert len(series) >= 90
    assert all(-1.0 <= value <= 1.0 for _, value in series)
    last = view.last
    assert last is not None
    window = RollingWindow.last(10, last.match_ms)
    result = compute(view, "momentum", MetricParams(team=view.meta.team_ids[0], window=window))
    assert result.value is not None
