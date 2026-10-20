"""Time scopes of a claim -> Day 1 windows (docs/day2-design.md section 4.2.4).

Patterns run on the folded text, so "hiệp 2", "hiep 2" and "HIỆP HAI" agree. "N phút cuối" /
"last N minutes" is a rolling window ending at `as_of`; "45 phút cuối" / "the last 45 minutes"
means the second half, as football commentary uses it.
"""

import re
from dataclasses import dataclass
from typing import Literal

from pundit.agents.contracts import MatchContext
from pundit.agents.language import MatchMode, Text, fold
from pundit.agents.numbers import EN_NUMBER_WORDS, VI_NUMBER_WORDS
from pundit.domain.matchtime import (
    FULL,
    H1,
    H2,
    MS_PER_MINUTE,
    MinuteWindow,
    RollingWindow,
    minute_exact,
)

ScopeKind = Literal["h1", "h2", "full", "last", "first", "from", "range", "before"]
MAX_MINUTE = 130.0

_WORD_VALUES: dict[str, int] = {fold(w): v for w, v in VI_NUMBER_WORDS.items()}
_WORD_VALUES.update(EN_NUMBER_WORDS)
_WORDS = "|".join(re.escape(w) for w in sorted(_WORD_VALUES, key=len, reverse=True))
N1 = rf"(?P<n1>\d+(?:\.\d+)?|{_WORDS})"
N2 = rf"(?P<n2>\d+(?:\.\d+)?|{_WORDS})"
ORD = r"(?:st|nd|rd|th)?"

_PATTERNS: tuple[tuple[ScopeKind, str], ...] = (
    # Vietnamese
    ("h1", r"hiep\s+(?:1|mot|dau\s+tien|thu\s+nhat)"),
    ("h2", r"hiep\s+(?:2|hai|sau|thu\s+hai)"),
    # "sau giờ nghỉ (giữa hiệp)", "sau khi nghỉ giữa hiệp": after the break
    ("h2", r"sau\s+(?:khi\s+)?(?:gio\s+|thoi\s+gian\s+)?nghi(?:\s+giua\s+(?:hiep|tran))?"),
    ("last", rf"{N1}\s+phut\s+(?:cuoi(?:\s+cung)?|gan\s+(?:nhat|day)|vua\s+qua|qua)"),
    ("first", rf"{N1}\s+phut\s+dau(?:\s+tien)?"),
    (
        "range",
        rf"(?:tu\s+|giua\s+)?phut\s+(?:thu\s+)?{N1}\s*(?:-|\u2013|den|toi|va)\s*(?:phut\s+)?"
        rf"(?:thu\s+)?{N2}",
    ),
    ("from", rf"(?:ke\s+)?(?:tu|sau)\s+(?:sau\s+)?phut\s+(?:thu\s+)?{N1}"),
    ("before", rf"truoc\s+phut\s+(?:thu\s+)?{N1}"),
    (
        "full",
        r"ca\s+tran(?:\s+dau)?|toan\s+tran|tu\s+dau\s+tran|tran\s+dau\s+nay"
        r"|(?:cho\s+)?den\s+(?:luc\s+nay|hien\s+tai|bay\s+gio|nay)",
    ),
    # English
    ("h1", r"(?:the\s+)?(?:first|1st|opening)\s+half"),
    (
        "h2",
        r"(?:the\s+)?(?:second|2nd)\s+half"
        r"|after\s+(?:the\s+)?(?:break|interval|half[\s-]?time|restart)",
    ),
    ("last", rf"(?:the\s+)?(?:last|past|final|closing)\s+{N1}\s+minutes?"),
    ("first", rf"(?:the\s+)?(?:first|opening)\s+{N1}\s+minutes?"),
    ("range", rf"minutes?\s+{N1}\s*(?:-|\u2013|to|and)\s*{N2}"),
    (
        "range",
        rf"(?:from|between)\s+(?:the\s+)?{N1}{ORD}\s+(?:minute\s+)?(?:to|until|till|and)\s+"
        rf"(?:the\s+)?{N2}{ORD}\s+minutes?",
    ),
    ("range", rf"(?:from|between)\s+{N1}'\s*(?:-|\u2013|to|and)\s*{N2}'"),
    ("from", rf"(?:since|from|after)\s+(?:the\s+)?{N1}{ORD}\s+minute"),
    ("from", rf"(?:since|from|after)\s+minute\s+{N1}"),
    ("from", rf"(?:since|from|after)\s+{N1}'"),
    ("before", rf"before\s+(?:the\s+)?{N1}{ORD}\s+minute"),
    ("before", rf"before\s+minute\s+{N1}"),
    ("before", rf"before\s+{N1}'"),
    (
        "full",
        r"overall|so\s+far|throughout|in\s+(?:the|this)\s+(?:match|game)"
        r"|(?:the\s+)?(?:whole|entire|full)\s+(?:match|game)",
    ),
)
# Matched with diacritics: folded, "hiệp đầu" (first half) is "hiệp đấu" (any period of play).
_LOWER_PATTERNS: tuple[tuple[ScopeKind, str], ...] = (("h1", r"hiệp\s+đầu"),)


