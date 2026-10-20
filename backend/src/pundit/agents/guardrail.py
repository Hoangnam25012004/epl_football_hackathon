"""Guardrail (docs/day2-design.md section 7): every published sentence passes through here.

`check(text, facts, context)` is shared with Day 3 (talking points, commentary). Numbers are
extracted from the text itself, never taken from what a model says it used, and each one must
match a Fact with consistent rounding: a number written with d decimals matches value v when
|written - v| <= 0.5 * 10^-d. Window bounds, the clock at `as_of`, half numbers and the score
are allowed context. A few unit hints ("61%", "15 cú sút", "xG 2.24") keep a number from
matching a fact of another kind.

True numbers can still be put together wrongly, so two more checks read the relations:
- WRONG_COMPARISON: "7 ... less than 0", "nhiều hơn, 0 so với 7" (two numbers of the same
  metric compared in the wrong direction);
- WRONG_ATTRIBUTION: a number placed next to one team (or player) when only another team's
  fact has that value ("HAR 39%" when HAR had 61% and KES 39%).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from pundit.agents.context import NEGATIVE_POLARITY
from pundit.agents.contracts import (
    Fact,
    GuardrailReport,
    GuardrailViolation,
    JudgedClaim,
    MatchContext,
    Verdict,
)
from pundit.agents.entities import find_entities
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
    match: MatchContext | None = None  # names for the attribution check (None: skipped)


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
        match=ctx,
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


def window_of(fact: Fact) -> str:
    """The window label of a fact key "metric:owner:label" (a derived key starts with one);
    the label of "the last 15 minutes" itself contains colons ("80:12-95:12")."""
    return fact.key.split("|", 1)[0].split(":", 2)[-1]


def _pair_ok(left: Num, right: Num, facts: Sequence[Fact], context: GuardContext) -> bool:
    pair = (left.value, right.value)
    if context.score is not None and pair in (context.score, context.score[::-1]):
        return True
    groups: dict[tuple[str | None, str], list[Fact]] = {}
    for fact in facts:
        if fact.derived or (fact.team is None and fact.player is None):
            continue
        groups.setdefault((fact.metric, window_of(fact)), []).append(fact)
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


# ----------------------------------------------------------------------------- relations

MORE_WORDS = frozenset({"more", "higher", "greater", "bigger", "larger", "nhiều", "cao", "lớn"})
LESS_WORDS = frozenset({"fewer", "less", "lower", "smaller", "ít", "thấp", "nhỏ", "kém"})
# Alone these mean "many / high / big / few / low / small" ("pressing tầm cao"); only
# "nhiều ... hơn" compares, and right after "hơn" they only intensify ("ít hơn nhiều")
NEEDS_PIVOT = frozenset({"nhiều", "cao", "lớn", "ít", "thấp", "nhỏ"})
PIVOTS = frozenset({"than", "hơn"})
PIVOTS_FOLDED = frozenset({"than", "hon"})
_WORD = re.compile(r"\w+")
_SEPARATORS = re.compile(
    r",|;|:|\(|\)|(?<!\w)(?:so voi|con|trong khi|va|nhung|hon|than|while|whereas|and|but|"
    r"against|versus|vs|compared with|compared to|to)(?!\w)"
)
Span = tuple[int, int]


def _sentences(text: Text) -> list[Span]:
    """Sentence spans; "2.24" and initials ("T. Calder") do not end a sentence."""
    raw = text.raw
    spans: list[Span] = []
    start = 0
    for index, char in enumerate(raw):
        if char not in ".!?;":
            continue
        if raw[index + 1 : index + 2].isdigit():
            continue
        words = raw[start:index].split()
        if char == "." and words and len(words[-1]) == 1 and words[-1].isalpha():
            continue
        spans.append((start, index))
        start = index + 1
    spans.append((start, len(raw)))
    return [span for span in spans if raw[span[0] : span[1]].strip()]


@dataclass(frozen=True)
class _Segment:
    start: int
    end: int
    compared: bool  # right after "hơn / than": "cao hơn KES với 61%" may be the subject's 61%


def _segments(text: Text, sentence: Span) -> list[_Segment]:
    """Parts of a sentence between separators (commas, "so với", "and", "than"...)."""
    start, end = sentence
    parts: list[_Segment] = []
    cursor, compared = start, False
    for cut in _SEPARATORS.finditer(text.folded, start, end):
        if text.folded[cursor : cut.start()].strip():
            parts.append(_Segment(cursor, cut.start(), compared))
            compared = False
        if cut.group(0) in PIVOTS_FOLDED:
            compared = True
        elif cut.group(0) not in ("so voi", "compared with", "compared to"):
            compared = False  # a comma or a new clause ends the comparison
        cursor = cut.end()
    if text.folded[cursor:end].strip():
        parts.append(_Segment(cursor, end, compared))
    return parts


def _stat_numbers(numbers: Sequence[Num]) -> list[Num]:
    return [n for n in numbers if not n.minute and not n.period and n.pair is None]


def _facts_for(text: Text, number: Num, facts: Sequence[Fact]) -> list[Fact]:
    hint = _hint(text, number)
    return [
        f
        for f in facts
        if (hint.units is None or f.unit in hint.units)
        and rounding_match(number.signed, number.decimals, f.value)
    ]


def _compared_metric(first: list[Fact], second: list[Fact]) -> str | None:
    """The metric of two numbers given for two different sides (None: not comparable)."""
    return next(
        (
            a.metric
            for a in first
            for b in second
            if a.metric is not None
            and a.metric == b.metric
            and not a.derived
            and not b.derived
            and (a.team, a.player) != (b.team, b.player)
        ),
        None,
    )


def _clause(text: Text, sentence: Span, start: int, end: int) -> Span:
    """The part of the sentence around [start, end), between commas, semicolons or colons."""
    first, last = sentence
    raw = text.raw
    left = max((i + 1 for i in range(first, start) if raw[i] in ",;:"), default=first)
    right = min((i for i in range(end, last) if raw[i] in ",;:"), default=last)
    return left, right


def _sense(text: Text, sentence: Span, word: Span, metric: str | None) -> int:
    """-1 when "cao hơn / higher" is said of a lower-is-better metric without naming it before
    the word in its clause: "pressing tầm cao hơn" is a lower PPDA, "PPDA cao hơn" a higher."""
    if metric not in NEGATIVE_POLARITY:
        return 1
    clause_start, _ = _clause(text, sentence, *word)
    return 1 if metric in text.lower[clause_start : word[0]] else -1


def _comparison_violations(
    text: Text, numbers: Sequence[Num], facts: Sequence[Fact]
) -> list[GuardrailViolation]:
    violations: list[GuardrailViolation] = []
    stats = _stat_numbers(numbers)
    for start, end in _sentences(text):
        words = [(m.start(), m.end(), m.group(0)) for m in _WORD.finditer(text.lower, start, end)]
        pivots = [(a, b) for a, b, word in words if word in PIVOTS]
        checked: set[Span] = set()
        for index, (d_start, d_end, word) in enumerate(words):
            if word not in MORE_WORDS and word not in LESS_WORDS:
                continue
            if word in NEEDS_PIVOT and index > 0 and words[index - 1][2] in PIVOTS:
                continue
            direction = 1 if word in MORE_WORDS else -1
            pivot = next(((a, b) for a, b in pivots if a >= d_start), None)
            if pivot is None:
                if word in NEEDS_PIVOT:
                    continue
                pivot = (d_start, d_end)
            if pivot in checked:
                continue
            before = [n for n in stats if start <= n.start < pivot[0]]
            after = [n for n in stats if pivot[1] <= n.start < end]
            if before and after:
                first, second = before[-1], after[0]
            elif len(after) >= 2:
                first, second = after[0], after[1]
            else:
                continue
            one, two = _facts_for(text, first, facts), _facts_for(text, second, facts)
            metric = _compared_metric(one, two)
            if metric is None:
                continue
            checked.add(pivot)
            direction *= _sense(text, (start, end), (d_start, d_end), metric)
            gap = first.signed - second.signed
            if gap * direction > 0:  # "more": first > second; "less": first < second
                continue
            shown_a = text.raw[first.start : first.end]
            shown_b = text.raw[second.start : second.end]
            relation = "more than" if direction > 0 else "less than"
            violations.append(
                GuardrailViolation(
                    code="WRONG_COMPARISON",
                    detail=f"{shown_a} is not {relation} {shown_b}; compare the numbers again",
                    span=text.raw[first.start : second.end],
                )
            )
    return violations


def _side_fact(facts: Sequence[Fact], metric: str, window: str, team: str) -> Fact | None:
    return next(
        (
            f
            for f in facts
            if not f.derived
            and f.metric == metric
            and f.team == team
            and f.player is None
            and window_of(f) == window
        ),
        None,
    )


WITH_WORDS = ("with ", "voi ")  # folded "với"


def _cited(
    text: Text, clause: Span, stats: Sequence[Num], facts: Sequence[Fact]
) -> set[tuple[str, str]]:
    """(metric, window) of the facts the numbers of a clause match."""
    return {
        (f.metric, window_of(f))
        for n in stats
        if clause[0] <= n.start < clause[1]
        for f in _facts_for(text, n, facts)
        if f.metric is not None
    }


def _side_comparison_violations(
    text: Text, numbers: Sequence[Num], facts: Sequence[Fact], match: MatchContext
) -> list[GuardrailViolation]:
    """ "KES kiểm soát bóng cao hơn HAR 61%" (qwen2.5:3b): "X … more … than Y" is checked on the
    facts of the metric its clause cites, even when the clause writes a single number."""
    violations: list[GuardrailViolation] = []
    teams = [
        m
        for m in find_entities(text, match)
        if m.kind == "team" and not m.ambiguous and m.team is not None
    ]
    stats = _stat_numbers(numbers)
    for sentence in _sentences(text):
        start, end = sentence
        words = [(m.start(), m.end(), m.group(0)) for m in _WORD.finditer(text.lower, start, end)]
        for index, (d_start, d_end, word) in enumerate(words):
            if word not in MORE_WORDS and word not in LESS_WORDS:
                continue
            if word in NEEDS_PIVOT and index > 0 and words[index - 1][2] in PIVOTS:
                continue
            pivot = next((b for _, b, w in words[index:] if w in PIVOTS), None)
            if pivot is None and word in NEEDS_PIVOT:
                continue
            after = d_end if pivot is None else pivot
            subject = [m for m in teams if start <= m.start and m.end <= d_start]
            other = [m for m in teams if after <= m.start < end]
            if not subject or not other or subject[-1].team == other[0].team:
                continue
            clause = _clause(text, sentence, d_start, d_end)
            cited = _cited(text, clause, stats, facts)
            if not cited and clause[1] < end:  # "fewer fouls than KES, with 6 fouls"
                following = _clause(text, sentence, clause[1] + 1, clause[1] + 1)
                if text.folded[following[0] : following[1]].lstrip().startswith(WITH_WORDS):
                    cited = _cited(text, following, stats, facts)
            if len(cited) != 1:
                continue
            ((metric, window),) = cited
            x = _side_fact(facts, metric, window, subject[-1].team or "")
            y = _side_fact(facts, metric, window, other[0].team or "")
            if x is None or y is None:
                continue
            direction = 1 if word in MORE_WORDS else -1
            direction *= _sense(text, sentence, (d_start, d_end), metric)
            if (x.value - y.value) * direction > 0:
                continue
            violations.append(
                GuardrailViolation(
                    code="WRONG_COMPARISON",
                    detail=(
                        f"{metric}: {match.team(x.team or '').name} {x.display}, "
                        f"{match.team(y.team or '').name} {y.display}; "
                        f"'{text.raw[d_start:d_end]}' says the opposite"
                    ),
                    span=text.raw[subject[-1].start : max(other[0].end, d_end)],
                )
            )
            break  # one finding per sentence is enough feedback
    return violations


def _owner_ok(owned: list[Fact], team: str | None, players: set[str]) -> bool:
    if players:
        return any(f.player in players or (f.player is None and f.team == team) for f in owned)
    return any(f.team == team for f in owned)


def _ordered_pair_ok(
    pair: tuple[Num, Num], teams: tuple[str, str], facts: Sequence[Fact], match: MatchContext
) -> bool:
    """`first a-b second` holds for the score at as_of or for one metric of the two teams."""
    (left, right), (first, second) = pair, teams
    if match.score.get(first) == left.value and match.score.get(second) == right.value:
        return True
    for fact in facts:
        if fact.derived or fact.team != first or fact.player is not None:
            continue
        if not rounding_match(left.value, left.decimals, fact.value):
            continue
        if any(
            other.metric == fact.metric
            and other.team == second
            and other.player is None
            and window_of(other) == window_of(fact)
            and rounding_match(right.value, right.decimals, other.value)
            for other in facts
        ):
            return True
    return False


def _scoreline_violations(
    text: Text, numbers: Sequence[Num], facts: Sequence[Fact], match: MatchContext
) -> list[GuardrailViolation]:
    """ "Harbor Lions FC 1-2 Kestrel Athletic": each side of the pair belongs to its team, and so
    does "KES … cao hơn HAR 15-1" (subject first, then the side it is compared with)."""
    violations: list[GuardrailViolation] = []
    mentions = [m for m in find_entities(text, match) if m.kind == "team" and not m.ambiguous]
    for sentence_start, sentence_end in _sentences(text):
        pivots = [
            word.start()
            for word in _WORD.finditer(text.lower, sentence_start, sentence_end)
            if word.group(0) in PIVOTS
        ]
        for index, left in enumerate(numbers):
            if left.pair != index + 1 or left.minute or not sentence_start <= left.start:
                continue
            right = numbers[index + 1]
            if right.end > sentence_end:
                continue
            before = [m for m in mentions if sentence_start <= m.start and m.end <= left.start]
            after = [m for m in mentions if right.end <= m.start and m.end <= sentence_end]
            if not before:
                continue
            if after:
                one, two = before[-1], after[0]
            elif any(before[0].end <= p < before[-1].start for p in pivots):
                one, two = before[0], before[-1]
            else:
                continue
            first, second = one.team, two.team
            if first is None or second is None or first == second:
                continue
            if _ordered_pair_ok((left, right), (first, second), facts, match):
                continue
            violations.append(
                GuardrailViolation(
                    code="WRONG_ATTRIBUTION",
                    detail=(
                        f"{text.raw[left.start : right.end]} is in the wrong order for "
                        f"{match.team(first).name} vs {match.team(second).name}"
                    ),
                    span=text.raw[one.start : max(two.end, right.end)],
                )
            )
    return violations


def _attribution_violations(
    text: Text, numbers: Sequence[Num], facts: Sequence[Fact], match: MatchContext
) -> list[GuardrailViolation]:
    violations = _scoreline_violations(text, numbers, facts, match)
    mentions = find_entities(text, match)
    stats = _stat_numbers(numbers)
    cues = [hit.start for hit in find_terms(text, DERIVED_TERMS)]
    for sentence in _sentences(text):
        in_sentence = {
            m.team
            for m in mentions
            if sentence[0] <= m.start < sentence[1] and not m.ambiguous and m.team is not None
        }
        for segment in _segments(text, sentence):
            seg_start, seg_end = segment.start, segment.end
            inside = [m for m in mentions if seg_start <= m.start < seg_end and not m.ambiguous]
            if not inside or any(m.kind == "both_teams" for m in inside):
                continue
            teams = {m.team for m in inside}
            if len(teams) != 1:
                continue
            (team,) = teams
            players = {m.player for m in inside if m.player is not None}
            allowed = in_sentence if segment.compared else {team}
            for number in (n for n in stats if seg_start <= n.start < seg_end):
                matched = _facts_for(text, number, facts)
                units = _hint(text, number).units
                clause_start, clause_end = _clause(text, sentence, number.start, number.end)
                said_derived = (units is not None and "ratio" in units) or any(
                    clause_start <= cue < clause_end for cue in cues
                )
                if said_derived and any(f.derived for f in matched):
                    continue  # "gấp 15 lần Kestrel", "goal difference is 1": nobody's number
                owned = [f for f in matched if not f.derived and (f.team or f.player)]
                if not owned or any(_owner_ok(owned, side, players) for side in allowed):
                    continue
                shown = text.raw[number.start : number.end]
                name = match.team(team).name if team is not None else "this side"
                owners = sorted({match.team(f.team).name for f in owned if f.team is not None})
                violations.append(
                    GuardrailViolation(
                        code="WRONG_ATTRIBUTION",
                        detail=f"{shown} is not a number of {name}; it is {', '.join(owners)}'s",
                        span=text.raw[seg_start:seg_end].strip(),
                    )
                )
    return violations


def _terms(vi: Sequence[str], en: Sequence[str]) -> list[Term[str]]:
    """Vietnamese single syllables keep their diacritics ("chó" is not "cho")."""
    return [Term(p, p, vi_mode(p)) for p in vi] + [Term(p, p, "folded") for p in en]


VERDICT_TERMS = _terms(VERDICT_LABELS_VI, VERDICT_LABELS_EN)
UNSAFE_TERMS = _terms(UNSAFE_VI, UNSAFE_EN)
# Words that make a sentence a statement about the match even without a team name
SUBJECT_VI = ("họ", "tỷ số", "hoà", "hòa", "thắng", "thua", "dẫn trước", "bàn thắng", "ghi bàn")
SUBJECT_EN = (
    "they", "their", "them", "the hosts", "the visitors", "score", "scored", "level", "draw",
    "drew", "won", "win", "lost", "lose", "lead", "led", "goal", "goals",
)  # fmt: skip
SUBJECT_TERMS = _terms(SUBJECT_VI, SUBJECT_EN)
# Words that announce a difference or a ratio (derived facts belong to no team)
DERIVED_TERMS = _terms(
    ("chênh lệch", "chênh", "hiệu số", "cách biệt", "gấp", "tỷ lệ"),
    ("difference", "gap", "margin", "ratio", "times"),
)


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
    numbers = extract_numbers(claim)
    violations += _comparison_violations(claim, numbers, facts)
    if context.match is not None:
        violations += _side_comparison_violations(claim, numbers, facts, context.match)
        violations += _attribution_violations(claim, numbers, facts, context.match)
    if context.verdict not in (None, "INSUFFICIENT_DATA"):
        for fact in dict.fromkeys(used):
            if not fact.evidence_event_ids:
                violations.append(
                    GuardrailViolation(
                        code="MISSING_EVIDENCE", detail=f"{fact.key} has no event id", span=None
                    )
                )
    return GuardrailReport(passed=not violations, violations=tuple(violations))


CONNECTIVES = frozenset({"và", "nhưng", "còn", "and", "but"})
ELLIPSES = ("...", "…")


def fragment_violations(text: str) -> list[GuardrailViolation]:
    """The explanation under a verdict stands alone: no "..." at either end and no leading
    "và / nhưng / còn / and / but" (a 3B model wrote "... và KES chỉ có 1 cú sút trong hiệp 2").
    Not part of `check`: Day 3 commentary may use "..." on purpose."""
    body = Text.of(text).raw.strip("\"'“” ")
    if body.startswith(ELLIPSES) or body.endswith(ELLIPSES):
        return [GuardrailViolation(code="FRAGMENT", detail="write complete sentences, no '...'")]
    first = _WORD.match(body)
    if first is not None and first.group(0).lower() in CONNECTIVES:
        word = first.group(0)
        detail = f"the text starts with {word!r}; write sentences that stand alone"
        return [GuardrailViolation(code="FRAGMENT", detail=detail, span=word)]
    return []


def ungrounded_violations(
    text: Text, numbers: Sequence[Num], match: MatchContext
) -> list[GuardrailViolation]:
    """Every sentence a model writes about a team, a player or the result cites a number from
    the facts: without one it cannot be checked (qwen2.5:3b wrote "KES có chênh lệch xG lớn hơn
    HAR", which is false). Definitions ("PPDA thấp hơn nghĩa là pressing mạnh hơn") need none."""
    grounding = [n.start for n in numbers if not n.period and not n.minute]
    subjects = [m.start for m in find_entities(text, match)]
    subjects += [hit.start for hit in find_terms(text, SUBJECT_TERMS)]
    violations: list[GuardrailViolation] = []
    for start, end in _sentences(text):
        if any(start <= s < end for s in subjects) and not any(start <= g < end for g in grounding):
            sentence = text.raw[start:end].strip()
            violations.append(
                GuardrailViolation(
                    code="UNGROUNDED",
                    detail=f"{sentence[:80]!r} has no number from the facts; cite one or drop it",
                    span=sentence[:120],
                )
            )
    return violations


def check_judged(
    text: str, judged: JudgedClaim, *, content_filtered: bool = False, model_text: bool = False
) -> GuardrailReport:
    """`check` plus the rules of a verdict explanation: it stands alone (FRAGMENT) and, when a
    model wrote it (`model_text`), every sentence about the match cites a number (UNGROUNDED).
    Templates are built from the facts and may add a definition, so they skip the latter."""
    report = check(
        text, judged.all_facts(), guard_context(judged), content_filtered=content_filtered
    )
    extra = fragment_violations(text) if text.strip() else []
    if model_text and text.strip() and judged.verdict != "INSUFFICIENT_DATA":
        written = Text.of(text)
        extra += ungrounded_violations(written, extract_numbers(written), judged.context)
    if judged.verdict != "INSUFFICIENT_DATA":
        extra += [
            GuardrailViolation(code="MISSING_EVIDENCE", detail=f"{f.key} has no event id")
            for f in judged.headline
            if not f.evidence_event_ids
        ]
    if extra:
        return GuardrailReport(passed=False, violations=report.violations + tuple(extra))
    return report


def feedback_lines(violations: Sequence[GuardrailViolation]) -> list[str]:
    """Violations as short lines for the LLM retry prompt."""
    return [f"{v.code}: {v.detail}" for v in violations]
