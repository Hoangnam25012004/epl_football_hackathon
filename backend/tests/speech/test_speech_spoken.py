"""How a verified text is read aloud (docs/day5-design.md section 5): every number written out
and placed, names as a voice says them, abbreviations, symbols."""

from decimal import Decimal

import pytest
from helpers import match_context
from hypothesis import given
from hypothesis import strategies as st

from pundit.agents.spoken import EMPTY_LEXICON, SpokenLexicon, normalize
from pundit.agents.spoken_numbers import parse_vi_number, parse_vi_ordinal

LEX = SpokenLexicon.from_match(match_context("dominant_loser", 42))

EXAMPLES = [  # section 5.6, on real lines of dominant_loser-42
    (
        "Cơ hội ngon ăn ở phút 9: J. Ellery dứt điểm (xG 0.35), bóng đi chệch khung thành.",
        "Cơ hội ngon ăn ở phút chín: Ellery dứt điểm, ích giê không phẩy ba mươi lăm, bóng đi "
        "chệch khung thành.",
    ),
    (
        "Phút 27, M. Ribas ghi bàn cho Kestrel Athletic với cú dứt điểm xG 0.21. Tỷ số: Harbor "
        "Lions FC 0-1 Kestrel Athletic.",
        "Phút hai mươi bảy, Ribas ghi bàn cho Kestrel Athletic với cú dứt điểm ích giê không "
        "phẩy hai mươi mốt. Tỷ số: Harbor Lions không, Kestrel Athletic một.",
    ),
    (
        "VÀOOO! J. Ellery ghi bàn cho Kestrel Athletic! Harbor Lions FC 1-2 Kestrel Athletic.",
        "Vào! Ellery ghi bàn cho Kestrel Athletic! Harbor Lions một, Kestrel Athletic hai.",
    ),
    (
        "Sau 75 phút: Harbor Lions FC kiểm soát bóng 55%, 16 cú sút, xG 2.08; Kestrel Athletic "
        "có 45%, 6 cú sút, xG 0.72.",
        "Sau bảy mươi lăm phút: Harbor Lions kiểm soát bóng năm mươi lăm phần trăm, mười sáu cú "
        "sút, ích giê hai phẩy không tám; Kestrel Athletic có bốn mươi lăm phần trăm, sáu cú "
        "sút, ích giê không phẩy bảy mươi hai.",
    ),
    (
        "Nghỉ giữa hiệp với tỷ số Harbor Lions FC 0-1 Kestrel Athletic. xG: HAR 0.48, KES 0.72.",
        "Nghỉ giữa hiệp với tỷ số Harbor Lions không, Kestrel Athletic một. Ích giê: Harbor "
        "Lions không phẩy bốn mươi tám, Kestrel Athletic không phẩy bảy mươi hai.",
    ),
    (
        "Các bàn thắng: M. Ribas (KES, phút 27); T. Calder (HAR, phút 63); J. Ellery (KES, phút "
        "78).",
        "Các bàn thắng: Ribas, Kestrel Athletic, phút hai mươi bảy; Calder, Harbor Lions, phút "
        "sáu mươi ba; Ellery, Kestrel Athletic, phút bảy mươi tám.",
    ),
]
FORMS = [  # a text starts with a capital once read ("xG" -> "Ích giê")
    ("từ -1 đến 1", "Từ âm một đến một"),
    ("phút 60-75", "Phút sáu mươi đến bảy mươi lăm"),
    ("(phút 45+3)", "Phút bốn mươi lăm cộng ba"),
    ("Bàn thắng 45+2'!", "Bàn thắng phút bốn mươi lăm cộng hai!"),
    ("Phút 63': vào!", "Phút sáu mươi ba: vào!"),
    ("bàn thứ 2 và lần thứ 4, thứ 1", "Bàn thứ hai và lần thứ tư, thứ nhất"),
    ("1/3 cuối sân", "Một phần ba cuối sân"),
    (
        "chạy 10.4 km, tốc độ 33 km/h, 120 m",
        "Chạy mười phẩy bốn ki lô mét, tốc độ ba mươi ba ki lô mét trên giờ, một trăm hai mươi mét",
    ),
    ("hiệp 1 và hiệp 2", "Hiệp một và hiệp hai"),
    ("57 % và 2,24", "Năm mươi bảy phần trăm và hai phẩy hai mươi bốn"),
    ("PPDA 8.5, VAR", "Pê pê đê a tám phẩy năm, vê a rờ"),
    ("Bàn thắng không được công nhận.", "Bàn thắng không được công nhận."),
    ("xG 0.00 \N{EN DASH} rất thấp", "Ích giê không phẩy không không, rất thấp"),
    ("GOAAAL \N{BLACK STAR} cực ĐẸP", "Goal cực ĐẸP"),
]


