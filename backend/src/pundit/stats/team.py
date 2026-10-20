"""Team counting metrics (docs/day1-design.md section 7.3, P0 table)."""

from collections.abc import Callable

from pundit.domain.events import CardEvent, Event, GoalEvent, PassEvent, ShotEvent, VarCheckEvent
from pundit.domain.log import LogView
from pundit.domain.pitch import FINAL_THIRD_X, in_box
from pundit.stats.registry import MetricParams, make_result, metric
from pundit.stats.result import StatResult
from pundit.stats.select import in_window, select


def _count(
    view: LogView,
    params: MetricParams,
    name: str,
    types: tuple[str, ...],
    keep: Callable[[Event], bool] | None = None,
) -> StatResult:
    events = select(view, types=types, team=params.team, player=params.player, window=params.window)
    ids = [e.id for e in events if keep is None or keep(e)]
    return make_result(view, name, params, value=float(len(ids)), evidence=ids)


@metric(
    name="score",
    unit="count",
    definition="Goals scored, excluding goals overturned by VAR.",
    description_en="Goals scored",
    description_vi="Số bàn thắng",
    supports_player=True,
)
def score(view: LogView, params: MetricParams) -> StatResult:
    return _count(view, params, "score", ("goal",))


@metric(
    name="passes",
    unit="count",
    definition="Passes attempted, any outcome, including restarts.",
    description_en="Passes attempted",
    description_vi="Số đường chuyền",
    supports_player=True,
)
def passes(view: LogView, params: MetricParams) -> StatResult:
    return _count(view, params, "passes", ("pass",))


@metric(
    name="pass_accuracy",
    unit="percent",
    definition="Completed passes / attempted passes x 100.",
    description_en="Pass accuracy",
    description_vi="Tỷ lệ chuyền chính xác",
    supports_player=True,
)
def pass_accuracy(view: LogView, params: MetricParams) -> StatResult:
    attempted = select(
        view, types=("pass",), team=params.team, player=params.player, window=params.window
    )
    completed = sum(1 for e in attempted if isinstance(e, PassEvent) and e.outcome == "complete")
    value = 100.0 * completed / len(attempted) if attempted else None
    return make_result(
        view,
        "pass_accuracy",
        params,
        value=value,
        evidence=[e.id for e in attempted],
        numerator=float(completed),
        denominator=float(len(attempted)),
    )


@metric(
    name="shots",
    unit="count",
    definition="Shots, excluding shots whose goal was overturned by VAR.",
    description_en="Shots",
    description_vi="Số cú sút",
    supports_player=True,
)
def shots(view: LogView, params: MetricParams) -> StatResult:
    return _count(view, params, "shots", ("shot",))


@metric(
    name="shots_on_target",
    unit="count",
    definition="Shots that were scored or saved.",
    description_en="Shots on target",
    description_vi="Số cú sút trúng đích",
    supports_player=True,
)
def shots_on_target(view: LogView, params: MetricParams) -> StatResult:
    return _count(
        view,
        params,
        "shots_on_target",
        ("shot",),
        lambda e: isinstance(e, ShotEvent) and e.outcome in ("goal", "saved"),
    )


@metric(
    name="xg",
    unit="xg",
    definition="Sum of expected goals of shots, excluding shots overturned by VAR.",
    description_en="Expected goals (xG)",
    description_vi="Bàn thắng kỳ vọng (xG)",
    supports_player=True,
)
def xg(view: LogView, params: MetricParams) -> StatResult:
    shots_ = select(
        view, types=("shot",), team=params.team, player=params.player, window=params.window
    )
    total = sum(e.xg for e in shots_ if isinstance(e, ShotEvent))
    return make_result(view, "xg", params, value=total, evidence=[e.id for e in shots_])


def is_final_third_entry(event: Event) -> bool:
    return (
        isinstance(event, PassEvent)
        and event.outcome == "complete"
        and event.x is not None
        and event.x < FINAL_THIRD_X <= event.end_x
    )


def is_box_entry(event: Event) -> bool:
    return (
        isinstance(event, PassEvent)
        and event.outcome == "complete"
        and event.x is not None
        and event.y is not None
        and not in_box(event.x, event.y)
        and in_box(event.end_x, event.end_y)
    )


