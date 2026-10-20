"""Time scopes -> windows (design section 4.2.4), at least 20 phrasings in VI and EN."""

import pytest
from helpers import match_context

from pundit.agents.contracts import MatchContext
from pundit.agents.language import Text
from pundit.agents.timescope import find_time_scopes, is_future, rolling_minutes
from pundit.domain.matchtime import FULL, H1, H2, MinuteWindow, RollingWindow

AS_OF_MS = 80 * 60_000


def scope(text: str) -> MinuteWindow | RollingWindow | None:
    found = find_time_scopes(Text.of(text), AS_OF_MS)
    return found[0].window if found else None


@pytest.mark.parametrize(
    ("text", "window"),
    [
        ("hiệp 1", H1),
        ("trong hiệp một", H1),
        ("hiệp đầu", H1),
        ("the first half", H1),
        ("1st half", H1),
        ("45 phút đầu", H1),
        ("hiệp 2", H2),
        ("HIỆP HAI", H2),
        ("hiep 2", H2),
        ("hiệp sau", H2),
        ("the second half", H2),
        ("2nd half", H2),
        ("after the break", H2),
        ("sau giờ nghỉ", H2),
        ("sau khi nghỉ giữa hiệp", H2),
        ("sau thời gian nghỉ giữa hiệp", H2),
        ("45 phút cuối", H2),
        ("the last 45 minutes", H2),
        ("từ phút 60", MinuteWindow(start_min=60)),
        ("kể từ phút thứ 60", MinuteWindow(start_min=60)),
        ("since the 60th minute", MinuteWindow(start_min=60)),
        ("after minute 75", MinuteWindow(start_min=75)),
        ("from 70'", MinuteWindow(start_min=70)),
        ("phút 60-75", MinuteWindow(start_min=60, end_min=75)),
        ("từ phút 60 đến phút 75", MinuteWindow(start_min=60, end_min=75)),
        ("between the 60th and 75th minutes", MinuteWindow(start_min=60, end_min=75)),
        ("minutes 60\u201375", MinuteWindow(start_min=60, end_min=75)),
        ("trước phút 30", MinuteWindow(end_min=30)),
        ("before the 30th minute", MinuteWindow(end_min=30)),
        ("30 phút đầu", MinuteWindow(end_min=30)),
        ("the first 30 minutes", MinuteWindow(end_min=30)),
        ("cả trận", FULL),
        ("so far", FULL),
        ("overall", FULL),
        ("in the match", FULL),
    ],
)
def test_phrases(text: str, window: MinuteWindow | RollingWindow) -> None:
    assert scope(text) == window


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("15 phút cuối", 15),
        ("10 phút gần nhất", 10),
        ("mười lăm phút vừa qua", 15),
        ("the last 10 minutes", 10),
        ("the past 20 minutes", 20),
    ],
)
def test_rolling_windows_end_at_as_of(text: str, minutes: int) -> None:
    window = scope(text)
    assert isinstance(window, RollingWindow)
    assert window.end_ms == AS_OF_MS + 1
    assert rolling_minutes(window) == minutes


@pytest.mark.parametrize("text", ["phút 75-60", "0 phút cuối", "phút 200-210", "hiệp đấu"])
def test_nonsense_is_not_a_window(text: str) -> None:
    assert scope(text) is None


def test_no_time_words() -> None:
    assert find_time_scopes(Text.of("HAR áp đảo"), AS_OF_MS) == []


def ctx_at(at: str | None) -> MatchContext:
    return match_context(at=at)


@pytest.mark.parametrize(
    ("window", "at", "future"),
    [
        (H2, "30'", True),
        (H1, "30'", False),
        (FULL, "30'", False),
        (MinuteWindow(start_min=60), "30'", True),
        (MinuteWindow(start_min=60), "61'", False),
        (MinuteWindow(start_min=60), "45+2'", True),
        (H2, "47'", False),
        (RollingWindow(start_ms=0, end_ms=60_000), "30'", False),
    ],
)
def test_future_windows(window: MinuteWindow | RollingWindow, at: str, future: bool) -> None:
    assert is_future(window, ctx_at(at)) is future
