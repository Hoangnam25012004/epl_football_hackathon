"""Words for windows, metrics, values and names in VI / EN.

Shared by facts, templates and moments, so a number is always written the same way. Values use
the rounding of Day 1 `display()`; decimals use a dot in both languages (xG 2.24).
"""

from pundit.agents.contracts import Lang, MatchContext, Subject
from pundit.agents.timescope import rolling_minutes
from pundit.domain.matchtime import FULL, H1, H2, MinuteWindow, RollingWindow
from pundit.stats.registry import REGISTRY
from pundit.stats.result import Unit

Window = MinuteWindow | RollingWindow

# Count metrics: (Vietnamese noun, English noun)
COUNT_NOUNS: dict[str, tuple[str, str]] = {
    "shots": ("cú sút", "shots"),
    "shots_on_target": ("cú sút trúng đích", "shots on target"),
    "score": ("bàn thắng", "goals"),
    "corners": ("quả phạt góc", "corners"),
    "fouls": ("lần phạm lỗi", "fouls"),
    "cards": ("thẻ phạt", "cards"),
    "yellow_cards": ("thẻ vàng", "yellow cards"),
    "red_cards": ("thẻ đỏ", "red cards"),
    "offsides": ("lần việt vị", "offsides"),
    "passes": ("đường chuyền", "passes"),
    "box_entries": ("lần đưa bóng vào vòng cấm", "box entries"),
    "final_third_entries": ("lần đưa bóng vào phần ba cuối sân", "final-third entries"),
    "tackles": ("pha tắc bóng", "tackles"),
    "interceptions": ("pha cắt bóng", "interceptions"),
    "difficult_passes_completed": ("đường chuyền khó thành công", "difficult passes completed"),
    "speed_alerts": ("lần vượt ngưỡng tốc độ", "speed alerts"),
    "var_overturns": ("bàn bị VAR huỷ", "goals overturned by VAR"),
}


# English singular of the count nouns, for "1 shot"
SINGULAR_EN: dict[str, str] = {
    "shots": "shot",
    "shots on target": "shot on target",
    "goals": "goal",
    "corners": "corner",
    "fouls": "foul",
    "cards": "card",
    "yellow cards": "yellow card",
    "red cards": "red card",
    "offsides": "offside",
    "passes": "pass",
    "box entries": "box entry",
    "final-third entries": "final-third entry",
    "tackles": "tackle",
    "interceptions": "interception",
    "difficult passes completed": "difficult pass completed",
    "speed alerts": "speed alert",
    "goals overturned by VAR": "goal overturned by VAR",
}


def number_text(value: float, unit: Unit) -> str:
    """The number of `display()` without its unit suffix."""
    match unit:
        case "count" | "percent":
            return f"{value:.0f}"
        case "meters" | "kmh" | "minutes" | "rating":
            return f"{value:.1f}"
        case _:
            return f"{value:.2f}"


def format_value(value: float, unit: Unit) -> str:
    """Same text as Day 1 `display()` for a StatResult with this value and unit."""
    number = number_text(value, unit)
    suffix = {"percent": "%", "meters": " m", "kmh": " km/h", "minutes": " min", "rating": "/10"}
    return number + suffix.get(unit, "")


def ratio_text(value: float) -> str:
    """15.0 -> "15"; 1.875 -> "1.9"."""
    return f"{value:.0f}" if abs(value - round(value)) < 0.05 else f"{value:.1f}"


def minute_text(value: float) -> str:
    return f"{value:g}"


def as_of_minute(ctx: MatchContext) -> str:
    """ "63'" -> "63"; "45+2'" -> "45+2"."""
    return ctx.clock_display.rstrip("'")


def window_label(window: Window, lang: Lang) -> str:
    """Mid-sentence name of a window: "hiệp 2" / "the second half"."""
    vi = lang == "vi"
    if isinstance(window, RollingWindow):
        n = rolling_minutes(window)
        return f"{n} phút gần nhất" if vi else f"the last {n} minutes"
    if window == FULL:
        return "cả trận" if vi else "the match"
    if window == H1:
        return "hiệp 1" if vi else "the first half"
    if window == H2:
        return "hiệp 2" if vi else "the second half"
    start, end = window.start_min, window.end_min
    if start is not None and end is not None:
        text = (
            f"từ phút {minute_text(start)} đến phút {minute_text(end)}"
            if vi
            else f"from minute {minute_text(start)} to minute {minute_text(end)}"
        )
    elif start is not None:
        text = f"từ phút {minute_text(start)}" if vi else f"since minute {minute_text(start)}"
    elif end is not None:
        text = f"trước phút {minute_text(end)}" if vi else f"before minute {minute_text(end)}"
    else:
        text = ""
    if window.period is not None:
        half = (
            f"hiệp {window.period}"
            if vi
            else ("the first half" if window.period == 1 else "the second half")
        )
        return f"{half}, {text}" if text else half
    return text


