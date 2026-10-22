"""How a verified text is read aloud (docs/day5-design.md section 5).

`normalize(text, language, lexicon)` turns the words of a commentary line, a recap paragraph or a
fixed phrase into the words a voice reads: every number written out (found by the Guardrail's own
`extract_numbers`, so exactly the numbers it verified are converted), the score read after each
team's name, team codes and "J. Ellery" replaced by names a voice can say, abbreviations spelled,
"VÀOOO" and parentheses tidied. Each change is an edit of the original string; edits never
overlap, so the place of every number in the spoken text is known and SpeechGuard can read it back.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pundit.agents.contracts import Lang, MatchContext
from pundit.agents.language import Text
from pundit.agents.numbers import Num, extract_numbers
from pundit.agents.spoken_numbers import NEGATIVE_WORD, vi_cardinal, vi_decimal, vi_ordinal

NumberKind = Literal["cardinal", "decimal", "ordinal", "word"]
ABBREVIATIONS_VI: dict[str, str] = {"xG": "ích giê", "PPDA": "pê pê đê a", "VAR": "vê a rờ"}
UNITS_VI: tuple[tuple[str, str], ...] = (
    ("km/h", "ki lô mét trên giờ"),
    ("km", "ki lô mét"),
    ("m", "mét"),
)
# Spoken forms of names that a listening review found hard to hear (section 5.4, phase V10)
PRONUNCIATION_VI: dict[str, str] = {}
TEAM_SUFFIXES = frozenset({"FC", "AFC", "CF"})
ALLOWED_PUNCTUATION = frozenset(".,;:!?'\"-")
_INITIAL = re.compile(r"^[A-Z]\.\s+(.+)$")
_REPEATED = re.compile(r"([^\W\d_])\1{2,}", re.IGNORECASE)
_WORD = re.compile(r"[^\W\d_]+")
_OPEN = re.compile(r"\s*\(\s*")
_CLOSE = re.compile(r"\s*\)")
EN_DASH = "\N{EN DASH}"
EM_DASH = "\N{EM DASH}"
MINUS = "\N{MINUS SIGN}"
CURLY_APOSTROPHE = "\N{RIGHT SINGLE QUOTATION MARK}"
_DASH = re.compile(rf"\s+[{EN_DASH}{EM_DASH}]\s+")


@dataclass(frozen=True)
class SpokenNumber:
    written: str  # "0.35"
    spoken: str  # "không phẩy ba mươi lăm"
    value: Decimal  # signed
    kind: NumberKind
    start: int  # position of `spoken` in the spoken text
    end: int


@dataclass(frozen=True)
class Spoken:
    text: str
    numbers: tuple[SpokenNumber, ...]
    names: tuple[tuple[int, int], ...]  # spans of proper names (masked by the language check)


@dataclass(frozen=True)
class SpokenLexicon:
    """Names of one match as written in texts, and as a voice says them."""

    names: tuple[tuple[str, str], ...]  # (written, spoken), longest written form first
    home: tuple[str, ...]  # written forms of the home team (full name, code)
    away: tuple[str, ...]
    home_spoken: str
    away_spoken: str

    @classmethod
    def from_match(cls, match: MatchContext) -> "SpokenLexicon":
        pairs: dict[str, str] = {}
        sides: list[tuple[tuple[str, ...], str]] = []
        for team in match.teams[:2]:
            spoken = _say(" ".join(w for w in team.name.split() if w not in TEAM_SUFFIXES))
            pairs[team.name] = spoken
            pairs[team.short_name] = spoken
            sides.append(((team.name, team.short_name), spoken))
        for player in match.players:
            initial = _INITIAL.match(player.short_name)
            pairs[player.short_name] = _say(initial.group(1) if initial else player.short_name)
            pairs.setdefault(player.name, _say(player.name))
        names = tuple(sorted(pairs.items(), key=lambda p: (-len(p[0]), p[0])))
        (home, home_spoken), (away, away_spoken) = sides
        return cls(names, home, away, home_spoken, away_spoken)

    def spoken_names(self) -> frozenset[str]:
        return frozenset(spoken for _, spoken in self.names)


def _say(name: str) -> str:
    return " ".join(PRONUNCIATION_VI.get(word, word) for word in name.split())


def _alternation(forms: Iterable[str]) -> str:
    return "|".join(re.escape(f) for f in sorted(set(forms), key=lambda f: (-len(f), f)))


@dataclass(frozen=True)
class _Number:
    offset: int  # within the edit's text
    spoken: str
    written: str
    value: Decimal
    kind: NumberKind


@dataclass(frozen=True)
class _Edit:
    start: int
    end: int
    text: str
    priority: int  # lower wins when two edits overlap
    numbers: tuple[_Number, ...] = ()
    names: tuple[tuple[int, int], ...] = ()  # within the edit's text


def _decimal_of(raw: str) -> Decimal:
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", raw):
        return Decimal(raw.replace(",", ""))
    return Decimal(raw.replace(",", "."))


def _words_of(raw: str, num: Num) -> tuple[str, NumberKind]:
    if num.decimals:
        return vi_decimal(raw), "decimal"
    return vi_cardinal(int(_decimal_of(raw))), "cardinal"


class _Builder:
    """Collects the text of one edit and where its numbers and names are."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.length = 0
        self.numbers: list[_Number] = []
        self.names: list[tuple[int, int]] = []

    def add(self, text: str) -> None:
        self.parts.append(text)
        self.length += len(text)

    def number(self, spoken: str, written: str, value: Decimal, kind: NumberKind) -> None:
        self.numbers.append(_Number(self.length, spoken, written, value, kind))
        self.add(spoken)

    def name(self, spoken: str) -> None:
        self.names.append((self.length, self.length + len(spoken)))
        self.add(spoken)

    def edit(self, start: int, end: int, priority: int) -> _Edit:
        return _Edit(start, end, "".join(self.parts), priority, tuple(self.numbers),
                     tuple(self.names))  # fmt: skip