def _compile(
    patterns: tuple[tuple[ScopeKind, str], ...], mode: MatchMode
) -> tuple[tuple[ScopeKind, re.Pattern[str], MatchMode], ...]:
    return tuple((kind, re.compile(rf"(?<!\w)(?:{p})(?!\w)"), mode) for kind, p in patterns)


_COMPILED = _compile(_PATTERNS, "folded") + _compile(_LOWER_PATTERNS, "lower")


@dataclass(frozen=True)
class TimeScope:
    start: int
    end: int
    kind: ScopeKind
    window: MinuteWindow | RollingWindow


def _number(token: str | None) -> float | None:
    if token is None:
        return None
    if token in _WORD_VALUES:
        return float(_WORD_VALUES[token])
    try:
        return float(token)
    except ValueError:
        return None


def _valid(minute: float) -> bool:
    return 0 < minute <= MAX_MINUTE


def _window(
    kind: ScopeKind, n1: float | None, n2: float | None, as_of_ms: int
) -> MinuteWindow | RollingWindow | None:
    """The window of a matched phrase; None when its numbers make no sense (minute 0, 60-30)."""
    if kind == "h1":
        return H1
    if kind == "h2":
        return H2
    if kind == "full":
        return FULL
    if n1 is None or not _valid(n1):
        return None
    if kind == "last":  # "45 phút cuối" / "the last 45 minutes" = the second half
        return H2 if n1 == 45 else RollingWindow.last(n1, as_of_ms)
    if kind == "first":
        return H1 if n1 == 45 else MinuteWindow(end_min=n1)
    if kind == "from":
        return MinuteWindow(start_min=n1)
    if kind == "before":
        return MinuteWindow(end_min=n1)
    if n2 is None or not _valid(n2) or n1 >= n2:
        return None
    return MinuteWindow(start_min=n1, end_min=n2)


def find_time_scopes(text: Text, as_of_ms: int) -> list[TimeScope]:
    """Non-overlapping scopes, longest first, sorted by position."""
    candidates: list[TimeScope] = []
    for kind, pattern, mode in _COMPILED:
        for match in pattern.finditer(text.view(mode)):
            groups = match.groupdict()
            window = _window(kind, _number(groups.get("n1")), _number(groups.get("n2")), as_of_ms)
            if window is not None:
                candidates.append(TimeScope(match.start(), match.end(), kind, window))
    candidates.sort(key=lambda s: (-(s.end - s.start), s.start))
    chosen: list[TimeScope] = []
    for scope in candidates:
        if all(scope.end <= c.start or c.end <= scope.start for c in chosen):
            chosen.append(scope)
    return sorted(chosen, key=lambda s: s.start)


def is_future(window: MinuteWindow | RollingWindow, ctx: MatchContext) -> bool:
    """True when no part of the window has been played at `as_of` (the claim cannot be judged)."""
    if isinstance(window, RollingWindow):
        return False
    if ctx.period == 0:
        return window != FULL
    if window.period is not None and window.period > ctx.period:
        return True
    if window.start_min is not None:
        return minute_exact(ctx.period, ctx.as_of_clock_ms) < window.start_min
    return False


def rolling_minutes(window: RollingWindow) -> int:
    """Length in whole minutes of a window built by `RollingWindow.last` (end is now + 1 ms)."""
    return round((window.end_ms - 1 - window.start_ms) / MS_PER_MINUTE)
