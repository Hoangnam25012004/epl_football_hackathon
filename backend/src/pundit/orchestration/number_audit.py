"""Independent audit of the numbers in published text (docs/day6-design.md 4.5).

Invented-number rate = numbers in published text with no source / all numbers in published text.
The target is 0. The extraction here is its own (design D6): it never imports the Guardrail or
`agents.numbers`, so a blind spot there cannot hide the same blind spot here.

A number has a source when it is in the display (or the rounded value) of a fact of the claim, in
the claim itself, a bound of a window of the claim, the minute the claim is asked at, the score at
that minute, or (for a minute) the minute of an event of the evidence. Number words count only
next to a counted noun ("two goals", "hai bàn"): "năm" is also a year and "không" a "no". A ratio
word ("twice as many", "gấp đôi") needs two facts of one metric in that ratio (10 %); when it
cannot be decided it goes to a list for a person to read, never silently through.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal

from pundit.agents.contracts import ClaimResult, Fact, Recap, StudioOutput, Trigger
from pundit.agents.recap import RecapPack
from pundit.domain.log import LogView
from pundit.domain.matchtime import MinuteWindow, RollingWindow, format_minute
from pundit.stats import MetricParams, compute

Kind = Literal["number", "percent", "minute", "word", "ratio"]
# a minute in a sentence names a moment: a goal, a card, a VAR decision, a change, a shot, an
# offside; the passes behind possession cover every minute and are no source for one
MOMENT_EVENTS = frozenset({"goal", "card", "var_check", "substitution", "shot", "offside"})
RATIO_TOLERANCE = 0.10

WORDS_EN = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "twenty": 20,
}  # fmt: skip
WORDS_VI = {
    "một": 1, "hai": 2, "ba": 3, "bốn": 4, "tư": 4, "năm": 5, "sáu": 6, "bảy": 7, "tám": 8,
    "chín": 9, "mười": 10,
}  # fmt: skip
NOUNS_EN = (
    r"goals?|shots?|corners?|(?:yellow |red )?cards?|fouls?|passes|pass|saves?|chances?|"
    r"times|offsides?|tackles?|substitutions?|minutes?|points?"
)
NOUNS_VI = r"bàn|cú sút|quả phạt góc|phạt góc|thẻ|lần|đường chuyền|pha|cơ hội|phút|điểm"
RATIOS = {
    "twice as many": 2.0, "twice as much": 2.0, "double": 2.0, "half as many": 0.5,
    "gấp đôi": 2.0, "gấp ba": 3.0, "gấp bốn": 4.0, "một nửa": 0.5,
}  # fmt: skip
_TIMES = re.compile(r"\b(\d+(?:[.,]\d+)?)\s+times\s+(?:as many|as much|more)\b", re.IGNORECASE)
_GAP = re.compile(r"\bgấp\s+(\d+(?:[.,]\d+)?)\s+lần\b", re.IGNORECASE)
_TIMES_WORD = re.compile(
    rf"\b({'|'.join(WORDS_EN)})\s+times\s+(?:as many|as much|more)\b", re.IGNORECASE
)
_GAP_WORD = re.compile(rf"\bgấp\s+({'|'.join(WORDS_VI)})\s+lần(?!\w)", re.IGNORECASE)
_ADDED = re.compile(r"(\d{1,3})\s*\+\s*(\d{1,2})\s*['\u2019]")  # ' or a typographic quote
_MINUTE = re.compile(r"(?<![\w.,])(\d{1,3})\s*['\u2019]")
# "phút 45+2" / "minute 90+3" (Day 6: the added time was read as minute 45 alone)
_MINUTE_WORD = re.compile(r"\b(?:phút|minute)\s+(\d{1,3})(?:\s*\+\s*(\d{1,2}))?\b", re.IGNORECASE)
_PERCENT = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s?%")
_NUMBER = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)(?![\w]|[.,]\d)")
_WORD_EN = re.compile(
    rf"\b({'|'.join(WORDS_EN)})\s+(?:\w+\s+)?(?:{NOUNS_EN})\b|\b(twice|thrice)\b", re.IGNORECASE
)
_WORD_VI = re.compile(rf"(?<!\w)({'|'.join(WORDS_VI)})\s+(?:{NOUNS_VI})(?!\w)", re.IGNORECASE)


@dataclass(frozen=True)
class Found:
    text: str  # as written: "61%", "2,24", "two goals", "45+2'", "twice as many"
    kind: Kind
    value: float | None  # a minute's text is compared, not its value


@dataclass(frozen=True)
class Sources:
    values: frozenset[float]
    minutes: frozenset[str]  # display minutes: "63'", "45+2'"
    ratios: tuple[float, ...]  # value ratios of two facts of one metric


@dataclass(frozen=True)
class Audit:
    numbers: tuple[Found, ...] = ()
    unsourced: tuple[Found, ...] = ()
    manual: tuple[Found, ...] = ()  # ratio words that could not be decided
    sentences: tuple[str, ...] = field(default=(), compare=False)

    @property
    def clean(self) -> bool:
        return not self.unsourced


def _value(text: str) -> float:
    return round(float(text.replace(",", ".")), 4)


def _minute_word(match: re.Match[str]) -> Found:
    added = f"+{int(match.group(2))}" if match.group(2) else ""
    return Found(f"{int(match.group(1))}{added}'", "minute", None)


def extract(text: str) -> list[Found]:
    """Every number of `text`, Vietnamese or English, each once, in the order of the text. The
    longest forms are read first: a ratio before its number, an added-time minute before its
    minute, a percentage before its number."""
    found: list[tuple[int, Found]] = []
    taken: list[tuple[int, int]] = []

    def free(start: int, end: int) -> bool:
        return all(end <= a or start >= b for a, b in taken)

    def take(pattern: re.Pattern[str], make: Callable[[re.Match[str]], Found]) -> None:
        for match in pattern.finditer(text):
            if free(match.start(), match.end()):
                found.append((match.start(), make(match)))
                taken.append((match.start(), match.end()))

    for phrase, ratio in RATIOS.items():
        pattern = re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.IGNORECASE)
        take(pattern, _fixed_ratio(ratio))
    take(_TIMES, lambda m: Found(m.group(0), "ratio", _value(m.group(1))))
    take(_GAP, lambda m: Found(m.group(0), "ratio", _value(m.group(1))))
    take(_TIMES_WORD, lambda m: Found(m.group(0), "ratio", float(WORDS_EN[m.group(1).lower()])))
    take(_GAP_WORD, lambda m: Found(m.group(0), "ratio", float(WORDS_VI[m.group(1).lower()])))
    take(_ADDED, lambda m: Found(f"{m.group(1)}+{m.group(2)}'", "minute", None))
    take(_MINUTE_WORD, _minute_word)
    take(_MINUTE, lambda m: Found(f"{int(m.group(1))}'", "minute", None))
    take(_PERCENT, lambda m: Found(m.group(0), "percent", _value(m.group(1))))
    take(_NUMBER, lambda m: Found(m.group(0), "number", _value(m.group(1))))
    take(_WORD_EN, _english_word)
    take(_WORD_VI, lambda m: Found(m.group(0), "word", float(WORDS_VI[m.group(1).lower()])))
    return [item for _, item in sorted(found, key=lambda pair: pair[0])]


def _fixed_ratio(value: float) -> Callable[[re.Match[str]], Found]:
    def make(match: re.Match[str]) -> Found:
        return Found(match.group(0), "ratio", value)

    return make


def _english_word(match: re.Match[str]) -> Found:
    if match.group(2):
        return Found(match.group(0), "word", 2.0 if match.group(2).lower() == "twice" else 3.0)
    return Found(match.group(0), "word", float(WORDS_EN[match.group(1).lower()]))


def _numbers_of(display: str) -> set[float]:
    return {_value(m.group(1)) for m in re.finditer(r"(\d+(?:[.,]\d+)?)", display)}


def _rounded(fact: Fact) -> set[float]:
    """The raw value at the precision of its display ("2.24" -> 2 decimals)."""
    decimals = max((len(m.group(1)) for m in re.finditer(r"\.(\d+)", fact.display)), default=0)
    return {round(abs(fact.value), decimals)}


def _facts(result: ClaimResult) -> list[Fact]:
    return [*result.headline, *(f for part in result.assertions for f in part.facts)]


def _window_bounds(window: MinuteWindow | RollingWindow) -> tuple[set[float], set[str]]:
    if isinstance(window, RollingWindow):
        minutes = round((window.end_ms - window.start_ms) / 60_000)
        return {float(minutes)}, set()
    values: set[float] = set()
    if window.period is not None:
        values |= {float(window.period), 45.0 if window.period == 1 else 90.0}
    for bound in (window.start_min, window.end_min):
        if bound is not None:
            values.add(round(bound, 4))
    return values, {f"{int(v)}'" for v in values if v >= 1 and v == int(v)}


def sources(result: ClaimResult, view: LogView) -> Sources:
    """What the published text of `result` may say; `view` is the match at the claim's minute."""
    values: set[float] = set()
    minutes: set[str] = set()
    facts = _facts(result)
    for fact in facts:
        values |= _numbers_of(fact.display) | _rounded(fact)
        for event_id in fact.evidence_event_ids:
            if view.has(event_id) and view.get(event_id).type in MOMENT_EVENTS:
                event = view.get(event_id)
                minutes.add(format_minute(event.period, event.clock_ms))
    values |= {abs(item.value) for item in extract(result.text) if item.value is not None}
    minutes |= {item.text for item in extract(result.text) if item.kind == "minute"}
    for part in result.assertions:
        bounds, marks = _window_bounds(part.assertion.window)
        values |= bounds
        minutes |= marks
    values |= {1.0, 2.0}  # "hiệp 1" / "the second half": the halves are always named
    minutes.add(result.as_of_display)
    values |= _numbers_of(result.as_of_display)
    for team in view.meta.team_ids:  # the score at the claim's minute (Stats Tools as oracle)
        score = compute(view, "score", MetricParams(team=team)).value
        if score is not None:
            values.add(float(score))
    ratios = tuple(
        a.value / b.value
        for a in facts
        for b in facts
        if a is not b and a.metric is not None and a.metric == b.metric and b.value
    )
    return Sources(values=frozenset(values), minutes=frozenset(minutes), ratios=ratios)