class _Normalizer:
    def __init__(self, raw: str, lexicon: SpokenLexicon) -> None:
        self.raw = raw
        self.text = Text(raw)
        self.lexicon = lexicon
        self.edits: list[_Edit] = []

    # ---------------------------------------------------------------- scores between team names

    def scores(self) -> list[tuple[int, int]]:
        lex = self.lexicon
        pattern = re.compile(
            rf"(?<!\w)(?:{_alternation(lex.home)})\s+(\d+)\s*[-{EN_DASH}]\s*(\d+)\s+"
            rf"(?:{_alternation(lex.away)})(?!\w)"
        )
        covered = []
        for match in pattern.finditer(self.raw):
            home, away = match.group(1), match.group(2)
            b = _Builder()
            b.name(lex.home_spoken)
            b.add(" ")
            b.number(vi_cardinal(int(home)), home, Decimal(home), "cardinal")
            b.add(", ")
            b.name(lex.away_spoken)
            b.add(" ")
            b.number(vi_cardinal(int(away)), away, Decimal(away), "cardinal")
            self.edits.append(b.edit(match.start(), match.end(), 0))
            covered.append((match.start(), match.end()))
        return covered

    # ---------------------------------------------------------------- numbers

    def _preceded_by_minute_word(self, start: int) -> bool:
        before = self.text.folded[:start].rstrip()
        return re.search(r"(?<!\w)phut(?: thu)?$", before) is not None

    def _apostrophe(self, end: int) -> int:
        return end + 1 if self.raw[end : end + 1] in ("'", CURLY_APOSTROPHE) else end

    def _unit(self, b: _Builder, end: int) -> int:
        for written, spoken in UNITS_VI:
            match = re.match(rf"\s?{re.escape(written)}(?![\w/])", self.raw[end:])
            if match:
                b.add(f" {spoken}")
                return end + match.end()
        return end

    def numbers(self, covered: list[tuple[int, int]]) -> None:
        nums = extract_numbers(self.text)  # `pair` indexes this list: never filter it
        index = 0
        while index < len(nums):
            num = nums[index]
            if _inside(num.start, num.end, covered):  # read with its team by scores()
                index += 1
                continue
            nxt = nums[index + 1] if index + 1 < len(nums) else None
            gap = self.raw[num.end : nxt.start] if nxt is not None else ""
            if (
                nxt is not None
                and not _inside(nxt.start, nxt.end, covered)
                and not num.word
                and not nxt.word
                and not nxt.negative
                and (num.pair == index + 1 or re.fullmatch(r"\s*\+\s*", gap) or gap == "/")
            ):
                self._two(num, nxt, gap)
                index += 2
                continue
            self._one(num)
            index += 1

    def _two(self, left: Num, right: Num, gap: str) -> None:
        """ "1-2", "60-75", "45+3'" and "1/3": two numbers read as one phrase."""
        raw_left = self.raw[left.start : left.end]
        raw_right = self.raw[right.start : right.end]
        start, end = left.start, right.end
        b = _Builder()
        minute_mark = self._apostrophe(end) > end
        if minute_mark and not self._preceded_by_minute_word(start):
            b.add("phút ")
        negative = left.negative and start > 0 and self.raw[start - 1] in ("-", MINUS)
        words, kind = _words_of(raw_left, left)
        if negative:  # the sign word belongs to the number: the guard reads it back
            start -= 1
            words = f"{NEGATIVE_WORD} {words}"
        sign = -1 if negative else 1
        b.number(words, raw_left, sign * _decimal_of(raw_left), kind)
        if "+" in gap:
            joint = " cộng "
        elif gap == "/":
            joint = " phần "
        elif left.minute or right.minute:
            joint = " đến "
        else:
            joint = ", "
        b.add(joint)
        words, kind = _words_of(raw_right, right)
        b.number(words, raw_right, _decimal_of(raw_right), kind)
        end = self._unit(b, self._apostrophe(end))
        self.edits.append(b.edit(start, end, 1))

    def _one(self, num: Num) -> None:
        raw_num = self.raw[num.start : num.end]
        start, end = num.start, num.end
        b = _Builder()
        if num.word:  # already a word ("hai thẻ vàng"): unchanged, but known to the guard
            b.number(raw_num, raw_num, Decimal(int(num.value)), "word")
            self.edits.append(b.edit(start, end, 1))
            return
        digits = re.match(r"\d+(?:[.,]\d+)*", raw_num)
        assert digits is not None
        raw_digits = digits.group(0)
        value = _decimal_of(raw_digits)
        end = start + len(raw_digits)
        negative = num.negative and start > 0 and self.raw[start - 1] in ("-", MINUS)
        if negative:
            start -= 1
        minute_mark = self._apostrophe(end) > end
        if minute_mark and not self._preceded_by_minute_word(num.start):
            b.add("phút ")
        ordinal = re.search(r"(?<!\w)thu$", self.text.folded[: num.start].rstrip()) is not None
        if ordinal and not num.decimals and not negative:
            b.number(vi_ordinal(int(value)), raw_digits, value, "ordinal")
        else:
            words, kind = _words_of(raw_digits, num)
            if negative:  # the sign word belongs to the number: the guard reads it back
                words, value = f"{NEGATIVE_WORD} {words}", -value
            b.number(words, raw_digits, value, kind)
        end = self._apostrophe(end)
        percent = re.match(r"\s?%", self.raw[end:])
        if percent:
            b.add(" phần trăm")
            end += percent.end()
        else:
            end = self._unit(b, end)
        self.edits.append(b.edit(start, end, 1))

    # ---------------------------------------------------------------- names, abbreviations, words

    def names(self) -> None:
        if not self.lexicon.names:
            return
        spoken = dict(self.lexicon.names)
        pattern = re.compile(rf"(?<!\w)(?:{_alternation(spoken)})(?!\w)")
        for match in pattern.finditer(self.raw):
            b = _Builder()
            b.name(spoken[match.group(0)])
            self.edits.append(b.edit(match.start(), match.end(), 2))

    def abbreviations(self) -> None:
        pattern = re.compile(rf"(?<!\w)(?:{_alternation(ABBREVIATIONS_VI)})(?!\w)")
        for match in pattern.finditer(self.raw):
            self.edits.append(
                _Edit(match.start(), match.end(), ABBREVIATIONS_VI[match.group(0)], 3)
            )

    def words(self) -> None:
        keep = set(ABBREVIATIONS_VI) | {w for w, _ in self.lexicon.names}
        for match in _WORD.finditer(self.raw):
            word = match.group(0)
            new = _REPEATED.sub(r"\1", word)
            if new.isupper() and (new != word or (len(new) >= 4 and word not in keep)):
                new = new.capitalize()
            if new != word:
                self.edits.append(_Edit(match.start(), match.end(), new, 4))

    def punctuation(self) -> None:
        raw = self.raw
        for match in _OPEN.finditer(raw):
            text = "" if not raw[: match.start()].strip() else ", "
            self.edits.append(_Edit(match.start(), match.end(), text, 5))
        for match in _CLOSE.finditer(raw):
            following = raw[match.end() : match.end() + 1]
            text = "" if following == "" or following in ",.;:!?" else ","
            self.edits.append(_Edit(match.start(), match.end(), text, 5))
        for match in _DASH.finditer(raw):
            self.edits.append(_Edit(match.start(), match.end(), ", ", 5))
        for index, ch in enumerate(raw):
            if not (ch.isalnum() or ch.isspace() or ch in ALLOWED_PUNCTUATION or ch in "()%+/'"):
                self.edits.append(_Edit(index, index + 1, "", 6))

    # ---------------------------------------------------------------- result

    def build(self) -> Spoken:
        chosen: list[_Edit] = []
        for edit in sorted(self.edits, key=lambda e: (e.priority, e.start)):
            if not any(edit.start < c.end and c.start < edit.end for c in chosen):
                chosen.append(edit)
        chosen.sort(key=lambda e: e.start)
        out: list[str] = []
        length = 0
        numbers: list[SpokenNumber] = []
        names: list[tuple[int, int]] = []
        position = 0
        for edit in chosen:
            gap = self.raw[position : edit.start]
            out.append(gap)
            length += len(gap)
            for n in edit.numbers:
                at = length + n.offset
                end = at + len(n.spoken)
                numbers.append(SpokenNumber(n.written, n.spoken, n.value, n.kind, at, end))
            names.extend((length + a, length + b) for a, b in edit.names)
            out.append(edit.text)
            length += len(edit.text)
            position = edit.end
        out.append(self.raw[position:])
        return _tidy("".join(out), numbers, names)


