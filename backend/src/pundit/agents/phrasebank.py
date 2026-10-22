"""Deterministic choice among phrasings (docs/day3-design.md principle S7).

Templates have several phrasings so the commentary does not repeat itself. The choice depends
only on a stable key (the trigger id): `zlib.crc32`, never `random` or `hash()` (salted per
process), so the same match always reads the same.
"""

import string
import zlib
from collections.abc import Mapping, Sequence
from functools import cache


def stable_index(key: str, size: int) -> int:
    if size <= 0:
        raise ValueError("no phrasing to choose from")
    return zlib.crc32(key.encode("utf-8")) % size


@cache
def fields_of(template: str) -> frozenset[str]:
    """Names of the `{placeholders}` of a template."""
    return frozenset(name for _, name, _, _ in string.Formatter().parse(template) if name)


def usable(variants: Sequence[str], values: Mapping[str, str]) -> list[str]:
    """Variants whose placeholders all have a value."""
    return [v for v in variants if fields_of(v) <= values.keys()]


def capitalize_sentences(text: str) -> str:
    """Every sentence starts with a capital ("phút 63: ..." -> "Phút 63: ..."), except the
    metric "xG", which keeps its spelling."""
    chars = list(text)
    start = True
    for index, char in enumerate(chars):
        if start and not char.isspace():
            if char.islower() and not text.startswith("xG", index):
                chars[index] = char.upper()
            start = False
        elif char in ".!?" and index + 1 < len(chars) and chars[index + 1].isspace():
            start = True
    return "".join(chars)


def fill(template: str, values: Mapping[str, str]) -> str:
    return capitalize_sentences(template.format(**values))


def pick(variants: Sequence[str], key: str, values: Mapping[str, str]) -> str | None:
    """The variant chosen for `key` among the usable ones, filled in; None if none is usable."""
    candidates = usable(variants, values)
    if not candidates:
        return None
    return fill(candidates[stable_index(key, len(candidates))], values)
