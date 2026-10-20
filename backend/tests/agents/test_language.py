"""Normalisation, term matching and language detection (design sections 4.2.1-4.2.2)."""

import unicodedata

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pundit.agents.language import Term, Text, detect_language, find_terms, fold, lower, nfc


@given(st.text(max_size=80))
def test_views_keep_the_length_of_the_text(raw: str) -> None:
    text = nfc(raw)
    assert len(fold(text)) == len(text) == len(lower(text))


@pytest.mark.parametrize(
    ("raw", "folded"),
    [
        ("Phạm Gia Bảo", "pham gia bao"),
        ("Đội nhà ÁP ĐẢO hiệp 2", "doi nha ap dao hiep 2"),
        ("Trịnh Minh Khôi", "trinh minh khoi"),
        ("Andrés Villalba", "andres villalba"),
        (unicodedata.normalize("NFD", "Gia Bảo"), "gia bao"),
    ],
)
def test_folding(raw: str, folded: str) -> None:
    assert Text.of(raw).folded == folded


def test_text_of_normalises_spaces_and_apostrophes() -> None:
    text = Text.of("  Harbor   didn\u2019t \n dominate ")
    assert text.raw == "Harbor didn't dominate"


def test_longest_match_wins() -> None:
    terms = [Term("cú sút", "shots"), Term("cú sút trúng đích", "on_target")]
    hits = find_terms(Text.of("KES có 1 cú sút trúng đích"), terms)
    assert [h.payload for h in hits] == ["on_target"]


def test_single_syllables_keep_their_diacritics() -> None:
    terms = [Term("thẻ", "card", "lower")]
    assert find_terms(Text.of("HAR nhận 2 thẻ"), terms)
    assert not find_terms(Text.of("thế trận cân bằng"), terms)
    assert not find_terms(Text.of("the second half"), terms)


def test_blocked_spans_are_not_matched() -> None:
    text = Text.of("hiệp 2 hai đội")
    hits = find_terms(text, [Term("hai đội", "both")], blocked=[(7, 10)])
    assert hits == []


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        ("Đội nhà áp đảo hiệp 2", "vi"),
        ("HAR ap dao hiep 2", "vi"),
        ("HAR pressing mạnh hơn KES trong hiệp 2", "vi"),
        ("Calder ghi bàn trong hiệp 2", "vi"),
        ("Harbor dominated the second half", "en"),
        ("Kestrel won despite lower xG", "en"),
        ("Harbor had 61% possession", "en"),
        ("The visitors had more shots in the first half", "en"),
    ],
)
def test_detect_language(claim: str, expected: str) -> None:
    assert detect_language(Text.of(claim)) == expected


def test_names_with_accents_do_not_make_a_claim_vietnamese() -> None:
    claim = "Andrés scored in the second half"
    text = Text.of(claim)
    assert detect_language(text, masked=[(0, len("Andrés"))]) == "en"
