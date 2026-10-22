"""Words of the studio: commentary glossary and style (prompts, reviewers) and the word lists the
studio Guardrail uses (docs/day3-design.md sections 8.2 and 12).

Outcome words: a saved shot is never "ghi bàn". Jargon: casual texts never say "xG". Assessment
words: "áp đảo" / "dominated" need a number in the same sentence when a model writes them.
"""

from pundit.agents.contracts import EventOutcome, Trigger
from pundit.agents.language import Term, vi_mode

# --------------------------------------------------------------------------- outcome words

OUTCOME_VI: dict[str, tuple[str, ...]] = {
    "goal": (
        "ghi bàn", "lập công", "vào lưới", "tung lưới", "thành bàn", "nâng tỷ số", "gỡ hoà",
        "gỡ hòa", "vươn lên dẫn", "mở tỷ số", "bàn thắng",
    ),
    "save": ("cứu thua", "cản phá", "bắt gọn", "đẩy bóng ra", "cứu vớt", "cứu bóng"),
    "block": ("bị chặn", "chặn lại", "cản lại"),
    "wide": ("chệch", "ra ngoài", "vọt xà", "không trúng đích"),
    "post": ("dội cột", "trúng cột", "dội xà", "trúng xà", "chạm cột"),
    "yellow": ("thẻ vàng",),
    "red": ("thẻ đỏ", "truất quyền"),
    "offside": ("việt vị",),
    # what never happens in these matches, or not at this moment (found in model drafts, P6)
    "extra_time": ("hiệp phụ", "loạt sút luân lưu", "luân lưu", "đá luân lưu"),
    "own_goal": ("phản lưới", "đốt lưới nhà"),
    "result": ("thắng trận", "giành chiến thắng", "chiến thắng", "thua trận", "nhận thất bại"),
}  # fmt: skip
OUTCOME_EN: dict[str, tuple[str, ...]] = {
    "goal": (
        "scores", "scored", "nets", "netted", "finds the net", "into the net", "equaliser",
        "equalizer", "opens the scoring", "goal!",
    ),
    "save": ("saves", "saved", "keeps it out", "parried", "parries", "kept it out",
             "kept out", "denied", "denies"),
    "block": ("blocked", "is blocked"),
    "wide": ("wide", "off target", "over the bar", "misses the target", "missed the target"),
    "post": ("hits the post", "hit the post", "off the post", "woodwork", "hits the bar",
             "crossbar"),
    "yellow": ("yellow card", "booked", "booking"),
    "red": ("red card", "sent off", "sending off"),
    "offside": ("offside",),
    "extra_time": ("extra time", "extra-time", "penalty shootout", "shootout", "shoot-out"),
    "own_goal": ("own goal", "own-goal"),
    "result": ("won the match", "won the game", "win the match", "wins the match", "victory",
               "lost the match", "lose the match"),
}  # fmt: skip
# Matched first (longest match wins), so they never count as outcome words
NEUTRAL_VI = ("bàn thắng kỳ vọng", "thẻ phạt", "thủ môn")
NEUTRAL_EN = ("expected goals", "goalkeeper", "goal kick", "goal kicks")

# Outcome of the trigger -> outcome words that contradict it
FORBIDDEN_BY_OUTCOME: dict[EventOutcome, frozenset[str]] = {
    "saved": frozenset({"goal", "block", "wide", "post"}),
    "blocked": frozenset({"goal", "save", "wide", "post"}),
    "off_target": frozenset({"goal", "save", "block", "post"}),
    "post": frozenset({"goal", "save", "block"}),
    "goal": frozenset({"wide"}),
    "yellow": frozenset({"red"}),
    "red": frozenset(),
    "second_yellow": frozenset(),
    "offside": frozenset({"goal"}),
    "overturned": frozenset(),
    "stands": frozenset(),
}
# Triggers without a goal: goal words there are a hallucination
NO_GOAL_KINDS = frozenset(
    {"corner", "offside", "added_time", "lull", "momentum_shift", "dangerous_spell",
     "substitution", "yellow_card", "red_card", "period_start"}
)  # fmt: skip


# Summary lines: a model gives the score but does not tell the goals (the recap does that
# from the verified list; small models invent who scored when)
SUMMARY_KINDS = frozenset(
    {"period_start", "period_end", "full_time", "stat_tick", "added_time", "lull"}
)


def forbidden_categories(trigger: Trigger, *, model_text: bool = False) -> frozenset[str]:
    """Outcome words that contradict what happened at this trigger."""
    found = set(FORBIDDEN_BY_OUTCOME[trigger.outcome]) if trigger.outcome is not None else set()
    if trigger.kind in NO_GOAL_KINDS:
        found.add("goal")
    found.add("extra_time")  # the synthetic matches never go to extra time
    if trigger.details.get("own_goal") != "true":
        found.add("own_goal")
    if trigger.kind != "full_time":
        found.add("result")  # nobody has won before the final whistle
    if model_text and trigger.kind in SUMMARY_KINDS:
        found.add("goal")
    return frozenset(found)


