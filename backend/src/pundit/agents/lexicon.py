"""Claim vocabulary, Vietnamese and English (docs/day2-design.md section 4.2.5-4.2.8).

Every phrase maps to a `Lex` entry. Vietnamese single syllables are matched with diacritics
("thẻ" is a card, "thế" is not); phrases and English words are matched folded. The two
languages have separate lists: the analyst only uses the list of the claim's language, so
English "the" can never be read as Vietnamese "thẻ".
"""

import re
from dataclasses import dataclass
from functools import cache
from typing import Literal

from pundit.agents.contracts import Lang
from pundit.agents.language import Term, fold, is_single_word, vi_mode

Category = Literal[
    "metric",
    "dominance",
    "result",
    "comparative",  # "more ... than", "nhiều ... hơn": direction +1 / -1
    "than",  # Vietnamese "hơn"
    "adjective",  # Vietnamese "nhiều", "ít", ... (direction of a later "hơn")
    "equality",
    "modifier",  # op for a stated number: "only", "at least", "khoảng"
    "zero",  # "no corners"
    "zero_end",  # Vietnamese "nào" in "chưa có ... nào"
    "hardly",  # "hardly any" -> a count of at most 2
    "negation",
    "passive",  # Vietnamese "bị"
    "conj",
    "out_of_scope",
]


@dataclass(frozen=True)
class Lex:
    cat: Category
    metric: str | None = None
    literal: bool = False  # the term names the metric itself ("PPDA"): no polarity flip
    verb: bool = False  # "ghi bàn", "scored": a scoring claim without a number means >= 1
    opponent: bool = False  # "bàn thua", "conceded": the metric is the opponent's
    direction: int = 0
    op: str | None = None
    passive: bool = False  # "bị áp đảo", "were dominated": the subject is the opponent
    by: bool = False  # "dominated by X": the subject is the entity after the phrase


def _metric(
    metric: str, *phrases: str, literal: bool = False, verb: bool = False, opponent: bool = False
) -> list[tuple[str, Lex]]:
    lex = Lex("metric", metric=metric, literal=literal, verb=verb, opponent=opponent)
    return [(p, lex) for p in phrases]


def _cat(cat: Category, *phrases: str, **fields: object) -> list[tuple[str, Lex]]:
    return [(p, Lex(cat, **fields)) for p in phrases]  # type: ignore[arg-type]