@metric(
    name="final_third_entries",
    unit="count",
    definition="Completed passes that start before x=70 and end at or beyond x=70.",
    description_en="Final-third entries",
    description_vi="Số lần đưa bóng vào 1/3 cuối sân",
    supports_player=True,
)
def final_third_entries(view: LogView, params: MetricParams) -> StatResult:
    return _count(view, params, "final_third_entries", ("pass",), is_final_third_entry)


@metric(
    name="box_entries",
    unit="count",
    definition="Completed passes from outside the penalty area that end inside it.",
    description_en="Penalty-area entries",
    description_vi="Số lần đưa bóng vào vòng cấm",
    supports_player=True,
)
def box_entries(view: LogView, params: MetricParams) -> StatResult:
    return _count(view, params, "box_entries", ("pass",), is_box_entry)


@metric(
    name="corners",
    unit="count",
    definition="Corner kicks taken (passes with play_type=corner).",
    description_en="Corners",
    description_vi="Số quả phạt góc",
)
def corners(view: LogView, params: MetricParams) -> StatResult:
    return _count(
        view,
        params,
        "corners",
        ("pass",),
        lambda e: isinstance(e, PassEvent) and e.play_type == "corner",
    )


@metric(
    name="fouls",
    unit="count",
    definition="Fouls committed.",
    description_en="Fouls committed",
    description_vi="Số lần phạm lỗi",
    supports_player=True,
)
def fouls(view: LogView, params: MetricParams) -> StatResult:
    return _count(view, params, "fouls", ("foul",))


@metric(
    name="cards",
    unit="count",
    definition="Cards received (yellow, second yellow and red).",
    description_en="Cards",
    description_vi="Số thẻ phạt",
    supports_player=True,
)
def cards(view: LogView, params: MetricParams) -> StatResult:
    return _count(view, params, "cards", ("card",))


@metric(
    name="yellow_cards",
    unit="count",
    definition="Yellow cards received; a second yellow counts as a yellow (and as a red).",
    description_en="Yellow cards",
    description_vi="Số thẻ vàng",
    supports_player=True,
)
def yellow_cards(view: LogView, params: MetricParams) -> StatResult:
    return _count(
        view,
        params,
        "yellow_cards",
        ("card",),
        lambda e: isinstance(e, CardEvent) and e.color in ("yellow", "second_yellow"),
    )


@metric(
    name="red_cards",
    unit="count",
    definition="Sendings-off: straight reds and second yellows.",
    description_en="Red cards",
    description_vi="Số thẻ đỏ",
    supports_player=True,
)
def red_cards(view: LogView, params: MetricParams) -> StatResult:
    return _count(
        view,
        params,
        "red_cards",
        ("card",),
        lambda e: isinstance(e, CardEvent) and e.color in ("second_yellow", "red"),
    )


@metric(
    name="tackles",
    unit="count",
    definition="Tackles attempted, won or lost.",
    description_en="Tackles",
    description_vi="Số pha tắc bóng",
    supports_player=True,
)
def tackles(view: LogView, params: MetricParams) -> StatResult:
    return _count(view, params, "tackles", ("tackle",))


@metric(
    name="interceptions",
    unit="count",
    definition="Passes intercepted.",
    description_en="Interceptions",
    description_vi="Số pha cắt bóng",
    supports_player=True,
)
def interceptions(view: LogView, params: MetricParams) -> StatResult:
    return _count(view, params, "interceptions", ("interception",))


@metric(
    name="offsides",
    unit="count",
    definition="Offsides, including offsides found by VAR.",
    description_en="Offsides",
    description_vi="Số lần việt vị",
    supports_player=True,
)
def offsides(view: LogView, params: MetricParams) -> StatResult:
    return _count(view, params, "offsides", ("offside",))


@metric(
    name="var_overturns",
    unit="count",
    definition="The team's goals overturned by VAR (counted at the VAR decision).",
    description_en="Goals overturned by VAR",
    description_vi="Số bàn thắng bị VAR huỷ",
)
def var_overturns(view: LogView, params: MetricParams) -> StatResult:
    ids: list[str] = []
    for event in view.of_type("var_check"):
        assert isinstance(event, VarCheckEvent)
        if event.check_type != "goal" or event.decision != "overturned":
            continue
        goal = view.get(event.related_ids[0])
        if (
            isinstance(goal, GoalEvent)
            and goal.team == params.team
            and in_window(event, params.window)
        ):
            ids.append(event.id)
    return make_result(view, "var_overturns", params, value=float(len(ids)), evidence=ids)
