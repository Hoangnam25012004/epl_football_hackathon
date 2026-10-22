"""Numbers read aloud in Vietnamese (docs/day5-design.md section 5.3).

The text-to-speech voice must never guess how to read a digit: vi-VN reads "." as a thousands
separator, so "2.24" could become "hai nghìn hai trăm bốn mươi". Every number is written out
here instead, and `parse_vi_number` reads the words back. The reader is written separately from
the writer (its own tables, its own grammar), so SpeechGuard and the tests use it as an oracle.
"""

import re
from decimal import Decimal

DIGITS_VI = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")
_SCALES = ((1_000_000_000, "tỷ"), (1_000_000, "triệu"), (1_000, "nghìn"))
MAX_NUMBER = 999_999_999_999
DECIMAL_WORD = "phẩy"
NEGATIVE_WORD = "âm"


def _below_thousand(n: int, *, inner: bool) -> list[str]:
    """0 < n < 1000; `inner`: a group after a larger one, read in full ("không trăm linh năm")."""
    hundreds, rest = divmod(n, 100)
    tens, units = divmod(rest, 10)
    words: list[str] = []
    if hundreds or inner:
        words += [DIGITS_VI[hundreds], "trăm"]
    if tens == 0:
        if units:
            if words:
                words.append("linh")
            words.append(DIGITS_VI[units])
        return words
    if tens == 1:
        words.append("mười")
    else:
        words += [DIGITS_VI[tens], "mươi"]
    if units == 1 and tens > 1:
        words.append("mốt")
    elif units == 5:
        words.append("lăm")
    elif units:
        words.append(DIGITS_VI[units])
    return words


def vi_cardinal(n: int) -> str:
    """0 -> "không", 21 -> "hai mươi mốt", 105 -> "một trăm linh năm",
    1005 -> "một nghìn không trăm linh năm"."""
    if n < 0 or n > MAX_NUMBER:
        raise ValueError(f"{n} is out of range")
    if n == 0:
        return DIGITS_VI[0]
    words: list[str] = []
    rest = n
    for size, name in _SCALES:
        group, rest = divmod(rest, size)
        if group:
            words += [*_below_thousand(group, inner=bool(words)), name]
    if rest:
        words += _below_thousand(rest, inner=bool(words))
    return " ".join(words)


def vi_ordinal(n: int) -> str:
    """The word after "thứ": 1 -> "nhất", 4 -> "tư", otherwise the cardinal."""
    if n == 1:
        return "nhất"
    if n == 4:
        return "tư"
    return vi_cardinal(n)


def vi_decimal(raw: str) -> str:
    """ "2.08" / "2,08" -> "hai phẩy không tám"; "0.35" -> "không phẩy ba mươi lăm".

    A fraction that starts with 0 is read digit by digit, any other as a whole number, which is
    how Vietnamese reads "2,08" and "0,35"."""
    match = re.fullmatch(r"(\d+)[.,](\d+)", raw)
    if match is None:
        raise ValueError(f"{raw!r} is not a decimal")
    whole, fraction = match.groups()
    if fraction.startswith("0"):
        tail = " ".join(DIGITS_VI[int(d)] for d in fraction)
    else:
        tail = vi_cardinal(int(fraction))
    return f"{vi_cardinal(int(whole))} {DECIMAL_WORD} {tail}"


# --------------------------------------------------------------------------- reading back (oracle)

_UNIT_WORDS = {
    "không": 0, "một": 1, "mốt": 1, "hai": 2, "ba": 3, "bốn": 4, "tư": 4, "năm": 5, "lăm": 5,
    "sáu": 6, "bảy": 7, "tám": 8, "chín": 9,
}  # fmt: skip
_SCALE_WORDS = {"tỷ": 1_000_000_000, "triệu": 1_000_000, "nghìn": 1_000, "ngàn": 1_000}


def _unit(word: str, *, inner: bool = False) -> int:
    """A digit word; "mốt", "lăm" and "tư" only after tens ("hai mươi mốt"), never alone."""
    if word not in _UNIT_WORDS or (not inner and word in ("mốt", "lăm")):
        raise ValueError(f"{word!r} is not a digit here")
    return _UNIT_WORDS[word]


def _group(words: list[str]) -> int:
    """A number below one thousand, in the strict grammar the writer uses."""
    if not words:
        raise ValueError("empty group")
    value = 0
    if len(words) >= 2 and words[1] == "trăm":
        value, words = 100 * _unit(words[0]), words[2:]
        if not words:
            return value
        if words[0] in ("linh", "lẻ"):
            if len(words) != 2:
                raise ValueError(f"bad group after linh: {words}")
            return value + _unit(words[1])
    if words[0] == "mười":
        if len(words) == 1:
            return value + 10
        if len(words) == 2 and words[1] not in ("mốt", "tư"):
            return value + 10 + _unit(words[1], inner=True)
        raise ValueError(f"bad group after mười: {words}")
    if len(words) >= 2 and words[1] == "mươi":
        tens = _unit(words[0])
        if tens < 2:
            raise ValueError(f"bad tens {words[0]!r}")
        if len(words) == 2:
            return value + 10 * tens
        if len(words) == 3:
            return value + 10 * tens + _unit(words[2], inner=True)
        raise ValueError(f"bad group after mươi: {words}")
    if len(words) == 1 and value == 0:
        return _unit(words[0])
    raise ValueError(f"bad group {words}")


def _integer(words: list[str]) -> int:
    total, last_scale = 0, None
    buffer: list[str] = []
    for word in words:
        if word in _SCALE_WORDS:
            scale = _SCALE_WORDS[word]
            if last_scale is not None and scale >= last_scale:
                raise ValueError(f"scale {word!r} out of order")
            total += _group(buffer) * scale
            buffer, last_scale = [], scale
        else:
            buffer.append(word)
    if buffer:
        total += _group(buffer)
    elif last_scale is None:
        raise ValueError("no number")
    return total


def parse_vi_number(text: str) -> Decimal:
    """Read Vietnamese number words back: "âm không phẩy bốn mươi hai" -> Decimal("-0.42").
    Raises ValueError on anything the writer would not produce."""
    words = text.lower().split()
    negative = bool(words) and words[0] == NEGATIVE_WORD
    if negative:
        words = words[1:]
    if not words:
        raise ValueError(f"{text!r} has no number")
    if DECIMAL_WORD in words:
        index = words.index(DECIMAL_WORD)
        whole = _integer(words[:index])
        tail = words[index + 1 :]
        if not tail:
            raise ValueError(f"{text!r} has nothing after {DECIMAL_WORD!r}")
        # a fraction that starts with "không" was read digit by digit ("2,08")
        digits = "".join(str(_unit(w)) for w in tail) if tail[0] == "không" else str(_integer(tail))
        value = Decimal(f"{whole}.{digits}")
    else:
        value = Decimal(_integer(words))
    return -value if negative else value


def parse_vi_ordinal(word: str) -> int:
    """The word after "thứ" back to a number ("nhất" -> 1, "tư" -> 4)."""
    if word == "nhất":
        return 1
    if word == "tư":
        return 4
    return int(parse_vi_number(word))