@pytest.mark.parametrize(("written", "spoken"), EXAMPLES)
def test_real_lines(written: str, spoken: str) -> None:
    assert normalize(written, "vi", LEX).text == spoken


@pytest.mark.parametrize(("written", "spoken"), FORMS)
def test_forms(written: str, spoken: str) -> None:
    assert normalize(written, "vi", LEX).text == spoken


def test_numbers_are_placed_and_read_back() -> None:
    for written, _ in EXAMPLES + FORMS:
        spoken = normalize(written, "vi", LEX)
        for number in spoken.numbers:
            words = spoken.text[number.start : number.end]
            assert words == number.spoken
            read = parse_vi_ordinal(words) if number.kind == "ordinal" else parse_vi_number(words)
            assert Decimal(read) == number.value, (written, number)


def test_score_keeps_the_order_of_the_numbers() -> None:
    spoken = normalize("Harbor Lions FC 2-1 Kestrel Athletic", "vi", LEX)
    assert [n.value for n in spoken.numbers] == [2, 1]
    assert [spoken.text[a:b] for a, b in spoken.names] == ["Harbor Lions", "Kestrel Athletic"]


def test_word_numbers_are_kept_and_known() -> None:
    spoken = normalize("Đội của bạn có một trận hoà, nhận hai thẻ vàng.", "vi", LEX)
    assert spoken.text == "Đội của bạn có một trận hoà, nhận hai thẻ vàng."
    assert [(n.spoken, n.kind) for n in spoken.numbers] == [("một", "word"), ("hai", "word")]


def test_vietnamese_names_and_the_other_squads() -> None:
    assert (LEX.home_spoken, dict(LEX.names)["HAR"]) == ("Harbor Lions", "Harbor Lions")  # no FC
    assert dict(LEX.names)["J. Ellery"] == "Ellery"
    lex = SpokenLexicon.from_match(match_context("comeback_var", 7))
    spoken = normalize("IRO 3-2 SAL", "vi", lex).text
    assert spoken == "Ironvale United ba, Saltmarsh Rovers hai"
    assert normalize("Gia Bảo dứt điểm", "vi", LEX).text == "Gia Bảo dứt điểm"


def test_english_has_no_pack_yet_and_empty_lexicon_works() -> None:
    with pytest.raises(ValueError):
        normalize("Kick-off", "en", LEX)
    assert normalize("Phút 9", "vi", EMPTY_LEXICON).text == "Phút chín"


TOKENS = [
    "Phút", "9", "27'", "45+2'", "0.35", "2.08", "57%", "Harbor Lions FC 1-2 Kestrel Athletic",
    "HAR", "KES", "xG", "PPDA", "J. Ellery", "Gia Bảo", "(", ")", ",", ".", ";", "hiệp 2",
    "thứ 4", "-1 đến 1", "10.4 km", "VÀOOO!", "không", "một cú sút", "hai thẻ vàng", "15-1",
    "1/3", "ghi bàn", "Kestrel Athletic", "\N{EN DASH}",
]  # fmt: skip


@given(st.lists(st.sampled_from(TOKENS), min_size=1, max_size=14))
def test_any_mix_of_real_tokens_reads_every_number_back(tokens: list[str]) -> None:
    written = " ".join(tokens)
    spoken = normalize(written, "vi", LEX)
    assert not any(ch.isdigit() for ch in spoken.text), (written, spoken.text)
    assert spoken.text == " ".join(spoken.text.split())
    for number in spoken.numbers:
        words = spoken.text[number.start : number.end]
        assert words == number.spoken
        read = parse_vi_ordinal(words) if number.kind == "ordinal" else parse_vi_number(words)
        assert Decimal(read) == number.value, (written, number)