def _sourced(item: Found, allowed: Sources) -> bool | None:
    """True / False, or None when a ratio word cannot be decided."""
    if item.kind == "minute":
        return item.text in allowed.minutes
    if item.kind == "ratio":
        assert item.value is not None
        if not allowed.ratios:
            return None
        return any(abs(r - item.value) <= RATIO_TOLERANCE * item.value for r in allowed.ratios)
    return item.value is not None and abs(item.value) in allowed.values


def audit_text(text: str, allowed: Sources) -> Audit:
    numbers = extract(text)
    unsourced: list[Found] = []
    manual: list[Found] = []
    for item in numbers:
        verdict = _sourced(item, allowed)
        if verdict is None:
            manual.append(item)
        elif not verdict:
            unsourced.append(item)
    return Audit(tuple(numbers), tuple(unsourced), tuple(manual), (text,))


def audit_claim(result: ClaimResult, view: LogView) -> Audit:
    """The published explanation of a claim, against everything it may say."""
    return audit_text(result.explanation, sources(result, view))


def total(audits: Iterable[Audit]) -> tuple[int, int]:
    """(numbers, unsourced) over several audits."""
    numbers = unsourced = 0
    for audit in audits:
        numbers += len(audit.numbers)
        unsourced += len(audit.unsourced)
    return numbers, unsourced


