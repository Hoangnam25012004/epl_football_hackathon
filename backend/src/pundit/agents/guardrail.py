"""Guardrail (docs/day2-design.md section 7): every published sentence passes through here.

`check(text, facts, context)` is shared with Day 3 (talking points, commentary). Numbers are
extracted from the text itself, never taken from what a model says it used, and each one must
match a Fact with consistent rounding: a number written with d decimals matches value v when
|written - v| <= 0.5 * 10^-d. Window bounds, the clock at `as_of`, half numbers and the score
are allowed context. A few unit hints ("61%", "15 cú sút", "xG 2.24") keep a number from
matching a fact of another kind.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from pundit.agents.contracts import (
    Fact,
    GuardrailReport,
    GuardrailViolation,
    JudgedClaim,
    Verdict,
)
from pundit.agents.explain_templates import MAX_TEXT_CHARS
from pundit.agents.language import Term, Text, find_terms, vi_mode
from pundit.agents.numbers import EN_STAT_NOUNS, VI_STAT_NOUNS, Num, extract_numbers
from pundit.agents.wording import window_numbers
from pundit.stats.result import Unit

VERDICT_LABELS_VI = ("đúng một phần", "không đủ dữ liệu", "đúng", "sai")
VERDICT_LABELS_EN = ("partly true", "insufficient data", "partly", "insufficient", "true", "false")
UNSAFE_VI = (
    "ngu", "ngu ngốc", "ngu dốt", "đần", "đần độn", "đồ ngốc", "vô dụng", "rác rưởi", "đồ rác",
    "đồ bỏ đi", "phế vật", "bất tài", "kém cỏi", "ăn hại", "thảm hại", "gian lận", "bán độ",
    "dàn xếp tỷ số", "mua trọng tài", "thiên vị", "khốn nạn", "mất dạy", "côn đồ", "súc vật",
    "chó", "láo",
)  # fmt: skip
UNSAFE_EN = (
    "idiot", "idiots", "stupid", "moron", "morons", "useless", "pathetic", "disgrace",
    "disgraceful", "clown", "clowns", "trash", "garbage", "cheat", "cheats", "cheated",
    "cheating", "corrupt", "rigged", "bribed", "match-fixing", "match fixing", "retard",
    "retarded", "scum", "thug", "thugs", "biased", "shameful",
)  # fmt: skip
CONTENT_FILTER = "content_filter"  # detail used when the LLM provider blocked the text
COUNT_HINTS = VI_STAT_NOUNS | EN_STAT_NOUNS
RATIO_HINTS = frozenset({"lần", "times"})
_NEXT_WORD = re.compile(r"\s*([^\s.,;:!?()]+)")


@dataclass(frozen=True)
class GuardContext:
    verdict: Verdict | None  # None outside the claim verifier (talking points, moments)
    clock_display: str  # "63'", "45+2'"
    score: tuple[int, int] | None  # (home, away) at as_of
    window_numbers: frozenset[float]  # bounds and lengths of the windows of the claim


def guard_context(judged: JudgedClaim) -> GuardContext:
    ctx = judged.context
    numbers: set[float] = set()
    for result in judged.results:
        numbers |= window_numbers(result.assertion.window)
    home, away = ctx.teams[0].id, ctx.teams[1].id
    return GuardContext(
        verdict=judged.verdict,
        clock_display=ctx.clock_display,
        score=(ctx.score.get(home, 0), ctx.score.get(away, 0)),
        window_numbers=frozenset(numbers),
    )


def rounding_match(written: float, decimals: int, value: float) -> bool:
    return abs(written - value) <= 0.5 * 10.0 ** (-decimals) + 1e-9


def _clock_numbers(clock_display: str) -> frozenset[float]:
    return frozenset(float(n) for n in re.findall(r"\d+", clock_display))


@dataclass(frozen=True)
class _Hint:
    units: frozenset[Unit] | None  # None: any unit


def _hint(text: Text, number: Num) -> _Hint:
    if number.percent:
        return _Hint(frozenset({"percent"}))
    before = text.folded[: number.start].rstrip()
    if before.endswith("xg"):
        return _Hint(frozenset({"xg"}))
    following = _NEXT_WORD.match(text.lower, number.end)
    word = following.group(1) if following else ""
    if word in ("km/h", "kmh"):
        return _Hint(frozenset({"kmh"}))
    if word in RATIO_HINTS:
        return _Hint(frozenset({"count", "ratio"}))
    if word in COUNT_HINTS:
        return _Hint(frozenset({"count"}))
    return _Hint(None)


def _matches(number: Num, facts: Sequence[Fact], hint: _Hint) -> bool:
    return any(
        (hint.units is None or fact.unit in hint.units)
        and rounding_match(number.signed, number.decimals, fact.value)
        for fact in facts
    )


def _pair_ok(left: Num, right: Num, facts: Sequence[Fact], context: GuardContext) -> bool:
    pair = (left.value, right.value)
    if context.score is not None and pair in (context.score, context.score[::-1]):
        return True
    groups: dict[tuple[str | None, str], list[Fact]] = {}
    for fact in facts:
        if fact.derived or (fact.team is None and fact.player is None):
            continue
        groups.setdefault((fact.metric, fact.key.rsplit(":", 1)[-1]), []).append(fact)
    for group in groups.values():
        for first in group:
            for second in group:
                if first is second:
                    continue
                if rounding_match(left.value, left.decimals, first.value) and rounding_match(
                    right.value, right.decimals, second.value
                ):
                    return True
    return False


def _nearest(number: Num, facts: Sequence[Fact]) -> Fact | None:
    candidates = [f for f in facts if not f.derived]
    if not candidates:
        return None
    return min(candidates, key=lambda f: abs(f.value - number.signed))


def _number_violations(
    text: Text, facts: Sequence[Fact], context: GuardContext
) -> tuple[list[GuardrailViolation], list[Fact]]:
    numbers = extract_numbers(text)
    minute_ok = context.window_numbers | _clock_numbers(context.clock_display)
    minute_facts = [f for f in facts if f.unit == "minutes"]
    violations: list[GuardrailViolation] = []
    used: list[Fact] = []

    def flag(number: Num, why: str) -> None:
        shown = text.raw[number.start : number.end]
        nearest = _nearest(number, facts)
        hint = f"; closest fact: {nearest.label_en} = {nearest.display}" if nearest else ""
        violations.append(
            GuardrailViolation(
                code="UNVERIFIED_NUMBER", detail=f"{shown!r} {why}{hint}", span=shown
            )
        )

    for index, number in enumerate(numbers):
        if number.pair is not None:
            if number.pair < index:
                continue
            right = numbers[number.pair]
            if number.minute or right.minute:
                for side in (number, right):
                    if side.value not in minute_ok and not _matches(
                        side, minute_facts, _Hint(None)
                    ):
                        flag(side, "is not a minute of this claim")
            elif not _pair_ok(number, right, facts, context):
                flag(
                    number,
                    f"and {text.raw[right.start : right.end]!r} are not a score or a stat pair",
                )
            continue
        if number.period:
            if number.value not in (1.0, 2.0):
                flag(number, "is not a half of the match")
            continue
        if number.minute:
            if number.value not in minute_ok and not _matches(number, minute_facts, _Hint(None)):
                flag(number, "is not a minute of this claim")
            continue
        hint = _hint(text, number)
        matched = [
            f
            for f in facts
            if (hint.units is None or f.unit in hint.units)
            and rounding_match(number.signed, number.decimals, f.value)
        ]
        if matched:
            used.extend(matched)
        else:
            flag(number, "does not match any verified number")
    return violations, used


def _terms(vi: Sequence[str], en: Sequence[str]) -> list[Term[str]]:
    """Vietnamese single syllables keep their diacritics ("chó" is not "cho")."""
    return [Term(p, p, vi_mode(p)) for p in vi] + [Term(p, p, "folded") for p in en]


VERDICT_TERMS = _terms(VERDICT_LABELS_VI, VERDICT_LABELS_EN)
UNSAFE_TERMS = _terms(UNSAFE_VI, UNSAFE_EN)


def _found(text: Text, terms: Sequence[Term[str]]) -> list[str]:
    return [hit.payload for hit in find_terms(text, terms)]


def check(
    text: str,
    facts: Sequence[Fact],
    context: GuardContext,
    *,
    content_filtered: bool = False,
) -> GuardrailReport:
    violations: list[GuardrailViolation] = []
    if not text.strip():
        violations.append(GuardrailViolation(code="EMPTY", detail="the text is empty"))
        return GuardrailReport(passed=False, violations=tuple(violations))
    if len(text) > MAX_TEXT_CHARS:
        violations.append(
            GuardrailViolation(
                code="TOO_LONG", detail=f"{len(text)} characters; the limit is {MAX_TEXT_CHARS}"
            )
        )
    claim = Text.of(text)
    for label in _found(claim, VERDICT_TERMS):
        violations.append(
            GuardrailViolation(
                code="VERDICT_MISMATCH",
                detail=f"the verdict word {label!r} must not appear; the UI shows the verdict",
                span=label,
            )
        )
    unsafe = _found(claim, UNSAFE_TERMS)
    for term in unsafe:
        violations.append(
            GuardrailViolation(code="UNSAFE_CONTENT", detail=f"banned term {term!r}", span=term)
        )
    if content_filtered:
        violations.append(
            GuardrailViolation(code="UNSAFE_CONTENT", detail=CONTENT_FILTER, span=None)
        )
    number_violations, used = _number_violations(claim, facts, context)
    violations += number_violations
    if context.verdict not in (None, "INSUFFICIENT_DATA"):
        for fact in dict.fromkeys(used):
            if not fact.evidence_event_ids:
                violations.append(
                    GuardrailViolation(
                        code="MISSING_EVIDENCE", detail=f"{fact.key} has no event id", span=None
                    )
                )
    return GuardrailReport(passed=not violations, violations=tuple(violations))


def check_judged(
    text: str, judged: JudgedClaim, *, content_filtered: bool = False
) -> GuardrailReport:
    report = check(
        text, judged.all_facts(), guard_context(judged), content_filtered=content_filtered
    )
    if judged.verdict != "INSUFFICIENT_DATA":
        missing = [f.key for f in judged.headline if not f.evidence_event_ids]
        if missing:
            extra = tuple(
                GuardrailViolation(code="MISSING_EVIDENCE", detail=f"{key} has no event id")
                for key in missing
            )
            return GuardrailReport(passed=False, violations=report.violations + extra)
    return report


def feedback_lines(violations: Sequence[GuardrailViolation]) -> list[str]:
    """Violations as short lines for the LLM retry prompt."""
    return [f"{v.code}: {v.detail}" for v in violations]