def _inside(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(a <= start and end <= b for a, b in spans)


def _tidy(text: str, numbers: list[SpokenNumber], names: list[tuple[int, int]]) -> Spoken:
    """Collapse the spaces left by removed symbols and trim, keeping every span in place."""
    keep: list[int] = []  # old index of every kept character
    for index, ch in enumerate(text):
        if ch.isspace():
            if not keep or text[keep[-1]].isspace():
                continue
            keep.append(index)
        else:
            keep.append(index)
    while keep and text[keep[-1]].isspace():
        keep.pop()
    new_of = {old: new for new, old in enumerate(keep)}
    chars = [text[i] for i in keep]
    # a sentence starts with a capital ("ích giê:" after a full stop): never changes a length
    for index, ch in enumerate(chars):
        before = "".join(chars[max(0, index - 2) : index])
        starts = index == 0 or before in (". ", "! ", "? ")
        if starts and ch.islower() and len(ch.upper()) == 1:
            chars[index] = ch.upper()
    tidy = "".join(chars)

    def moved(start: int, end: int) -> tuple[int, int]:
        inside = [new_of[i] for i in range(start, end) if i in new_of]
        if not inside:
            raise ValueError(f"span {start}-{end} vanished from {text!r}")
        return inside[0], inside[-1] + 1

    spoken_numbers = []
    for n in numbers:
        a, b = moved(n.start, n.end)
        spoken_numbers.append(SpokenNumber(n.written, tidy[a:b], n.value, n.kind, a, b))
    return Spoken(tidy, tuple(spoken_numbers), tuple(moved(a, b) for a, b in names))


def normalize(text: str, language: Lang, lexicon: SpokenLexicon) -> Spoken:
    """The words a voice reads for `text` (numbers written out, names as said)."""
    if language != "vi":
        raise ValueError(f"no spoken pack for {language!r} yet (docs/day5-design.md 5.7)")
    raw = Text.of(text).raw
    normalizer = _Normalizer(raw, lexicon)
    covered = normalizer.scores()
    normalizer.numbers(covered)
    normalizer.names()
    normalizer.abbreviations()
    normalizer.words()
    normalizer.punctuation()
    return normalizer.build()


EMPTY_LEXICON = SpokenLexicon(names=(), home=("￿",), away=("￿",), home_spoken="",
                              away_spoken="")  # fmt: skip
