"""Pass quality metrics: distance, difficulty, speed (section 7.3)."""

from dataclasses import dataclass

from pundit.domain.events import PassEvent
from pundit.domain.log import LogView
from pundit.domain.pitch import distance
from pundit.domain.thresholds import DIFFICULT_PASS_THRESHOLD
from pundit.domain.xmodels import difficulty_rating, pass_difficulty
from pundit.stats.registry import MetricParams, make_result, metric
from pundit.stats.result import StatResult
from pundit.stats.select import select


@dataclass(frozen=True)
class PassProfile:
    length_m: float
    difficulty: float  # 0..1
    rating: float  # 1..10
    speed_kmh: float


def pass_profile(event: PassEvent) -> PassProfile:
    assert event.x is not None and event.y is not None
    length = distance(event.x, event.y, event.end_x, event.end_y)
    difficulty = pass_difficulty(
        event.x,
        event.y,
        event.end_x,
        event.end_y,
        under_pressure=event.under_pressure,
        height=event.height,
        is_cross=event.is_cross,
    )
    return PassProfile(
        length_m=length,
        difficulty=difficulty,
        rating=difficulty_rating(difficulty),
        speed_kmh=length / (event.duration_ms / 1000) * 3.6,
    )


def _passes(view: LogView, params: MetricParams) -> list[PassEvent]:
    events = select(
        view, types=("pass",), team=params.team, player=params.player, window=params.window
    )
    return [e for e in events if isinstance(e, PassEvent)]


@metric(
    name="pass_length_avg",
    unit="meters",
    definition="Average pass length in metres, any outcome.",
    description_en="Average pass length",
    description_vi="Cự ly chuyền trung bình",
    supports_player=True,
)
def pass_length_avg(view: LogView, params: MetricParams) -> StatResult:
    passes = _passes(view, params)
    value = sum(pass_profile(p).length_m for p in passes) / len(passes) if passes else None
    return make_result(
        view, "pass_length_avg", params, value=value, evidence=[p.id for p in passes]
    )


@metric(
    name="pass_difficulty_avg",
    unit="rating",
    definition=(
        "Average pass difficulty on a 1-10 scale (1 - completion probability for a neutral team)."
    ),
    description_en="Average pass difficulty",
    description_vi="Độ khó đường chuyền trung bình",
    supports_player=True,
)
def pass_difficulty_avg(view: LogView, params: MetricParams) -> StatResult:
    passes = _passes(view, params)
    value = sum(pass_profile(p).rating for p in passes) / len(passes) if passes else None
    return make_result(
        view, "pass_difficulty_avg", params, value=value, evidence=[p.id for p in passes]
    )


@metric(
    name="difficult_passes_completed",
    unit="count",
    definition=f"Completed passes with difficulty >= {DIFFICULT_PASS_THRESHOLD}.",
    description_en="Difficult passes completed",
    description_vi="Số đường chuyền khó thành công",
    supports_player=True,
)
def difficult_passes_completed(view: LogView, params: MetricParams) -> StatResult:
    ids = [
        p.id
        for p in _passes(view, params)
        if p.outcome == "complete" and pass_profile(p).difficulty >= DIFFICULT_PASS_THRESHOLD
    ]
    return make_result(
        view, "difficult_passes_completed", params, value=float(len(ids)), evidence=ids
    )


@metric(
    name="pass_speed_avg",
    unit="kmh",
    definition="Average ball speed of completed passes (length / duration).",
    description_en="Average pass speed",
    description_vi="Tốc độ bóng trung bình của đường chuyền",
    supports_player=True,
)
def pass_speed_avg(view: LogView, params: MetricParams) -> StatResult:
    completed = [p for p in _passes(view, params) if p.outcome == "complete"]
    value = (
        sum(pass_profile(p).speed_kmh for p in completed) / len(completed) if completed else None
    )
    return make_result(
        view, "pass_speed_avg", params, value=value, evidence=[p.id for p in completed]
    )