# ----------------------------------------------------- studio (docs/day6-design.md 4.8)


def _studio_sources(
    facts: Iterable[Fact], view: LogView, minute: str, bounds: Iterable[float]
) -> Sources:
    """What a studio text may say: the facts of its item, its minute, the windows' bounds and
    the score at that event."""
    facts = list(facts)
    marks = set(bounds)
    values: set[float] = {1.0, 2.0} | marks | _numbers_of(minute)
    # the minute and the windows' whole-minute bounds ("Phút 60:" of a stat at the 60' mark)
    minutes: set[str] = {minute} | {f"{int(b)}'" for b in marks if b >= 1 and b == int(b)}
    for fact in facts:
        values |= _numbers_of(fact.display) | _rounded(fact)
        for event_id in fact.evidence_event_ids:
            if view.has(event_id) and view.get(event_id).type in MOMENT_EVENTS:
                event = view.get(event_id)
                minutes.add(format_minute(event.period, event.clock_ms))
                # and the events it is about: the goal a VAR check ruled out (35', the check 36')
                for related in getattr(event, "related_ids", ()):
                    if view.has(related):
                        about = view.get(related)
                        minutes.add(format_minute(about.period, about.clock_ms))
    for team in view.meta.team_ids:
        score = compute(view, "score", MetricParams(team=team)).value
        if score is not None:
            values.add(float(score))
    ratios = tuple(
        a.value / b.value
        for a in facts
        for b in facts
        if a is not b and a.metric is not None and a.metric == b.metric and b.value
    )
    return Sources(values=frozenset(values), minutes=frozenset(minutes), ratios=ratios)