VI_ENTRIES: list[tuple[str, Lex]] = [
    *_metric("possession", "kiểm soát bóng", "cầm bóng", "giữ bóng", "kiểm soát",
             "tỷ lệ kiểm soát bóng", "quyền kiểm soát bóng", "possession"),
    *_metric("possession_time", "thời gian kiểm soát bóng", "thời gian giữ bóng",
             "thời gian cầm bóng"),
    *_metric("shots", "cú sút", "pha sút", "lần sút", "sút", "dứt điểm", "pha dứt điểm",
             "cú dứt điểm", "lần dứt điểm", "sút bóng"),
    *_metric("shots_on_target", "sút trúng đích", "cú sút trúng đích", "trúng đích",
             "dứt điểm trúng đích", "trúng khung thành", "sút trúng khung thành",
             "cú sút trúng khung thành"),
    *_metric("xg", "xg", "bàn thắng kỳ vọng", "chỉ số xg", "expected goals"),
    *_metric("score", "bàn thắng", "bàn", "pha lập công"),
    *_metric("score", "ghi bàn", "lập công", "nổ súng", "ghi được bàn", "ghi bàn thắng",
             verb=True),
    *_metric("score", "bàn thua", "thủng lưới", "để thủng lưới", "để lọt lưới", opponent=True),
    *_metric("corners", "phạt góc", "quả phạt góc", "pha phạt góc", "lần phạt góc",
             "cú phạt góc"),
    *_metric("fouls", "phạm lỗi", "lỗi", "pha phạm lỗi", "lần phạm lỗi", "số lần phạm lỗi"),
    *_metric("cards", "thẻ", "thẻ phạt", "chiếc thẻ"),
    *_metric("var_overturns", "bàn bị VAR huỷ", "bàn bị VAR hủy", "bàn bị huỷ", "bàn bị hủy",
             "bàn thắng bị huỷ", "bàn thắng bị hủy", "bị VAR huỷ", "bị VAR hủy",
             "bị VAR từ chối", "bàn bị từ chối", "bàn thắng bị từ chối",
             # Day 6: the active voice ("VAR đã huỷ một bàn thắng của X")
             "VAR huỷ", "VAR hủy", "VAR đã huỷ", "VAR đã hủy", "VAR từ chối", "VAR đã từ chối",
             "VAR không công nhận", "VAR đã không công nhận", verb=True),
    *_metric("yellow_cards", "thẻ vàng", "chiếc thẻ vàng"),
    *_metric("red_cards", "thẻ đỏ", "chiếc thẻ đỏ", "thẻ đỏ trực tiếp"),
    *_metric("red_cards", "bị đuổi khỏi sân", "bị truất quyền thi đấu", verb=True),
    *_metric("offsides", "việt vị", "lần việt vị", "pha việt vị", "rơi vào thế việt vị"),
    *_metric("passes", "đường chuyền", "lần chuyền", "chuyền bóng", "pha chuyền", "chuyền",
             "lượt chuyền"),
    *_metric("pass_accuracy", "chuyền chính xác", "tỷ lệ chuyền chính xác",
             "độ chính xác chuyền bóng", "chuyền bóng chính xác", "tỷ lệ chuyền thành công",
             "độ chính xác"),
    *_metric("box_entries", "vào vòng cấm", "lần vào vòng cấm", "xâm nhập vòng cấm",
             "đưa bóng vào vòng cấm", "lần đưa bóng vào vòng cấm", "pha xâm nhập vòng cấm",
             "vòng cấm"),
    *_metric("final_third_entries", "1/3 cuối sân", "một phần ba cuối sân", "phần ba cuối sân",
             "vào 1/3 cuối sân", "lần vào 1/3 cuối sân", "đưa bóng vào 1/3 cuối sân",
             "1/3 sân đối phương"),
    *_metric("ppda", "pressing", "gây áp lực", "áp sát", "pressing tầm cao", "tạo áp lực"),
    *_metric("ppda", "ppda", literal=True),
    *_metric("momentum", "thế trận", "momentum", "đà tấn công"),
    *_metric("tackles", "tắc bóng", "pha tắc bóng", "lần tắc bóng", "xoạc bóng"),
    *_metric("interceptions", "cắt bóng", "pha cắt bóng", "lần cắt bóng", "đánh chặn",
             "pha đánh chặn"),
    *_metric("tempo", "nhịp độ", "tempo"),
    *_metric("shot_speed_max", "cú sút mạnh nhất", "tốc độ sút", "tốc độ cú sút",
             "cú sút nhanh nhất"),
    # Day 6: English words in a Vietnamese sentence, as commentators mix them
    *_metric("shots", "shots", "shot"),
    *_metric("shots_on_target", "shots on target", "shot on target"),
    *_metric("corners", "corners", "corner"),
    *_metric("fouls", "fouls", "foul"),
    *_metric("passes", "passes"),
    *_metric("tackles", "tackles"),
    *_metric("interceptions", "interceptions"),
    *_cat("dominance", "áp đảo", "lấn lướt", "lấn át", "làm chủ thế trận", "nắm thế trận",
          "kiểm soát thế trận", "chiếm ưu thế", "vượt trội", "dồn ép", "ép sân", "thống trị",
          "chiếm thế thượng phong", "làm chủ trận đấu", "kiểm soát trận đấu", "chơi tốt hơn",
          "chơi hay hơn", "chơi lấn lướt", "chơi áp đảo", "làm chủ cuộc chơi",
          "nắm quyền kiểm soát", "chơi vượt trội"),
    *_cat("dominance", "bị áp đảo", "bị lấn lướt", "bị lấn át", "bị dồn ép", "bị ép sân",
          "bị thống trị", "bị vượt trội", "bị làm chủ", passive=True),
    *_cat("passive", "bị"),
    *_cat("result", "thắng", "chiến thắng", "giành chiến thắng", "thắng trận", "đánh bại",
          "thắng lợi", "giành thắng lợi", "dẫn trước", "đang dẫn", "thắng cuộc", op=">"),
    # Day 6: "dẫn" alone ("Kestrel dẫn 1-0") is a result only with a score (also "to dribble")
    *_cat("result", "dẫn", op=">"),
    *_cat("result", "thua", "để thua", "thua trận", "thất bại", "bại trận", "nhận thất bại",
          "bị dẫn", "đang bị dẫn", "thua cuộc", op="<"),
    *_cat("result", "hoà", "hòa", "hoà nhau", "hòa nhau", "chia điểm", "bất phân thắng bại",
          op="=="),
    *_cat("comparative", "nhiều hơn", "cao hơn", "mạnh hơn", "tốt hơn", "lớn hơn",
          "nhỉnh hơn", "trội hơn", "vượt", "nhanh hơn", "dài hơn", "mạnh mẽ hơn", direction=1),
    *_cat("comparative", "ít hơn", "thấp hơn", "yếu hơn", "kém hơn", "nhỏ hơn", "thua kém",
          "chậm hơn", "ngắn hơn", direction=-1),
    *_cat("than", "hơn"),
    *_cat("adjective", "nhiều", "cao", "mạnh", "tốt", "lớn", "nhanh", "dài", "trội",
          direction=1),
    *_cat("adjective", "ít", "thấp", "yếu", "kém", "nhỏ", "chậm", "ngắn", direction=-1),
    *_cat("equality", "ngang nhau", "bằng nhau", "như nhau", "ngang bằng", "tương đương",
          "giống nhau", "bằng với", "ngang với"),
    *_cat("modifier", "chỉ", "chỉ có", "đúng", "vỏn vẹn", op="=="),
    *_cat("modifier", "ít nhất", "tối thiểu", "không dưới", "không ít hơn", op=">="),
    *_cat("modifier", "tối đa", "không quá", "không hơn", "nhiều nhất", op="<="),
    *_cat("modifier", "trên", op=">"),
    *_cat("modifier", "dưới", "chưa tới", "chưa đến", op="<"),
    *_cat("modifier", "khoảng", "gần", "xấp xỉ", "chừng", "tầm", "cỡ", "gần như", "ngót",
          "khoảng chừng", op="~"),
    *_cat("zero_end", "nào"),
    *_cat("hardly", "gần như không", "hầu như không", "gần như chẳng", "hầu như chẳng",
          "gần như chưa", "hầu như chưa"),
    *_cat("negation", "không", "chưa", "chẳng", "không hề", "chưa hề", "chẳng hề",
          "không phải", "đâu có"),
    *_cat("conj", "nhưng", "nhưng mà", "và", ", còn", "trong khi", "trong lúc", "dù",
          "mặc dù", "dẫu", "tuy", "tuy nhiên", "dẫu vậy"),
    *_cat("out_of_scope", "trọng tài", "thiên vị", "xứng đáng", "đáng lẽ", "lẽ ra", "sẽ",
          "dự đoán", "hay nhất", "xuất sắc nhất", "tệ nhất", "kém nhất", "đẹp nhất",
          "may mắn", "không may", "phong độ", "huấn luyện viên", "hlv", "khán giả",
          "cổ động viên", "chuyển nhượng", "đáng ra"),
]  # fmt: skip

