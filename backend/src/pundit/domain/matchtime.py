"""Match clock, display minutes and time windows (docs/day1-design.md section 3.2).

Every window is half-open: [start, end).
"""

import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

MS_PER_MINUTE = 60_000
PERIOD_REGULATION_MS = 45 * MS_PER_MINUTE


def period_offset(period: int) -> int:
    return 0 if period == 1 else 45


def minute_fields(period: int, clock_ms: int) -> tuple[int, int]:
    """Stored `minute` (0-based, 45 offset in the 2nd half) and `second`."""
    return period_offset(period) + clock_ms // MS_PER_MINUTE, (clock_ms // 1000) % 60


def minute_exact(period: int, clock_ms: int) -> float:
    return period_offset(period) + clock_ms / MS_PER_MINUTE


def format_minute(period: int, clock_ms: int) -> str:
    """Football display: 0:00-0:59 is 1'; 45:00 of the first half is 45+1'."""
    regular_end = 45 if period == 1 else 90
    shown = period_offset(period) + clock_ms // MS_PER_MINUTE + 1
    return f"{shown}'" if shown <= regular_end else f"{regular_end}+{shown - regular_end}'"


_DISPLAY_RE = re.compile(r"^\s*(\d{1,3})\s*(?:\+\s*(\d{1,2}))?\s*'?\s*$")


def parse_display_minute(text: str) -> tuple[int, int]:
    """Inverse of `format_minute`: "27'" -> (1, 1_560_000); "90+2'" -> (2, 2_760_000).

    Returns (period, clock_ms at the start of that minute).
    """
    match = _DISPLAY_RE.match(text)
    if match is None:
        raise ValueError(f"not a display minute: {text!r}")
    base, added = int(match.group(1)), match.group(2)
    if added is not None:
        if base not in (45, 90):
            raise ValueError(f"added time only after 45' or 90': {text!r}")
        period = 1 if base == 45 else 2
        shown = base + int(added)
        if int(added) < 1:
            raise ValueError(f"added minute must be >= 1: {text!r}")
    else:
        if not 1 <= base <= 90:
            raise ValueError(f"minute must be 1..90: {text!r}")
        period = 1 if base <= 45 else 2
        shown = base
    return period, (shown - 1 - period_offset(period)) * MS_PER_MINUTE


def format_clock(ms: int) -> str:
    """Duration as mm:ss, for example ball-in-play time."""
    total_seconds = ms // 1000
    return f"{total_seconds // 60:02d}:{total_seconds % 60:02d}"


def _fmt_min(value: float) -> str:
    return f"{value:g}'"


class MinuteWindow(BaseModel):
    """Window on the football-minute axis, optionally restricted to one half."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["minute"] = "minute"
    period: Literal[1, 2] | None = None
    start_min: float | None = Field(default=None, ge=0)
    end_min: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _check_bounds(self) -> Self:
        if (
            self.start_min is not None
            and self.end_min is not None
            and self.start_min >= self.end_min
        ):
            raise ValueError("start_min must be < end_min")
        return self

    def contains(self, period: int, clock_ms: int, match_ms: int) -> bool:
        if self.period is not None and period != self.period:
            return False
        minute = minute_exact(period, clock_ms)
        if self.start_min is not None and minute < self.start_min:
            return False
        return not (self.end_min is not None and minute >= self.end_min)

    def label(self) -> str:
        prefix = f"H{self.period}" if self.period is not None else ""
        if self.start_min is None and self.end_min is None:
            return prefix or "FULL"
        start = _fmt_min(self.start_min) if self.start_min is not None else "start"
        end = _fmt_min(self.end_min) if self.end_min is not None else "end"
        return f"{prefix} {start}-{end}".strip()


class RollingWindow(BaseModel):
    """Window on the played-time axis (`match_ms`), for "the last N minutes"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["rolling"] = "rolling"
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def _check_bounds(self) -> Self:
        if self.start_ms >= self.end_ms:
            raise ValueError("start_ms must be < end_ms")
        return self

    @classmethod
    def last(cls, minutes: float, now_ms: int) -> "RollingWindow":
        """The last `minutes` of played time, including an event exactly at `now_ms`."""
        if minutes <= 0:
            raise ValueError("minutes must be > 0")
        return cls(start_ms=max(0, now_ms - round(minutes * MS_PER_MINUTE)), end_ms=now_ms + 1)

    def contains(self, period: int, clock_ms: int, match_ms: int) -> bool:
        return self.start_ms <= match_ms < self.end_ms

    def label(self) -> str:
        return f"{format_clock(self.start_ms)}-{format_clock(self.end_ms)}"


Window = Annotated[MinuteWindow | RollingWindow, Field(discriminator="kind")]

FULL = MinuteWindow()
H1 = MinuteWindow(period=1)
H2 = MinuteWindow(period=2)


def overlap_ms(
    window: MinuteWindow | RollingWindow,
    *,
    period: int,
    period_start_ms: int,
    start_ms: int,
    end_ms: int,
) -> int:
    """Milliseconds of [start_ms, end_ms) (on the match_ms axis, inside one period) in `window`."""
    if end_ms <= start_ms:
        return 0
    low, high = start_ms, end_ms
    if isinstance(window, MinuteWindow):
        if window.period is not None and window.period != period:
            return 0
        offset = period_offset(period)
        if window.start_min is not None:
            low = max(low, period_start_ms + round((window.start_min - offset) * MS_PER_MINUTE))
        if window.end_min is not None:
            high = min(high, period_start_ms + round((window.end_min - offset) * MS_PER_MINUTE))
    else:
        low = max(low, window.start_ms)
        high = min(high, window.end_ms)
    return max(0, high - low)


_WINDOW_RANGE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)?\s*-\s*(\d+(?:\.\d+)?)?\s*$")


def parse_window(text: str) -> MinuteWindow:
    """CLI helper: FULL, H1, H2, "60-75", "75-", "-30"."""
    upper = text.strip().upper()
    if upper == "FULL":
        return FULL
    if upper == "H1":
        return H1
    if upper == "H2":
        return H2
    match = _WINDOW_RANGE_RE.match(text)
    if match is None or (match.group(1) is None and match.group(2) is None):
        raise ValueError(f"not a window: {text!r} (use FULL, H1, H2, 60-75, 75-, -30)")
    start = float(match.group(1)) if match.group(1) is not None else None
    end = float(match.group(2)) if match.group(2) is not None else None
    return MinuteWindow(start_min=start, end_min=end)
