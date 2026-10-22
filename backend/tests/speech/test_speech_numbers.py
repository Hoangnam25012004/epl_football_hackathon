"""Numbers read aloud in Vietnamese (docs/day5-design.md section 5.3): a golden table, and the
independent reader as an oracle on every whole number and on decimals."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pundit.agents.spoken_numbers import (
    parse_vi_number,
    parse_vi_ordinal,
    vi_cardinal,
    vi_decimal,
    vi_ordinal,
)

GOLDEN = {
    0: "không",
    1: "một",
    4: "bốn",
    5: "năm",
    10: "mười",
    11: "mười một",
    14: "mười bốn",
    15: "mười lăm",
    20: "hai mươi",
    21: "hai mươi mốt",
    24: "hai mươi bốn",
    25: "hai mươi lăm",
    30: "ba mươi",
    41: "bốn mươi mốt",
    44: "bốn mươi bốn",
    45: "bốn mươi lăm",
    50: "năm mươi",
    55: "năm mươi lăm",
    99: "chín mươi chín",
    100: "một trăm",
    101: "một trăm linh một",
    105: "một trăm linh năm",
    110: "một trăm mười",
    115: "một trăm mười lăm",
    121: "một trăm hai mươi mốt",
    1000: "một nghìn",
    1005: "một nghìn không trăm linh năm",
    1010: "một nghìn không trăm mười",
    1100: "một nghìn một trăm",
    10_400: "mười nghìn bốn trăm",
    100_000: "một trăm nghìn",
    999_999: "chín trăm chín mươi chín nghìn chín trăm chín mươi chín",
    1_000_005: "một triệu không trăm linh năm",
}
DECIMALS = {
    "0.35": "không phẩy ba mươi lăm",
    "0.05": "không phẩy không năm",
    "0.00": "không phẩy không không",
    "2.08": "hai phẩy không tám",
    "2.10": "hai phẩy mười",
    "2.73": "hai phẩy bảy mươi ba",
    "10.4": "mười phẩy bốn",
    "2,24": "hai phẩy hai mươi bốn",  # the Vietnamese decimal comma
}


@pytest.mark.parametrize(("number", "words"), sorted(GOLDEN.items()))
def test_cardinals(number: int, words: str) -> None:
    assert vi_cardinal(number) == words
    assert parse_vi_number(words) == number


@pytest.mark.parametrize(("raw", "words"), sorted(DECIMALS.items()))
def test_decimals(raw: str, words: str) -> None:
    assert vi_decimal(raw) == words
    assert parse_vi_number(words) == Decimal(raw.replace(",", "."))


def test_ordinals() -> None:
    assert [vi_ordinal(n) for n in (1, 2, 3, 4, 5)] == ["nhất", "hai", "ba", "tư", "năm"]
    assert [parse_vi_ordinal(w) for w in ("nhất", "tư", "hai mươi")] == [1, 4, 20]


def test_negative_and_bad_input() -> None:
    assert parse_vi_number("âm không phẩy bốn mươi hai") == Decimal("-0.42")
    for bad in ("", "phẩy", "mười mốt", "mốt", "hai mươi mươi", "một trăm hai", "không ba"):
        with pytest.raises(ValueError):
            parse_vi_number(bad)
    for bad in (-1, 10**12):
        with pytest.raises(ValueError):
            vi_cardinal(bad)
    with pytest.raises(ValueError):
        vi_decimal("12")


@given(st.integers(min_value=0, max_value=999_999_999))
def test_every_whole_number_reads_back(number: int) -> None:
    words = vi_cardinal(number)
    assert parse_vi_number(words) == number
    assert words == " ".join(words.split())  # no double space, no edge space


@given(st.integers(min_value=0, max_value=200), st.integers(min_value=0, max_value=99))
def test_decimals_read_back(whole: int, hundredths: int) -> None:
    raw = f"{whole}.{hundredths:02d}"
    assert parse_vi_number(vi_decimal(raw)) == Decimal(raw)
    one_digit = f"{whole}.{hundredths % 10}"
    assert parse_vi_number(vi_decimal(one_digit)) == Decimal(one_digit)
