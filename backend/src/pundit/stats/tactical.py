"""Tactical metrics: PPDA, momentum, control vs chaos, tempo (section 7.3, P1 table)."""

from pundit.domain.events import (
    BallRecoveryEvent,
    Event,
    InterceptionEvent,
    PassEvent,
    ShotEvent,
    TackleEvent,
)
from pundit.domain.log import LogView
from pundit.domain.matchtime import MS_PER_MINUTE, RollingWindow
from pundit.domain.pitch import FINAL_THIRD_X, HALFWAY_X, PPDA_OPPONENT_MAX_X, PPDA_ZONE_X, in_box
from pundit.stats.possession import possession_timeline, possession_totals
from pundit.stats.registry import MetricParams, make_result, metric
from pundit.stats.result import StatResult
from pundit.stats.select import in_window, select

# Control vs chaos normalisation bounds: 10th-90th percentiles of 10-minute windows in
# balanced generated matches (docs, section 7.3). Label thresholds were checked on halves:
# balanced halves sit around 0.35-0.47, one-sided spells below 0.15.
CHAOS_TRANSITIONS_PER_MIN = (2.4, 4.0)
CHAOS_PASSES_PER_SPELL = (4.2, 6.9)
CHAOS_DUELS_PER_MIN = (0.56, 1.45)
CHAOS_CONTROL_BELOW = 0.25
CHAOS_CHAOS_ABOVE = 0.55


@metric(
    name="ppda",
    unit="ratio",
    definition=(
        "Passes allowed per defensive action: opponent passes in their own defensive 60% / "
        "the team's tackles, interceptions and fouls in its attacking 60%. Lower = more pressing."
    ),
    description_en="PPDA (pressing intensity)",
    description_vi="PPDA (cường độ pressing)",
)
def ppda(view: LogView, params: MetricParams) -> StatResult:
    assert params.team is not None
    opponent = view.meta.opponent(params.team)
    allowed = [
        e
        for e in select(view, types=("pass",), team=opponent, window=params.window)
        if e.x is not None and e.x <= PPDA_OPPONENT_MAX_X
    ]
    actions = [
        e
        for e in select(
            view, types=("tackle", "interception", "foul"), team=params.team, window=params.window
        )
        if e.x is not None and e.x >= PPDA_ZONE_X
    ]
    value = len(allowed) / len(actions) if actions else None
    return make_result(
        view,
        "ppda",
        params,
        value=value,
        evidence=[e.id for e in actions],
        numerator=float(len(allowed)),
        denominator=float(len(actions)),
    )


def threat_points(event: Event) -> float:
    """Contribution of one event to its team's attacking threat."""
    if isinstance(event, ShotEvent):
        return 1.0 + 4.0 * event.xg
    if isinstance(event, PassEvent):
        points = 0.5 if event.play_type == "corner" else 0.0
        if event.outcome == "complete" and event.x is not None and event.y is not None:
            if event.x < FINAL_THIRD_X <= event.end_x:
                points += 0.6
            if not in_box(event.x, event.y) and in_box(event.end_x, event.end_y):
                points += 1.0
        return points
    won_ball = isinstance(event, InterceptionEvent | BallRecoveryEvent) or (
        isinstance(event, TackleEvent) and event.outcome == "won"
    )
    if won_ball and event.x is not None and event.x >= HALFWAY_X:
        return 0.4
    return 0.0


@metric(
    name="momentum",
    unit="index",
    definition=(
        "Share of attacking threat in the window: (team - opponent) / (team + opponent), "
        "from -1 to 1. Threat: shot 1+4xG, final-third entry 0.6, box entry 1.0, corner 0.5, "
        "ball won in the opponent half 0.4."
    ),
    description_en="Momentum",
    description_vi="Thế trận (momentum)",
)
def momentum(view: LogView, params: MetricParams) -> StatResult:
    assert params.team is not None
    events = select(
        view,
        types=("shot", "pass", "interception", "ball_recovery", "tackle"),
        window=params.window,
    )
    team_points = 0.0
    opponent_points = 0.0
    evidence: list[str] = []
    for event in events:
        points = threat_points(event)
        if points <= 0:
            continue
        evidence.append(event.id)
        if event.team == params.team:
            team_points += points
        else:
            opponent_points += points
    total = team_points + opponent_points
    value = (team_points - opponent_points) / total if total > 0 else 0.0
    return make_result(
        view,
        "momentum",
        params,
        value=value,
        evidence=evidence,
        numerator=team_points,
        denominator=total,
    )


