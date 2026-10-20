"""TemplateExplainer (docs/day2-design.md section 6.1): the mock and the last fallback.

Short, neutral sentences built only from Fact values (same rounding as `display()`), window
bounds, the clock at `as_of` and the score. No verdict words: the UI shows the verdict. A test
renders every (verdict x assertion kind x language) combination and runs the Guardrail on it,
so the fallback can never be the thing that gets blocked.
"""

from pundit.agents.contracts import (
    AssertionResult,
    ExplanationDraft,
    Fact,
    GuardrailViolation,
    JudgedClaim,
    Lang,
    MatchContext,
    ReasonCode,
)
from pundit.agents.wording import (
    as_of_minute,
    measure,
    number_text,
    window_intro,
    window_label,
)
from pundit.domain.matchtime import FULL, H1, H2, MinuteWindow, RollingWindow

MAX_TEXT_CHARS = 300

INSUFFICIENT_TEXT: dict[ReasonCode, tuple[str, str]] = {
    "unsupported_claim": (
        "Nhận định này nằm ngoài những gì có thể kiểm chứng bằng số liệu trận đấu.",
        "This statement cannot be checked against the match statistics.",
    ),
    "unknown_entity": (
        "Không tìm thấy đội hoặc cầu thủ được nhắc tới trong trận này.",
        "The team or player mentioned is not part of this match.",
    ),
    "ambiguous_entity": (
        "Tên được nhắc tới khớp với nhiều cầu thủ; hãy nêu rõ hơn.",
        "The name matches more than one player; please be more specific.",
    ),
    "no_data_in_window": (
        "Chưa có thời gian bóng lăn trong khoảng thời gian này, chưa thể đánh giá.",
        "The ball has not been in play in this period yet, so it cannot be assessed.",
    ),
    "low_sample": (
        "Khoảng thời gian này có quá ít thời gian bóng lăn để đánh giá thế trận.",
        "Too little of this period has been played to judge which side was on top.",
    ),
    "as_of_out_of_range": (
        "Thời điểm được hỏi chưa diễn ra trong trận.",
        "The requested moment of the match has not happened yet.",
    ),
    "ok": (
        "Chưa thể kiểm chứng nhận định này.",
        "This statement cannot be checked yet.",
    ),
}
PART_INSUFFICIENT = (
    "Một vế của nhận định chưa thể kiểm chứng.",
    "One part of the statement cannot be checked yet.",
)
PPDA_NOTE = (
    "PPDA thấp hơn nghĩa là pressing mạnh hơn.",
    "A lower PPDA means more intense pressing.",
)


def _pick(pair: tuple[str, str], lang: Lang) -> str:
    return pair[0] if lang == "vi" else pair[1]


def _future(window: MinuteWindow | RollingWindow, ctx: MatchContext, lang: Lang) -> str:
    label = window_label(window, lang)
    half = window in (H1, H2)
    if lang == "vi":
        now = f"(hiện tại là phút {as_of_minute(ctx)}), chưa thể đánh giá."
        if half:
            return f"{label.capitalize()} chưa bắt đầu {now}"
        return f"Khoảng thời gian {label} chưa diễn ra {now}"
    now = f"(it is {ctx.clock_display} now), so it cannot be assessed."
    if half:
        return f"{label[0].upper()}{label[1:]} has not started yet {now}"
    return f"The period {label} has not been played yet {now}"


def _insufficient(judged: JudgedClaim, lang: Lang) -> str:
    reason = judged.reason_code
    if reason == "future_window" and judged.results:
        return _future(judged.results[0].assertion.window, judged.context, lang)
    return _pick(INSUFFICIENT_TEXT.get(reason, INSUFFICIENT_TEXT["ok"]), lang)


def _name(ctx: MatchContext, fact: Fact) -> str:
    if fact.player is not None:
        return ctx.player(fact.player).name
    assert fact.team is not None
    return ctx.team(fact.team).name


def _scoreline(result: AssertionResult, ctx: MatchContext, lang: Lang) -> str:
    first, second = result.facts[0], result.facts[1]
    by_team = {first.team: first, second.team: second}
    home, away = ctx.teams[0], ctx.teams[1]
    line = (
        f"{home.name} {number_text(by_team[home.id].value, 'count')}-"
        f"{number_text(by_team[away.id].value, 'count')} {away.name}"
    )
    window = result.assertion.window
    if window == FULL:
        if ctx.finished:
            return f"Kết quả chung cuộc: {line}." if lang == "vi" else f"Final score: {line}."
        return (
            f"Tỷ số tính tới phút {as_of_minute(ctx)}: {line}."
            if lang == "vi"
            else f"Score at {ctx.clock_display}: {line}."
        )
    intro = window_intro(window, lang, ctx)
    return f"{intro}, tỷ số là {line}." if lang == "vi" else f"{intro}, the score was {line}."


def _value_of(fact: Fact) -> str:
    return number_text(fact.value, fact.unit)


