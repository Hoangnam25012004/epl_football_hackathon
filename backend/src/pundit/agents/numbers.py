"""Numbers in a claim or an explanation (docs/day2-design.md section 7.2).

Digits, decimals (`2.24`, and `2,24` with one or two digits after the comma), percentages,
pairs (`1-2`), minutes (`63'`, "phút 63", "60th minute", "15 phút"), halves ("hiệp 2",
"2nd half") and number words 0-20 *only* right before a stat noun ("hai cú sút",
"three corners"). "không" is never a number: in Vietnamese it is mostly a negation.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from pundit.agents.language import Text

_DIGITS = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)*)(st|nd|rd|th)?(?!\w)")
_THOUSANDS = re.compile(r"^\d{1,3}(?:,\d{3})+$")
_COMMA_DECIMAL = re.compile(r"^\d+,\d{1,2}$")
_DOT_DECIMAL = re.compile(r"^\d+\.\d+$")
_PAIR_GAP = re.compile(r"^\s*[-\u2013]\s*$")
_ADDED_TIME_GAP = re.compile(r"^\s*\+\s*$")

VI_NUMBER_WORDS: dict[str, int] = {
    "một": 1, "hai": 2, "ba": 3, "bốn": 4, "năm": 5, "sáu": 6, "bảy": 7, "tám": 8,
    "chín": 9, "mười": 10, "mười một": 11, "mười hai": 12, "mười ba": 13, "mười bốn": 14,
    "mười lăm": 15, "mười sáu": 16, "mười bảy": 17, "mười tám": 18, "mười chín": 19,
    "hai mươi": 20,
}  # fmt: skip
EN_NUMBER_WORDS: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}  # fmt: skip
# Nouns that turn a preceding number word into a number (matched on the lower-case text).
VI_STAT_NOUNS = frozenset(
    {"cú", "lần", "bàn", "quả", "thẻ", "đường", "pha", "lỗi", "cơ", "tình", "phạt", "trận",
     "điểm", "phút"}
)  # fmt: skip
EN_STAT_NOUNS = frozenset(
    {"shot", "shots", "goal", "goals", "corner", "corners", "card", "cards", "foul", "fouls",
     "pass", "passes", "time", "times", "offside", "offsides", "chance", "chances", "save",
     "saves", "attempt", "attempts", "tackle", "tackles", "interception", "interceptions",
     "entry", "entries", "point", "points", "minute", "minutes", "booking", "bookings"}
)  # fmt: skip
EN_NOUN_ADJECTIVES = frozenset({"yellow", "red", "big", "clear", "more"})
_WORD_NUMBER_RE = re.compile(
    "|".join(
        rf"(?<!\w){re.escape(w)}(?!\w)"
        for w in sorted([*VI_NUMBER_WORDS, *EN_NUMBER_WORDS], key=len, reverse=True)
    )
)
_NEXT_WORD = re.compile(r"\s+([^\s.,;:!?()]+)")


@dataclass(frozen=True)
class Num:
    value: float
    start: int
    end: int
    decimals: int = 0
    percent: bool = False
    word: bool = False  # written as a word ("hai", "three")
    minute: bool = False  # a minute of the match or a number of minutes
    period: bool = False  # the number of a half
    pair: int | None = None  # index of the other number of an "a-b" pair
    negative: bool = False

    @property
    def signed(self) -> float:
        return -self.value if self.negative else self.value


def _parse_digits(raw: str) -> tuple[float, int] | None:
    if _THOUSANDS.match(raw):
        return float(raw.replace(",", "")), 0
    if _COMMA_DECIMAL.match(raw):
        whole, frac = raw.split(",")
        return float(f"{whole}.{frac}"), len(frac)
    if _DOT_DECIMAL.match(raw):
        return float(raw), len(raw.split(".")[1])
    if raw.isdigit():
        return float(raw), 0
    return None


def _followed_by(folded: str, end: int, *words: str) -> bool:
    rest = folded[end:].lstrip()
    return any(re.match(rf"{re.escape(w)}(?!\w)", rest) for w in words)


def _preceded_by(folded: str, start: int, *words: str) -> bool:
    before = folded[:start].rstrip()
    return any(re.search(rf"(?<!\w){re.escape(w)}$", before) for w in words)


def _is_blocked(start: int, end: int, blocked: Sequence[tuple[int, int]]) -> bool:
    return any(start < b and a < end for a, b in blocked)


def _word_numbers(text: Text, blocked: Sequence[tuple[int, int]]) -> list[Num]:
    found: list[Num] = []
    for match in _WORD_NUMBER_RE.finditer(text.lower):
        start, end = match.start(), match.end()
        if _is_blocked(start, end, blocked):
            continue
        word = match.group(0)
        value = VI_NUMBER_WORDS.get(word, EN_NUMBER_WORDS.get(word))
        assert value is not None
        nxt = _NEXT_WORD.match(text.lower, end)
        if nxt is None:
            continue
        noun = nxt.group(1)
        if word in EN_NUMBER_WORDS and noun in EN_NOUN_ADJECTIVES:
            after = _NEXT_WORD.match(text.lower, nxt.end())
            noun = after.group(1) if after is not None else ""
        nouns = VI_STAT_NOUNS if word in VI_NUMBER_WORDS else EN_STAT_NOUNS
        if noun not in nouns:
            continue
        minute = noun in ("phút", "minute", "minutes")
        found.append(Num(float(value), start, end, word=True, minute=minute))
    return found


def extract_numbers(text: Text, *, blocked: Sequence[tuple[int, int]] = ()) -> list[Num]:
    """Every number in `text` outside `blocked`, in order of position."""
    folded = text.folded
    numbers: list[Num] = []
    for match in _DIGITS.finditer(text.raw):
        start, end = match.start(), match.end()
        if _is_blocked(start, end, blocked):
            continue
        parsed = _parse_digits(match.group(1))
        if parsed is None:
            continue
        value, decimals = parsed
        ordinal = match.group(2) is not None
        percent = text.raw[end:].lstrip().startswith("%") or _followed_by(
            folded, end, "phan tram", "percent", "per cent"
        )
        minute = (
            text.raw[end : end + 1] in ("'", "\u2019")
            or _followed_by(folded, end, "phut", "minute", "minutes", "mins", "min")
            or _preceded_by(folded, start, "phut", "phut thu", "minute", "minutes")
        )
        period = _preceded_by(folded, start, "hiep", "hiep thu") or (
            _followed_by(folded, end, "half") and (ordinal or value in (1, 2))
        )
        negative = False
        before = text.raw[:start]
        if before.endswith(("-", "\u2212")):  # hyphen or minus sign
            prior = before[:-1].rstrip()
            negative = not prior or not prior[-1].isdigit()
        numbers.append(
            Num(
                value,
                start,
                end,
                decimals=decimals,
                percent=percent,
                minute=minute and not percent,
                period=period and not minute,
                negative=negative,
            )
        )
    numbers.extend(_word_numbers(text, blocked))
    numbers.sort(key=lambda n: n.start)
    # pairs "1-2" / "60-75" (hyphen or en dash) and added time "45+2": both sides share the context
    for index in range(len(numbers) - 1):
        left, right = numbers[index], numbers[index + 1]
        if left.word or right.word or left.negative or right.negative:
            continue
        gap = text.raw[left.end : right.start]
        if _PAIR_GAP.match(gap) and left.decimals == right.decimals == 0:
            minute = left.minute or right.minute
            numbers[index] = replace(left, pair=index + 1, minute=minute, period=False)
            numbers[index + 1] = replace(right, pair=index, minute=minute, period=False)
        elif _ADDED_TIME_GAP.match(gap):
            numbers[index] = replace(left, minute=True, period=False)
            numbers[index + 1] = replace(right, minute=True, period=False)
    return numbers