def momentum_series(
    view: LogView, team: str, *, window_minutes: float = 10.0
) -> list[tuple[int, float]]:
    """Momentum at the end of every played minute, over a rolling window (for charts)."""
    last = view.last
    if last is None:
        return []
    series: list[tuple[int, float]] = []
    for minute in range(1, last.match_ms // MS_PER_MINUTE + 2):
        end_ms = minute * MS_PER_MINUTE
        window = RollingWindow(
            start_ms=max(0, end_ms - round(window_minutes * MS_PER_MINUTE)), end_ms=end_ms
        )
        result = momentum(view, MetricParams(team=team, window=window))
        assert result.value is not None
        series.append((minute, result.value))
    return series


def _norm(value: float, bounds: tuple[float, float]) -> float:
    low, high = bounds
    return max(0.0, min(1.0, (value - low) / (high - low)))


def spell_starts(view: LogView, params: MetricParams) -> list[str]:
    """First event of every spell in the window: a spell is consecutive possessions of one team,
    so a restart by the team that already had the ball (corner, throw-in) is not a transition."""
    starts: list[str] = []
    previous_team: str | None = None
    for interval in possession_timeline(view):
        if not in_window(view.get(interval.start_event_id), params.window):
            continue
        if interval.team != previous_team:
            starts.append(interval.start_event_id)
        previous_team = interval.team
    return starts


@metric(
    name="chaos_index",
    unit="index",
    definition=(
        "Control vs chaos, 0..1: structure x (1 - dominance). Structure = 0.5*transitions "
        "per minute of ball in play + 0.3*(1 - passes per spell) + 0.2*duels per minute, "
        "each normalised; dominance = |momentum| in the window. Chaos needs frequent changes "
        "of possession AND no side in control. Label: control < 0.25, chaos > 0.55."
    ),
    description_en="Control vs chaos index",
    description_vi="Chỉ số kiểm soát / hỗn loạn",
    scope="match",
)
def chaos_index(view: LogView, params: MetricParams) -> StatResult:
    totals = possession_totals(view, params.window)
    in_play_minutes = totals.total_ms / MS_PER_MINUTE
    starts = spell_starts(view, params)
    if in_play_minutes < 1.0 or not starts:
        return make_result(view, "chaos_index", params, value=None, evidence=[])
    passes = len(select(view, types=("pass",), window=params.window))
    duels = len(select(view, types=("foul", "tackle"), window=params.window))
    structure = (
        0.5 * _norm((len(starts) - 1) / in_play_minutes, CHAOS_TRANSITIONS_PER_MIN)
        + 0.3 * (1.0 - _norm(passes / len(starts), CHAOS_PASSES_PER_SPELL))
        + 0.2 * _norm(duels / in_play_minutes, CHAOS_DUELS_PER_MIN)
    )
    home = view.meta.team_ids[0]
    swing = momentum(view, MetricParams(team=home, window=params.window)).value
    dominance = abs(swing) if swing is not None else 0.0
    value = structure * (1.0 - dominance)
    if value < CHAOS_CONTROL_BELOW:
        label = "control"
    elif value > CHAOS_CHAOS_ABOVE:
        label = "chaos"
    else:
        label = "balanced"
    return make_result(view, "chaos_index", params, value=value, evidence=starts, label=label)


@metric(
    name="tempo",
    unit="ratio",
    definition="Passes per minute of the team's own possession time.",
    description_en="Tempo (passes per minute in possession)",
    description_vi="Nhịp độ (số đường chuyền mỗi phút giữ bóng)",
)
def tempo(view: LogView, params: MetricParams) -> StatResult:
    assert params.team is not None
    totals = possession_totals(view, params.window)
    minutes = totals.team_ms[params.team] / MS_PER_MINUTE
    passes = select(view, types=("pass",), team=params.team, window=params.window)
    value = len(passes) / minutes if minutes > 0 else None
    return make_result(
        view,
        "tempo",
        params,
        value=value,
        evidence=[e.id for e in passes],
        numerator=float(len(passes)),
        denominator=minutes,
    )