def _dominance(result: AssertionResult, ctx: MatchContext, lang: Lang, compact: bool) -> str:
    facts = {(f.metric, f.team): f for f in result.facts if not f.derived}
    team = result.assertion.subject.team
    assert team is not None
    other = ctx.opponent(team)
    p, po = facts[("possession", team)], facts[("possession", other)]
    s, so = facts[("shots", team)], facts[("shots", other)]
    x, xo = facts[("xg", team)], facts[("xg", other)]
    m = facts[("momentum", team)]
    intro = window_intro(result.assertion.window, lang, ctx)
    name, other_name = ctx.team(team).name, ctx.team(other).name
    if lang == "vi":
        if compact:
            return (
                f"{intro}, {name} kiểm soát bóng {_value_of(p)}%, dứt điểm {_value_of(s)} lần "
                f"so với {_value_of(so)}."
            )
        return (
            f"{intro}, {name} kiểm soát bóng {_value_of(p)}% so với {_value_of(po)}% của "
            f"{other_name}, dứt điểm {_value_of(s)} lần so với {_value_of(so)}, xG "
            f"{_value_of(x)} so với {_value_of(xo)}, momentum {_value_of(m)}."
        )
    if compact:
        return (
            f"{intro}, {name} had {_value_of(p)}% possession and {_value_of(s)} shots to "
            f"{_value_of(so)}."
        )
    return (
        f"{intro}, {name} had {_value_of(p)}% possession to {_value_of(po)}% for {other_name}, "
        f"{_value_of(s)} shots to {_value_of(so)}, xG {_value_of(x)} to {_value_of(xo)} and "
        f"momentum {_value_of(m)}."
    )


def _compare(result: AssertionResult, ctx: MatchContext, lang: Lang, compact: bool) -> str:
    a = result.assertion
    if a.metric == "score" and all(
        f.team is not None and f.player is None for f in result.facts[:2]
    ):
        return _scoreline(result, ctx, lang)
    first, second = result.facts[0], result.facts[1]
    assert first.metric is not None and second.metric is not None
    intro = window_intro(a.window, lang, ctx)
    one = f"{_name(ctx, first)} {measure(first.metric, first.value, first.unit, lang)}"
    two = f"{_name(ctx, second)} {measure(second.metric, second.value, second.unit, lang)}"
    text = f"{intro}, {one}, còn {two}." if lang == "vi" else f"{intro}, {one} and {two}."
    if compact:
        return text
    ratio = next((f for f in result.facts if f.derived and f.unit == "ratio"), None)
    if a.ratio is not None and ratio is not None:
        text += (
            f" Tỷ lệ là {ratio.display} lần."
            if lang == "vi"
            else f" That is {ratio.display} times as many."
        )
    if a.metric == "ppda":
        text += " " + _pick(PPDA_NOTE, lang)
    return text


def _value(result: AssertionResult, ctx: MatchContext, lang: Lang) -> str:
    fact = result.facts[0]
    assert fact.metric is not None
    intro = window_intro(result.assertion.window, lang, ctx)
    return f"{intro}, {_name(ctx, fact)} {measure(fact.metric, fact.value, fact.unit, lang)}."


def _sentence(result: AssertionResult, ctx: MatchContext, lang: Lang, compact: bool) -> str:
    if result.outcome == "INSUFFICIENT":
        return _pick(PART_INSUFFICIENT, lang)
    kind = result.assertion.kind
    if kind == "dominance":
        return _dominance(result, ctx, lang, compact)
    if kind == "compare":
        return _compare(result, ctx, lang, compact)
    return _value(result, ctx, lang)


def render(judged: JudgedClaim) -> str:
    """The template explanation of a judged claim (at most MAX_TEXT_CHARS characters)."""
    lang = judged.language
    if judged.verdict == "INSUFFICIENT_DATA":
        return _insufficient(judged, lang)
    ctx = judged.context
    for compact in (False, True):
        sentences = list(dict.fromkeys(_sentence(r, ctx, lang, compact) for r in judged.results))
        text = " ".join(sentences)
        if len(text) <= MAX_TEXT_CHARS:
            return text
    kept: list[str] = []
    for sentence in sentences:
        if len(" ".join([*kept, sentence])) > MAX_TEXT_CHARS:
            break
        kept.append(sentence)
    return " ".join(kept) if kept else sentences[0][:MAX_TEXT_CHARS]


def template_draft(judged: JudgedClaim, attempt: int = 1) -> ExplanationDraft:
    return ExplanationDraft(
        text=render(judged), language=judged.language, source="template", attempt=attempt
    )


class TemplateExplainer:
    """ExplainerPort backed by the templates."""

    model = "template"

    async def explain(
        self, judged: JudgedClaim, feedback: list[GuardrailViolation] | None = None
    ) -> ExplanationDraft:
        del feedback  # templates are already guardrail-safe
        return template_draft(judged)
