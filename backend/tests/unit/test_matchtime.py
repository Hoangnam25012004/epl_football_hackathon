import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from pundit.domain.matchtime import (
    FULL,
    H1,
    H2,
    MinuteWindow,
    RollingWindow,
    format_clock,
    format_minute,
    minute_exact,
    minute_fields,
    overlap_ms,
    parse_display_minute,
    parse_window,
)


@pytest.mark.parametrize(
    ("period", "clock_ms", "expected"),
    [
        (1, 0, "1'"),
        (1, 44 * 60_000 + 59_999, "45'"),
        (1, 45 * 60_000, "45+1'"),
        (1, 47 * 60_000 + 30_000, "45+3'"),
        (2, 0, "46'"),
        (2, 44 * 60_000 + 59_999, "90'"),
        (2, 45 * 60_000, "90+1'"),
    ],
)
def test_format_minute(period: int, clock_ms: int, expected: str) -> None:
    assert format_minute(period, clock_ms) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1'", (1, 0)),
        ("27'", (1, 26 * 60_000)),
        ("45", (1, 44 * 60_000)),
        ("45+2'", (1, 46 * 60_000)),
        ("46'", (2, 0)),
        ("90+2'", (2, 46 * 60_000)),
    ],
)
def test_parse_display_minute(text: str, expected: tuple[int, int]) -> None:
    assert parse_display_minute(text) == expected


@pytest.mark.parametrize("bad", ["0", "91'", "45+0", "60+2", "abc", "", "90+"])
def test_parse_display_minute_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_display_minute(bad)


@given(st.sampled_from([1, 2]), st.integers(min_value=0, max_value=55))
def test_parse_inverts_format(period: int, minute_index: int) -> None:
    clock_ms = minute_index * 60_000
    assert parse_display_minute(format_minute(period, clock_ms)) == (period, clock_ms)


def test_minute_fields_and_exact() -> None:
    assert minute_fields(1, 30_000) == (0, 30)
    assert minute_fields(2, 61_000) == (46, 1)
    assert minute_exact(2, 15 * 60_000) == 60.0
    assert format_clock(38 * 60_000 + 12_345) == "38:12"


class TestMinuteWindow:
    def test_start_is_inclusive_and_end_exclusive(self) -> None:
        window = MinuteWindow(period=2, start_min=60, end_min=75)
        assert window.contains(2, 15 * 60_000, 0)
        assert not window.contains(2, 15 * 60_000 - 1, 0)
        assert window.contains(2, 30 * 60_000 - 1, 0)
        assert not window.contains(2, 30 * 60_000, 0)
        assert not window.contains(1, 15 * 60_000, 0)

    def test_half_includes_stoppage(self) -> None:
        assert H1.contains(1, 47 * 60_000, 0)
        assert not H1.contains(2, 0, 0)
        assert H2.contains(2, 49 * 60_000, 0)

    def test_without_period_covers_first_half_stoppage_and_second_half(self) -> None:
        window = MinuteWindow(start_min=40, end_min=50)
        assert window.contains(1, 46 * 60_000, 0)  # 45+2'
        assert window.contains(2, 2 * 60_000, 0)  # 47'
        assert not window.contains(2, 6 * 60_000, 0)  # 51'

    def test_full_contains_everything(self) -> None:
        assert FULL.contains(1, 0, 0) and FULL.contains(2, 50 * 60_000, 0)

    @pytest.mark.parametrize(("start", "end"), [(75, 60), (60, 60), (-1, 10)])
    def test_rejects_bad_bounds(self, start: float, end: float) -> None:
        with pytest.raises(ValidationError):
            MinuteWindow(start_min=start, end_min=end)

    def test_labels(self) -> None:
        assert FULL.label() == "FULL"
        assert H2.label() == "H2"
        assert MinuteWindow(period=2, start_min=75).label() == "H2 75'-end"


class TestRollingWindow:
    def test_last_includes_now_and_exact_start(self) -> None:
        window = RollingWindow.last(5, 600_000)
        assert window.contains(1, 0, 600_000)
        assert window.contains(1, 0, 300_000)
        assert not window.contains(1, 0, 299_999)

    def test_rejects_bad_bounds(self) -> None:
        with pytest.raises(ValidationError):
            RollingWindow(start_ms=10, end_ms=10)
        with pytest.raises(ValueError):
            RollingWindow.last(0, 1000)


class TestOverlap:
    def test_partial_overlap_with_minute_window(self) -> None:
        window = MinuteWindow(period=2, start_min=60, end_min=75)
        p2_start = 47 * 60_000
        start = p2_start + 14 * 60_000  # 59'
        end = p2_start + 16 * 60_000  # 61'
        assert (
            overlap_ms(window, period=2, period_start_ms=p2_start, start_ms=start, end_ms=end)
            == 60_000
        )

    def test_other_period_and_empty(self) -> None:
        assert overlap_ms(H1, period=2, period_start_ms=0, start_ms=0, end_ms=10) == 0
        assert overlap_ms(FULL, period=1, period_start_ms=0, start_ms=10, end_ms=10) == 0

    def test_rolling(self) -> None:
        window = RollingWindow(start_ms=1_000, end_ms=2_000)
        assert overlap_ms(window, period=1, period_start_ms=0, start_ms=500, end_ms=1_500) == 500

    @given(
        st.integers(min_value=0, max_value=50 * 60_000),
        st.integers(min_value=1, max_value=120_000),
        st.integers(min_value=1, max_value=89),
    )
    def test_overlap_is_additive_over_adjacent_windows(
        self, start: int, length: int, cut: int
    ) -> None:
        kwargs = {"period": 2, "period_start_ms": 46 * 60_000, "start_ms": 46 * 60_000 + start}
        kwargs["end_ms"] = kwargs["start_ms"] + length
        whole = overlap_ms(FULL, **kwargs)  # type: ignore[arg-type]
        left = overlap_ms(MinuteWindow(end_min=cut), **kwargs)  # type: ignore[arg-type]
        right = overlap_ms(MinuteWindow(start_min=cut), **kwargs)  # type: ignore[arg-type]
        assert left + right == whole == length


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("FULL", FULL),
        ("h2", H2),
        ("60-75", MinuteWindow(start_min=60, end_min=75)),
        ("75-", MinuteWindow(start_min=75)),
        ("-30", MinuteWindow(end_min=30)),
    ],
)
def test_parse_window(text: str, expected: MinuteWindow) -> None:
    assert parse_window(text) == expected


@pytest.mark.parametrize("bad", ["", "-", "abc", "75-60"])
def test_parse_window_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_window(bad)
