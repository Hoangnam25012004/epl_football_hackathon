"""Possession measured in ball-in-play time (docs/day1-design.md sections 4.4 and 7.3)."""

from dataclasses import dataclass

from pundit.domain.ballstate import INITIAL_BALL_STATE, PossessionInterval, step
from pundit.domain.log import LogView
from pundit.domain.matchtime import FULL, MS_PER_MINUTE, MinuteWindow, RollingWindow, overlap_ms
from pundit.stats.registry import MetricParams, make_result, metric
from pundit.stats.result import StatResult
from pundit.stats.select import select


def possession_timeline(view: LogView) -> tuple[PossessionInterval, ...]:
    """All possession intervals in the view. A running possession is closed at the last event."""
    state = INITIAL_BALL_STATE
    intervals: list[PossessionInterval] = []
    for event in view:
        state, closed = step(state, event)
        if closed is not None:
            intervals.append(closed)
    last = view.last
    if state.holder is not None and last is not None:
        assert state.start_event_id is not None and state.period in (1, 2)
        intervals.append(
            PossessionInterval(
                team=state.holder,
                possession_id=state.possession_id,
                period=state.period,
                period_start_ms=state.period_start_ms,
                start_ms=state.since_ms,
                end_ms=last.match_ms,
                start_event_id=state.start_event_id,
                end_event_id=None,
            )
        )
    return tuple(intervals)


@dataclass(frozen=True)
class PossessionTotals:
    team_ms: dict[str, int]
    total_ms: int
    evidence: dict[str, list[str]]  # possession-opening event ids per team


def possession_totals(
    view: LogView, window: MinuteWindow | RollingWindow = FULL
) -> PossessionTotals:
    team_ms = dict.fromkeys(view.meta.team_ids, 0)
    evidence: dict[str, list[str]] = {team: [] for team in view.meta.team_ids}
    for interval in possession_timeline(view):
        overlap = overlap_ms(
            window,
            period=interval.period,
            period_start_ms=interval.period_start_ms,
            start_ms=interval.start_ms,
            end_ms=interval.end_ms,
        )
        if overlap > 0:
            team_ms[interval.team] += overlap
            evidence[interval.team].append(interval.start_event_id)
    return PossessionTotals(team_ms=team_ms, total_ms=sum(team_ms.values()), evidence=evidence)


@metric(
    name="possession",
    unit="percent",
    definition=(
        "Time the team had the ball / total ball-in-play time x 100. Dead-ball time is excluded."
    ),
    description_en="Possession (ball-in-play time)",
    description_vi="Kiểm soát bóng (theo thời gian bóng lăn)",
)
def possession(view: LogView, params: MetricParams) -> StatResult:
    assert params.team is not None
    totals = possession_totals(view, params.window)
    team_ms = totals.team_ms[params.team]
    value = 100.0 * team_ms / totals.total_ms if totals.total_ms > 0 else None
    return make_result(
        view,
        "possession",
        params,
        value=value,
        evidence=totals.evidence[params.team],
        numerator=float(team_ms),
        denominator=float(totals.total_ms),
    )


@metric(
    name="possession_time",
    unit="minutes",
    definition="Minutes of ball-in-play time with the team in possession.",
    description_en="Time in possession",
    description_vi="Thời gian giữ bóng",
)
def possession_time(view: LogView, params: MetricParams) -> StatResult:
    assert params.team is not None
    totals = possession_totals(view, params.window)
    team_ms = totals.team_ms[params.team]
    return make_result(
        view,
        "possession_time",
        params,
        value=team_ms / MS_PER_MINUTE,
        evidence=totals.evidence[params.team],
        numerator=float(team_ms),
        denominator=float(totals.total_ms),
    )


@metric(
    name="ball_in_play_time",
    unit="minutes",
    definition="Minutes the ball was in play (both teams).",
    description_en="Ball-in-play time",
    description_vi="Thời gian bóng lăn",
    scope="match",
)
def ball_in_play_time(view: LogView, params: MetricParams) -> StatResult:
    totals = possession_totals(view, params.window)
    evidence = [eid for ids in totals.evidence.values() for eid in ids]
    return make_result(
        view,
        "ball_in_play_time",
        params,
        value=totals.total_ms / MS_PER_MINUTE,
        evidence=evidence,
        numerator=float(totals.total_ms),
    )


@metric(
    name="pass_share",
    unit="percent",
    definition="Opta-style possession: team passes / all passes x 100. For comparison only.",
    description_en="Pass share",
    description_vi="Tỷ lệ đường chuyền (đối chiếu)",
)
def pass_share(view: LogView, params: MetricParams) -> StatResult:
    every_pass = select(view, types=("pass",), window=params.window)
    team_passes = [e.id for e in every_pass if e.team == params.team]
    value = 100.0 * len(team_passes) / len(every_pass) if every_pass else None
    return make_result(
        view,
        "pass_share",
        params,
        value=value,
        evidence=team_passes,
        numerator=float(len(team_passes)),
        denominator=float(len(every_pass)),
    )