EN_ENTRIES: list[tuple[str, Lex]] = [
    *_metric("possession", "possession", "ball possession", "of the ball",
             "share of possession", "share of the ball"),
    *_metric("possession_time", "time in possession", "time on the ball", "possession time"),
    *_metric("shots", "shots", "shot", "attempts", "attempt", "efforts", "shots at goal",
             "goal attempts", "attempts on goal"),
    *_metric("shots_on_target", "shots on target", "shot on target", "on target",
             "on-target shots", "shots on goal", "efforts on target", "attempts on target"),
    *_metric("xg", "xg", "expected goals", "expected goal"),
    *_metric("score", "goals", "goal"),
    *_metric("score", "scored", "netted", "found the net", "on the scoresheet", "scores",
             verb=True),
    *_metric("score", "conceded", "goals conceded", "goals against", opponent=True),
    *_metric("corners", "corners", "corner", "corner kicks", "corner kick"),
    *_metric("fouls", "fouls", "foul", "fouls committed"),
    *_metric("cards", "cards", "card", "bookings", "booking"),
    *_metric("var_overturns", "disallowed goals", "disallowed goal", "goals disallowed",
             "goal disallowed", "overturned by var", "ruled out by var", "chalked off",
             "goals overturned", "goal overturned", "goal overturned by var",
             "goals overturned by var", "goal disallowed by var", "goals disallowed by var",
             "goal ruled out by var", "goals ruled out by var",
             # Day 6: the active voice ("VAR overturned an Ironvale goal")
             "var overturned", "var disallowed", "var ruled out", "var chalked off",
             "var cancelled", "var canceled", verb=True),
    *_metric("yellow_cards", "yellow cards", "yellow card", "yellows"),
    *_metric("red_cards", "red cards", "red card", "reds", "sending off", "sendings off",
             "dismissals", "dismissal"),
    *_metric("red_cards", "sent off", verb=True),  # "had a player sent off": at least one
    *_metric("offsides", "offsides", "offside", "caught offside", "offside calls"),
    *_metric("passes", "passes", "pass"),
    *_metric("pass_accuracy", "pass accuracy", "passing accuracy", "pass completion",
             "pass completion rate", "passing percentage", "accuracy"),
    *_metric("box_entries", "box entries", "entries into the box", "penalty area entries",
             "entries into the penalty area", "penalty box entries"),
    *_metric("final_third_entries", "final third entries", "final-third entries",
             "entries into the final third", "final third"),
    *_metric("ppda", "pressing", "pressed", "press", "pressure"),
    *_metric("ppda", "ppda", literal=True),
    *_metric("momentum", "momentum"),
    *_metric("tackles", "tackles", "tackle"),
    *_metric("interceptions", "interceptions", "interception"),
    *_metric("tempo", "tempo"),
    *_metric("shot_speed_max", "fastest shot", "shot speed", "hardest shot"),
    *_cat("dominance", "dominated", "dominate", "dominates", "dominating", "dominant",
          "on top", "in control", "controlled", "controlled the game", "controlled the match",
          "had the upper hand", "the better side", "the better team", "bossed",
          "ran the show", "outplayed", "on the front foot"),
    *_cat("dominance", "were dominated", "was dominated", "been dominated", "got dominated",
          "were outplayed", "was outplayed", "were second best", "were on the back foot",
          "was on the back foot", "were pinned back", passive=True),
    *_cat("dominance", "were dominated by", "was dominated by", "dominated by",
          "were outplayed by", "was outplayed by", passive=True, by=True),
    *_cat("result", "won", "win", "wins", "beat", "beats", "victorious", "took the win",
          "came out on top", "are leading", "were leading", "is leading", "are ahead",
          "were ahead", "is ahead", "defeated", "led", "leads", "lead", op=">"),
    *_cat("result", "lost", "lose", "loses", "were beaten", "was beaten", "got beaten",
          "were defeated", "was defeated", "are trailing", "were trailing", "is trailing",
          "are behind", "were behind", "trailed", "trails", op="<"),
    *_cat("result", "drew", "draw", "drawn", "tied", op="=="),
    *_cat("comparative", "more", "higher", "greater", "stronger", "better", "harder",
          "more intense", "outshot", "out-shot", "outscored", direction=1),
    *_cat("comparative", "fewer", "less", "lower", "weaker", "worse", direction=-1),
    *_cat("equality", "the same number of", "same number of", "the same amount of",
          "as many", "as much", "equal", "equal number of", "level on", "identical",
          "the same"),
    *_cat("modifier", "only", "just", "exactly", op="=="),
    *_cat("modifier", "at least", "no fewer than", "no less than", op=">="),
    *_cat("modifier", "at most", "no more than", "up to", op="<="),
    *_cat("modifier", "more than", "over", "above", op=">"),
    *_cat("modifier", "fewer than", "less than", "under", "below", op="<"),
    *_cat("modifier", "around", "about", "roughly", "approximately", "nearly", "almost",
          "close to", "approx", "some", op="~"),
    *_cat("zero", "no", "not a single", "without a", "without any", "not one",
          "failed to register a", "failed to register any"),
    *_cat("hardly", "hardly any", "barely any", "almost no", "next to no", "scarcely any"),
    *_cat("negation", "not", "didn't", "did not", "doesn't", "does not", "don't", "do not",
          "wasn't", "was not", "weren't", "were not", "isn't", "is not", "aren't", "are not",
          "hasn't", "has not", "haven't", "have not", "hadn't", "had not", "never",
          "failed to"),
    *_cat("conj", "but", "and", "while", "whereas", "despite", "although", "though", "yet",
          "even though", "in spite of"),
    *_cat("out_of_scope", "referee", "ref", "biased", "deserved", "deserve", "should have",
          "will", "would", "best player", "worst", "lucky", "unlucky", "man of the match",
          "manager", "coach", "fans", "crowd", "transfer"),
]  # fmt: skip