@dataclass(frozen=True)
class StudioAudit:
    """The model texts a studio run published, audited like the claim explanations."""

    texts: int = 0
    audits: tuple[tuple[str, Audit], ...] = ()  # (item id, audit) of every model text

    @property
    def numbers(self) -> int:
        return total(a for _, a in self.audits)[0]

    @property
    def unsourced(self) -> list[tuple[str, Found, str]]:
        return [(item, f, a.sentences[0]) for item, a in self.audits for f in a.unsourced]

    @property
    def manual(self) -> list[tuple[str, Found, str]]:
        return [(item, f, a.sentences[0]) for item, a in self.audits for f in a.manual]


def _model_texts(output: StudioOutput) -> list[tuple[str, str, list[Fact]]]:
    """(item id, text, facts) of every text a model wrote in one studio output."""
    found: list[tuple[str, str, list[Fact]]] = []
    item = output.commentary
    found += [(item.id, t.text, list(item.facts)) for t in item.texts if t.source == "llm"]
    point = output.talking_point
    if point is not None:
        for lines, source in ((point.en, point.source_en), (point.vi, point.source_vi)):
            if source == "llm":
                found += [(point.id, line.text, list(point.facts)) for line in lines]
    third = output.lower_third
    if third is not None and third.source == "llm":
        for text in (third.title_vi, third.title_en, third.subtitle_vi, third.subtitle_en):
            found.append((third.id, text, list(third.facts)))
    return found


def audit_studio(
    outputs: Iterable[tuple[Trigger, StudioOutput]],
    recaps: Iterable[tuple[Recap, RecapPack]],
    view_at: Callable[[int], LogView],
    pack_facts: Callable[[Trigger], tuple[list[Fact], list[float]]],
) -> StudioAudit:
    """Every published model text of a studio run against what its item may say: the facts of
    its FactPack (`pack_facts`), its trigger's minute and the score then; a recap against its
    RecapPack: facts, the minutes of its goals, ruled-out goals, cards and key moments."""
    audits: list[tuple[str, Audit]] = []
    for trigger, output in outputs:
        texts = _model_texts(output)
        if not texts:
            continue
        view = view_at(trigger.seq)
        facts, bounds = pack_facts(trigger)
        for item_id, text, item_facts in texts:
            allowed = _studio_sources([*facts, *item_facts], view, trigger.display_minute, bounds)
            audits.append((item_id, audit_text(text, allowed)))
    for recap, pack in recaps:
        view = view_at(recap.as_of_seq)
        minutes = {goal.minute for goal in (*pack.goals, *pack.overturned)}
        minutes |= {card.minute for card in pack.cards}
        minutes |= {moment.display_minute for moment in pack.key_moments}
        base = _studio_sources([*pack.facts, *recap.facts], view, "", pack.window_numbers)
        allowed = Sources(base.values, base.minutes | minutes, base.ratios)
        for version in recap.texts:
            if version.source == "llm":
                for part in (version.title, *version.paragraphs):
                    audits.append((recap.id, audit_text(part, allowed)))
    return StudioAudit(texts=len(audits), audits=tuple(audits))
