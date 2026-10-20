"""Number extraction shared by the parser and the guardrail (design section 7.2)."""

import pytest

from pundit.agents.language import Text
from pundit.agents.numbers import Num, extract_numbers


def nums(text: str) -> list[Num]:
    return extract_numbers(Text.of(text))


def values(text: str) -> list[float]:
    return [n.signed for n in nums(text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("kiểm soát bóng 61%", [61.0]),
        ("xG 2.24 so với 0.24", [2.24, 0.24]),
        ("xG 2,24", [2.24]),
        ("1,000 passes", [1000.0]),
        ("15 shots to 1", [15.0, 1.0]),
        ("momentum -0.91", [-0.91]),
        ("hai cú sút", [2.0]),
        ("three corners", [3.0]),
        ("two yellow cards", [2.0]),
        ("hai đội", []),
        ("không có cú sút nào", []),
        ("một phần ba cuối sân", []),
        ("", []),
    ],
)
def test_values(text: str, expected: list[float]) -> None:
    assert values(text) == pytest.approx(expected)


def test_decimals_and_percent() -> None:
    (xg,) = nums("xG 2.24")
    assert (xg.decimals, xg.percent) == (2, False)
    (pct,) = nums("61 phần trăm")
    assert pct.percent and pct.decimals == 0


def test_score_pair() -> None:
    left, right = nums("Harbor Lions FC 1-2 Kestrel Athletic")
    assert left.pair == 1 and right.pair == 0
    assert not left.negative and not right.negative
    spaced = nums("1 - 2")
    assert [n.signed for n in spaced] == [1.0, 2.0] and spaced[0].pair == 1


@pytest.mark.parametrize(
    "text", ["phút 63", "63'", "15 phút gần nhất", "the 60th minute", "minute 60"]
)
def test_minutes(text: str) -> None:
    (number,) = nums(text)
    assert number.minute and not number.period


def test_added_time_and_minute_ranges() -> None:
    first, second = nums("45+2'")
    assert first.minute and second.minute
    low, high = nums("phút 60\u201375")
    assert low.minute and high.minute and low.pair == 1


@pytest.mark.parametrize("text", ["hiệp 2", "hiệp thứ 2", "the 2nd half"])
def test_halves(text: str) -> None:
    (number,) = nums(text)
    assert number.period and number.value == 2


def test_blocked_spans() -> None:
    text = Text.of("gấp 3 lần, 15 cú sút")
    assert [n.value for n in extract_numbers(text, blocked=[(0, 9)])] == [15.0]
