"""Text normalisation, term matching and language detection for VI / EN claims.

Two views of a text keep the same length, so a span found in one is a span in the other:
- `lower`: NFC, lower case, diacritics kept ("thẻ" and "thế" stay different);
- `folded`: lower case without diacritics, "đ" -> "d" ("Gia Bảo", "gia bao" and NFD input match).

Rule of thumb used by every lexicon: a single Vietnamese syllable is matched on `lower` (folding
makes "thẻ", "thế" and English "the" collide); multi-word phrases and names are matched on `folded`.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import cache
from typing import Literal

from pundit.agents.contracts import Lang

MatchMode = Literal["lower", "folded"]


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


@cache
def _lower_char(ch: str) -> str:
    low = ch.lower()
    return low[0] if low else ch


@cache
def _fold_char(ch: str) -> str:
    if ch in "đĐ":
        return "d"
    low = _lower_char(ch)
    for part in unicodedata.normalize("NFD", low):
        if not unicodedata.combining(part):
            return part
    return low  # a lone combining mark: keep it (it is a word boundary anyway)


def lower(text: str) -> str:
    """Lower case, one output character per input character."""
    return "".join(_lower_char(ch) for ch in text)


def fold(text: str) -> str:
    """Lower case without diacritics, one output character per input character."""
    return "".join(_fold_char(ch) for ch in text)


@dataclass(frozen=True)
class Text:
    """A claim (or explanation) in NFC with its lower-case and folded views."""

    raw: str
    lower: str = field(init=False)
    folded: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "lower", lower(self.raw))
        object.__setattr__(self, "folded", fold(self.raw))
        assert len(self.raw) == len(self.lower) == len(self.folded)

    @classmethod
    def of(cls, text: str) -> "Text":
        """NFC, single spaces, straight apostrophes (a curly apostrophe matches "didn't")."""
        return cls(re.sub(r"\s+", " ", nfc(text)).strip().replace("\u2019", "'"))

    def view(self, mode: MatchMode) -> str:
        return self.lower if mode == "lower" else self.folded


def is_single_word(phrase: str) -> bool:
    return len(phrase.split()) == 1


def vi_mode(phrase: str) -> MatchMode:
    """Vietnamese single syllables match with diacritics; phrases match folded."""
    return "lower" if is_single_word(phrase) else "folded"


@cache
def _term_regex(phrase: str, mode: MatchMode) -> re.Pattern[str]:
    key = lower(nfc(phrase)) if mode == "lower" else fold(nfc(phrase))
    parts = [re.escape(word) for word in key.split()]
    body = r"\s+".join(parts).replace(r"\-", r"[-\s]?")
    # word boundaries only where the phrase starts / ends with a word character (", còn")
    before = r"(?<!\w)" if key[:1].isalnum() else ""
    after = r"(?!\w)" if key[-1:].isalnum() else ""
    return re.compile(f"{before}{body}{after}")


@dataclass(frozen=True)
class Term[P]:
    phrase: str
    payload: P
    mode: MatchMode = "folded"


@dataclass(frozen=True)
class Hit[P]:
    start: int
    end: int
    term: Term[P]

    @property
    def payload(self) -> P:
        return self.term.payload

    def overlaps(self, start: int, end: int) -> bool:
        return self.start < end and start < self.end


def find_terms[P](
    text: Text, terms: Iterable[Term[P]], *, blocked: Sequence[tuple[int, int]] = ()
) -> list[Hit[P]]:
    """Non-overlapping hits, longest first, then leftmost; result sorted by position.

    Spans in `blocked` (already used by another lexicon) are never matched.
    """
    candidates: list[Hit[P]] = []
    for term in terms:
        haystack = text.view(term.mode)
        for found in _term_regex(term.phrase, term.mode).finditer(haystack):
            candidates.append(Hit(found.start(), found.end(), term))
    candidates.sort(key=lambda h: (-(h.end - h.start), h.start))
    chosen: list[Hit[P]] = []
    taken = list(blocked)
    for hit in candidates:
        if any(hit.overlaps(a, b) for a, b in taken):
            continue
        chosen.append(hit)
        taken.append((hit.start, hit.end))
    return sorted(chosen, key=lambda h: h.start)


# --------------------------------------------------------------------------- language detection

VI_LETTERS = frozenset("ăâđêôơưĂÂĐÊÔƠƯ")
VI_TONES = frozenset("̣̀́̃̉")  # grave, acute, tilde, hook, dot below

_VI_WORDS_LOWER = (
    "đội",
    "hiệp",
    "có",
    "không",
    "chưa",
    "nhiều",
    "ít",
    "hơn",
    "trong",
    "của",
    "và",
    "nhưng",
    "phút",
    "cú",
    "sút",
    "bàn",
    "thắng",
    "thua",
    "hoà",
    "hòa",
    "áp",
    "đảo",
    "kiểm",
    "soát",
    "bóng",
    "cầm",
    "ghi",
    "lỗi",
    "thẻ",
    "góc",
    "chuyền",
    "vẫn",
    "cả",
    "hai",
    "trận",
    "được",
    "lần",
    "khoảng",
    "gần",
    "đã",
    "đang",
    "này",
    "nhà",
    "khách",
    "chỉ",
    "nào",
    "với",
    "so",
)
_VI_WORDS_FOLDED = (
    "doi nha",
    "doi khach",
    "hiep",
    "khong",
    "nhieu",
    "cu sut",
    "ban thang",
    "ghi ban",
    "ap dao",
    "kiem soat",
    "phat goc",
    "pham loi",
    "the vang",
    "the do",
    "duong chuyen",
    "the tran",
    "lan at",
    "lan luot",
    "chua co",
    "trong hiep",
    "ca tran",
    "phut cuoi",
)
_EN_WORDS = (
    "the",
    "had",
    "has",
    "have",
    "more",
    "fewer",
    "less",
    "than",
    "half",
    "first",
    "second",
    "shots",
    "shot",
    "goals",
    "goal",
    "won",
    "win",
    "lost",
    "drew",
    "of",
    "and",
    "but",
    "did",
    "not",
    "was",
    "were",
    "is",
    "are",
    "possession",
    "corners",
    "fouls",
    "cards",
    "minutes",
    "dominated",
    "dominate",
    "despite",
    "twice",
    "as",
    "many",
    "in",
    "on",
    "target",
    "no",
    "scored",
    "around",
    "about",
    "only",
    "both",
    "teams",
    "team",
    "side",
    "match",
    "game",
)


@cache
def _language_terms() -> tuple[list[Term[str]], list[Term[str]]]:
    vi = [Term(w, "vi", "lower") for w in _VI_WORDS_LOWER]
    vi += [Term(w, "vi", "folded") for w in _VI_WORDS_FOLDED]
    en = [Term(w, "en", "lower") for w in _EN_WORDS]
    return vi, en


def detect_language(text: Text, masked: Sequence[tuple[int, int]] = ()) -> Lang:
    """`vi` or `en` from function words outside `masked` spans (entity names).

    Ties go to `vi` when Vietnamese letters or tone marks appear outside the masked spans.
    """
    vi_terms, en_terms = _language_terms()
    vi_hits = len(find_terms(text, vi_terms, blocked=masked))
    en_hits = len(find_terms(text, en_terms, blocked=masked))
    if vi_hits != en_hits:
        return "vi" if vi_hits > en_hits else "en"
    for index, ch in enumerate(text.raw):
        if any(a <= index < b for a, b in masked):
            continue
        if ch in VI_LETTERS or any(c in VI_TONES for c in unicodedata.normalize("NFD", ch)):
            return "vi"
    return "en"