def window_intro(window: Window, lang: Lang, ctx: MatchContext) -> str:
    """Sentence opening for a window: "Trong hiệp 2", "Up to 63'"."""
    vi = lang == "vi"
    if window == FULL:
        if ctx.finished:
            return "Tính cả trận" if vi else "Over the match"
        return f"Tính tới phút {as_of_minute(ctx)}" if vi else f"Up to {ctx.clock_display}"
    if isinstance(window, RollingWindow) or window in (H1, H2):
        label = window_label(window, lang)
        return f"Trong {label}" if vi else f"In {label}"
    label = window_label(window, lang)
    return label[0].upper() + label[1:]


def window_numbers(window: Window) -> frozenset[float]:
    """Numbers a sentence about this window may contain (bounds, rolling length)."""
    if isinstance(window, RollingWindow):
        return frozenset({float(rolling_minutes(window))})
    values = {float(v) for v in (window.start_min, window.end_min) if v is not None}
    if window.period is not None:
        values.add(float(window.period))
    return frozenset(values)


def team_name(ctx: MatchContext, team_id: str) -> str:
    return ctx.team(team_id).name


def subject_name(ctx: MatchContext, subject: Subject, lang: Lang) -> str:
    if subject.kind == "both_teams":
        return "hai đội" if lang == "vi" else "both teams"
    if subject.player is not None:
        return ctx.player(subject.player).name
    assert subject.team is not None
    return team_name(ctx, subject.team)


def metric_label(metric: str, lang: Lang) -> str:
    spec = REGISTRY[metric]
    return spec.description_vi if lang == "vi" else spec.description_en


def fact_labels(metric: str, who: str, window: Window) -> tuple[str, str]:
    """(label_vi, label_en) of a Fact: "Số cú sút · HAR · hiệp 2"."""
    return (
        f"{metric_label(metric, 'vi')} · {who} · {window_label(window, 'vi')}",
        f"{metric_label(metric, 'en')} · {who} · {window_label(window, 'en')}",
    )


def measure(metric: str, value: float, unit: Unit, lang: Lang) -> str:
    """Verb phrase stating a value: "có 15 cú sút" / "had 15 shots"."""
    v = number_text(value, unit)
    vi = lang == "vi"
    if metric == "score":
        return f"ghi {v} bàn" if vi else f"scored {v} goal" + ("" if v == "1" else "s")
    if metric in COUNT_NOUNS:
        noun_vi, noun_en = COUNT_NOUNS[metric]
        if v == "1":
            noun_en = SINGULAR_EN.get(noun_en, noun_en)
        return f"có {v} {noun_vi}" if vi else f"had {v} {noun_en}"
    phrases: dict[str, tuple[str, str]] = {
        "possession": (f"kiểm soát bóng {v}%", f"had {v}% possession"),
        "pass_share": (f"có tỷ lệ đường chuyền {v}%", f"had a {v}% share of passes"),
        "possession_time": (f"giữ bóng {v} phút", f"had {v} minutes on the ball"),
        "xg": (f"có xG {v}", f"had an xG of {v}"),
        "pass_accuracy": (f"chuyền chính xác {v}%", f"completed {v}% of passes"),
        "ppda": (f"có PPDA {v}", f"had a PPDA of {v}"),
        "momentum": (f"có momentum {v}", f"had a momentum of {v}"),
        "tempo": (
            f"có nhịp độ {v} đường chuyền mỗi phút giữ bóng",
            f"played {v} passes per minute on the ball",
        ),
        "shot_speed_max": (f"có cú sút nhanh nhất {v} km/h", f"had a fastest shot of {v} km/h"),
        "player_distance": (f"di chuyển {v} m", f"covered {v} m"),
        "player_top_speed": (f"đạt tốc độ tối đa {v} km/h", f"reached a top speed of {v} km/h"),
    }
    if metric in phrases:
        text_vi, text_en = phrases[metric]
        return text_vi if vi else text_en
    label = metric_label(metric, lang)
    shown = format_value(value, unit)
    return f"có {label.lower()} {shown}" if vi else f"had a {label.lower()} of {shown}"