REASONS: dict[str, str] = {
    "extra_time": "there is no extra time or shootout in this match",
    "own_goal": "this goal is not an own goal",
    "result": "the match is not over",
}


def outcome_terms() -> list[Term[str]]:
    """Every outcome word (payload = category) plus the neutral phrases (payload "neutral")."""
    terms: list[Term[str]] = []
    for category, words in OUTCOME_VI.items():
        terms += [Term(w, category, vi_mode(w)) for w in words]
    for category, words in OUTCOME_EN.items():
        terms += [Term(w, category, "folded") for w in words]
    terms += [Term(w, "neutral", vi_mode(w)) for w in NEUTRAL_VI]
    terms += [Term(w, "neutral", "folded") for w in NEUTRAL_EN]
    return terms


# --------------------------------------------------------------------------- jargon, assessment

JARGON_VI = ("xg", "ppda", "momentum", "bàn thắng kỳ vọng", "chỉ số hỗn loạn", "nhịp độ")
JARGON_EN = ("xg", "ppda", "momentum", "expected goals", "chaos index", "tempo")

ASSESSMENT_VI = (
    "áp đảo", "lấn lướt", "vượt trội", "làm chủ", "chiếm ưu thế", "thống trị", "lép vế",
    "ép sân", "dồn ép", "tốt hơn", "kém hơn", "hay hơn", "nguy hiểm hơn", "hiệu quả hơn",
    "nhỉnh hơn", "thế trận nghiêng",
)  # fmt: skip
ASSESSMENT_EN = (
    "dominate", "dominates", "dominated", "dominating", "dominant", "on top", "in control",
    "controlled", "better", "worse", "superior", "outplayed", "more dangerous",
    "on the front foot",
)  # fmt: skip


def jargon_terms() -> list[Term[str]]:
    return [Term(w, w, vi_mode(w)) for w in JARGON_VI] + [Term(w, w, "folded") for w in JARGON_EN]


def assessment_terms() -> list[Term[str]]:
    return [Term(w, w, vi_mode(w)) for w in ASSESSMENT_VI] + [
        Term(w, w, "folded") for w in ASSESSMENT_EN
    ]


# --------------------------------------------------------------------------- commentary style

GLOSSARY_VI: tuple[tuple[str, str], ...] = (
    ("dứt điểm", "sút về phía khung thành"),
    ("cứu thua", "thủ môn chặn được cú sút"),
    ("cản phá", "chặn bóng, thường là thủ môn"),
    ("chệch cột dọc", "cú sút đi ra ngoài, sát cột"),
    ("dội cột / dội xà", "bóng chạm khung thành rồi bật ra"),
    ("bị chặn", "cú sút trúng cầu thủ phòng ngự"),
    ("đánh đầu", "dứt điểm bằng đầu"),
    ("sút xa", "dứt điểm từ ngoài vòng cấm"),
    ("phạt góc", "quả đá từ góc sân"),
    ("tình huống cố định", "phạt góc, đá phạt, ném biên"),
    ("việt vị", "lỗi đứng sai vị trí khi nhận bóng"),
    ("phản công", "tấn công nhanh ngay sau khi đoạt bóng"),
    ("lật cánh", "chuyển bóng từ cánh này sang cánh kia"),
    ("pressing tầm cao", "áp sát đối phương ngay ở phần sân của họ"),
    ("dồn ép", "tấn công liên tục"),
    ("kiểm soát bóng", "thời gian giữ bóng"),
    ("thế trận", "đội nào đang chơi lấn lướt"),
    ("bù giờ", "thời gian cộng thêm cuối hiệp"),
    ("thẻ vàng", "cảnh cáo"),
    ("thẻ đỏ", "truất quyền thi đấu"),
    ("vào sân thay", "cầu thủ dự bị vào sân"),
    ("rời sân", "cầu thủ ra khỏi sân"),
    ("gỡ hoà", "ghi bàn san bằng tỷ số"),
    ("vươn lên dẫn", "ghi bàn để dẫn trước"),
    ("mở tỷ số", "bàn thắng đầu tiên của trận"),
    ("cơ hội ngon ăn", "cơ hội ghi bàn rất rõ ràng"),
    ("bàn thắng kỳ vọng (xG)", "khả năng một cú sút thành bàn, từ 0 đến 1"),
    ("tung lưới", "ghi bàn vào lưới đối phương"),
    ("thủ môn", "người gác đền"),
    ("hiệp một / hiệp hai", "hai hiệp đấu"),
)

STYLE_VI = (
    "Trung lập, không cổ vũ đội nào, không phán xét cầu thủ hay trọng tài.",
    "Câu ngắn, động từ mạnh, đúng thuật ngữ trong bảng.",
    "Tên cầu thủ viết đúng như trong dữ liệu; tên đội viết đầy đủ hoặc mã 3 chữ.",
    "Số viết đúng như trường display (dấu chấm thập phân).",
)