# The metric named by "outshot" / "outscored" when no other metric term is present.
IMPLIED_METRIC = {"outshot": "shots", "out-shot": "shots", "outscored": "score"}

# "gấp đôi", "gấp 3 lần", "twice as many", "3 times as many"
_RATIO_WORDS = {
    "doi": 2.0, "hai": 2.0, "ba": 3.0, "bon": 4.0, "nam": 5.0, "sau": 6.0, "bay": 7.0,
    "tam": 8.0, "chin": 9.0, "muoi": 10.0, "ruoi": 1.5,
    "twice": 2.0, "double": 2.0, "triple": 3.0, "treble": 3.0,
    "two": 2.0, "three": 3.0, "four": 4.0, "five": 5.0, "six": 6.0, "seven": 7.0,
    "eight": 8.0, "nine": 9.0, "ten": 10.0,
}  # fmt: skip
_VI_RATIO = "doi|hai|ba|bon|nam|sau|bay|tam|chin|muoi|ruoi"
_EN_RATIO = "two|three|four|five|six|seven|eight|nine|ten"
# "gấp tám lần": every number word, or the number would be read as a plain value (Day 6)
RATIO_PATTERNS = (
    re.compile(rf"(?<!\w)(?:nhieu\s+)?gap\s+(?P<n>\d+(?:[.,]\d+)?|{_VI_RATIO})(?:\s+lan)?(?!\w)"),
    re.compile(
        r"(?<!\w)(?P<n>twice|double|triple|treble)(?:\s+(?:as\s+many|as\s+much|the))?(?!\w)"
    ),
    re.compile(
        rf"(?<!\w)(?P<n>\d+(?:\.\d+)?|{_EN_RATIO})\s+times\s+(?:as\s+many|as\s+much|more)(?!\w)"
    ),
)


def ratio_value(token: str) -> float | None:
    if token in _RATIO_WORDS:
        return _RATIO_WORDS[token]
    try:
        value = float(token.replace(",", "."))
    except ValueError:
        return None
    return value if value > 1 else None


def _mode(phrase: str, lang: Lang) -> Literal["lower", "folded"]:
    if lang == "vi":
        return vi_mode(phrase)
    return "folded" if not is_single_word(phrase) or phrase.isascii() else "lower"


@cache
def lexicon(lang: Lang) -> tuple[Term[Lex], ...]:
    entries = VI_ENTRIES if lang == "vi" else EN_ENTRIES
    return tuple(Term(phrase, lex, _mode(phrase, lang)) for phrase, lex in entries)


def known_words() -> frozenset[str]:
    """Every folded word of both lexicons (used to tell names from ordinary words)."""
    words: set[str] = set()
    for phrase, _ in (*VI_ENTRIES, *EN_ENTRIES):
        words.update(fold(phrase).split())
    return frozenset(words)
