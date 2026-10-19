"""Speed and distance metrics (section 7.3, P1 table)."""

from pundit.domain.events import PlayerPhysicalEvent, ShotEvent
from pundit.domain.log import LogView
from pundit.domain.thresholds import SHOT_SPEED_ALERT_KMH, SPEED_ALERT_KMH
from pundit.stats.registry import MetricParams, make_result, metric
from pundit.stats.result import StatResult
from pundit.stats.select import in_window, select


def _snapshots(view: LogView, player: str) -> list[PlayerPhysicalEvent]:
    return [
        e
        for e in view.of_type("player_physical")
        if isinstance(e, PlayerPhysicalEvent) and e.player == player
    ]


@metric(
    name="shot_speed_max",
    unit="kmh",
    definition="Fastest shot (ball speed), excluding shots overturned by VAR.",
    description_en="Fastest shot",
    description_vi="Cú sút mạnh nhất",
    supports_player=True,
)
def shot_speed_max(view: LogView, params: MetricParams) -> StatResult:
    shots = [
        e
        for e in select(
            view, types=("shot",), team=params.team, player=params.player, window=params.window
        )
        if isinstance(e, ShotEvent)
    ]
    if not shots:
        return make_result(view, "shot_speed_max", params, value=None, evidence=[])
    fastest = max(shots, key=lambda e: (e.speed_kmh, -e.seq))
    return make_result(
        view, "shot_speed_max", params, value=fastest.speed_kmh, evidence=[fastest.id]
    )


@metric(
    name="player_distance",
    unit="meters",
    definition="Distance covered inside the window, from cumulative physical snapshots.",
    description_en="Distance covered",
    description_vi="Quãng đường di chuyển",
    scope="player",
)
def player_distance(view: LogView, params: MetricParams) -> StatResult:
    assert params.player is not None
    previous = 0.0
    total = 0.0
    evidence: list[str] = []
    for snap in _snapshots(view, params.player):
        increment = snap.distance_m - previous
        previous = snap.distance_m
        if in_window(snap, params.window):
            total += increment
            evidence.append(snap.id)
    return make_result(
        view, "player_distance", params, value=total if evidence else None, evidence=evidence
    )


@metric(
    name="player_top_speed",
    unit="kmh",
    definition="Top speed reached so far, as of the latest snapshot inside the window.",
    description_en="Top speed",
    description_vi="Tốc độ tối đa",
    scope="player",
)
def player_top_speed(view: LogView, params: MetricParams) -> StatResult:
    assert params.player is not None
    inside = [s for s in _snapshots(view, params.player) if in_window(s, params.window)]
    if not inside:
        return make_result(view, "player_top_speed", params, value=None, evidence=[])
    latest = inside[-1]
    return make_result(
        view, "player_top_speed", params, value=latest.top_speed_kmh, evidence=[latest.id]
    )


@metric(
    name="speed_alerts",
    unit="count",
    definition=(
        f"Players crossing {SPEED_ALERT_KMH:g} km/h for the first time, "
        f"plus shots at {SHOT_SPEED_ALERT_KMH:g} km/h or faster."
    ),
    description_en="Speed alerts",
    description_vi="Cảnh báo tốc độ",
    supports_player=True,
)
def speed_alerts(view: LogView, params: MetricParams) -> StatResult:
    evidence: list[str] = []
    last_top: dict[str, float] = {}
    for snap in view.of_type("player_physical"):
        assert isinstance(snap, PlayerPhysicalEvent) and snap.player is not None
        before = last_top.get(snap.player, 0.0)
        last_top[snap.player] = snap.top_speed_kmh
        crossed = before < SPEED_ALERT_KMH <= snap.top_speed_kmh
        if (
            crossed
            and snap.team == params.team
            and (params.player is None or snap.player == params.player)
            and in_window(snap, params.window)
        ):
            evidence.append(snap.id)
    for shot in select(
        view, types=("shot",), team=params.team, player=params.player, window=params.window
    ):
        if isinstance(shot, ShotEvent) and shot.speed_kmh >= SHOT_SPEED_ALERT_KMH:
            evidence.append(shot.id)
    return make_result(view, "speed_alerts", params, value=float(len(evidence)), evidence=evidence)
