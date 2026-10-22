"""'Người mới xem': short explanations of the rules and of the statistics, shown to beginners
when the situation happens (docs/day3-design.md section 9).

The texts are static and reviewed by hand; tests check that every rule has both languages, the
lengths, that no banned word slips in, and that the statistics match the definitions of
stats/registry.py. They never contain a number of the match.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache

from pundit.agents.contracts import Lang, RuleId, RuleView
from pundit.agents.language import Term, Text, find_terms, vi_mode


@dataclass(frozen=True)
class RuleText:
    title_vi: str
    title_en: str
    short_vi: str
    short_en: str
    full_vi: str
    full_en: str


RULES: dict[RuleId, RuleText] = {
    "offside": RuleText(
        "Việt vị",
        "Offside",
        "Việt vị: cầu thủ tấn công đứng gần khung thành đối phương hơn cả bóng lẫn hậu vệ "
        "áp chót đúng lúc đồng đội chuyền.",
        "Offside: an attacker is nearer the opponents' goal line than both the ball and the "
        "second-last defender when a team-mate passes.",
        "Cầu thủ tấn công bị việt vị nếu ở phần sân đối phương, đứng gần đường biên ngang cuối "
        "sân hơn cả bóng lẫn cầu thủ phòng ngự áp chót, đúng lúc đồng đội chuyền bóng, và có "
        "tham gia vào tình huống. Không có việt vị khi nhận bóng trực tiếp từ quả ném biên, "
        "phạt góc hay phát bóng. Đội phòng ngự được hưởng một quả đá phạt gián tiếp.",
        "An attacker is offside when, in the opponents' half, they are nearer the goal line "
        "than both the ball and the second-last defender at the moment a team-mate plays the "
        "ball, and they become involved in play. There is no offside directly from a throw-in, "
        "a corner or a goal kick. The defending team gets an indirect free kick.",
    ),
    "var": RuleText(
        "VAR",
        "VAR",
        "VAR là trợ lý trọng tài video, chỉ can thiệp khi có sai sót rõ ràng.",
        "VAR is the video assistant referee; it only steps in for clear and obvious errors.",
        "VAR (trợ lý trọng tài video) chỉ can thiệp khi có sai sót rõ ràng ở bốn loại tình "
        "huống: bàn thắng, phạt đền, thẻ đỏ trực tiếp và nhầm người. Trọng tài có thể ra màn "
        "hình bên sân xem lại rồi giữ nguyên hoặc đổi quyết định.",
        "The VAR (video assistant referee) only steps in for clear and obvious errors in four "
        "kinds of decision: goals, penalties, straight red cards and mistaken identity. The "
        "referee can review the footage at the pitch-side monitor, then keep or change the "
        "decision.",
    ),
    "penalty": RuleText(
        "Phạt đền",
        "Penalty",
        "Phạt đền: đội phòng ngự phạm lỗi ngay trong vòng cấm nhà; người sút chỉ còn đối mặt "
        "thủ môn.",
        "Penalty: the defending team fouled inside its own box; the taker faces only the "
        "goalkeeper.",
        "Phạt đền được thổi khi đội phòng ngự phạm lỗi (như đá người, kéo người, chạm tay) "
        "ngay trong vòng cấm của mình. Bóng đặt ở chấm phạt đền, chỉ còn người sút đối mặt "
        "thủ môn.",
        "A penalty is given when the defending team commits a foul (a kick, a pull, a "
        "handball) inside its own penalty area. The ball is placed on the penalty spot and "
        "only the taker faces the goalkeeper.",
    ),
    "second_yellow": RuleText(
        "Thẻ vàng thứ hai",
        "Second yellow card",
        "Thẻ vàng thứ hai thành thẻ đỏ: cầu thủ rời sân, đội chơi thiếu người.",
        "A second yellow becomes a red: the player leaves and the team plays a player down.",
        "Thẻ vàng thứ hai trong trận đồng nghĩa với thẻ đỏ: cầu thủ phải rời sân và đội phải "
        "chơi thiếu người tới hết trận.",
        "A second yellow card in the same match means a red card: the player must leave the "
        "pitch and the team plays a player down until the end.",
    ),
    "red_card": RuleText(
        "Thẻ đỏ",
        "Red card",
        "Thẻ đỏ: cầu thủ rời sân ngay, đội chơi thiếu người và không được thay bù.",
        "Red card: the player leaves at once; the team plays a player down and cannot replace "
        "them.",
        "Thẻ đỏ trực tiếp dành cho lỗi nghiêm trọng như vào bóng nguy hiểm, hành vi bạo lực "
        "hay phạm lỗi để chặn một cơ hội ghi bàn rõ ràng. Cầu thủ phải rời sân ngay, đội chơi "
        "thiếu một người và không được thay người bù vào.",
        "A straight red card is for a serious offence such as a dangerous tackle, violent "
        "conduct or a foul that denies an obvious goal-scoring opportunity. The player leaves "
        "the pitch at once; the team plays a player down and may not replace them.",
    ),
    "yellow_card": RuleText(
        "Thẻ vàng",
        "Yellow card",
        "Thẻ vàng là lời cảnh cáo; nhận hai thẻ vàng trong một trận thì phải rời sân.",
        "A yellow card is a caution; two in the same match mean a sending-off.",
        "Thẻ vàng là lời cảnh cáo, ví dụ cho lỗi phạm lỗi chiến thuật, phản ứng trọng tài hay "
        "câu giờ. Nhận hai thẻ vàng trong cùng một trận thì bị truất quyền thi đấu.",
        "A yellow card is a caution, for example for a tactical foul, dissent or time-wasting. "
        "A player who receives two yellow cards in the same match is sent off.",
    ),
    "corner": RuleText(
        "Phạt góc",
        "Corner kick",
        "Phạt góc: bóng đi hết đường biên ngang sau khi chạm cầu thủ phòng ngự lần cuối.",
        "Corner: the ball crossed the goal line after last touching a defender.",
        "Đội tấn công được hưởng phạt góc khi cầu thủ đội phòng ngự là người chạm bóng cuối "
        "cùng trước khi bóng đi hết đường biên ngang (mà không thành bàn). Quả phạt góc đá từ "
        "góc sân và có thể ghi bàn trực tiếp.",
        "The attacking team gets a corner kick when a defender was the last to touch the ball "
        "before it crossed the goal line without a goal. It is taken from the corner arc and "
        "can be scored directly.",
    ),
    "substitution": RuleText(
        "Thay người",
        "Substitutions",
        "Mỗi đội được thay tối đa năm cầu thủ, trong không quá ba lần dừng trận.",
        "Each team may make up to five substitutions, in no more than three stoppages.",
        "Mỗi đội được thay tối đa năm cầu thủ, trong không quá ba lần dừng trận (không tính "
        "giờ nghỉ giữa hiệp). Cầu thủ đã rời sân thì không được vào lại.",
        "Each team may use up to five substitutes, in no more than three stoppages of play "
        "(half-time does not count). A player who has left the pitch may not come back.",
    ),
    "added_time": RuleText(
        "Bù giờ",
        "Added time",
        "Bù giờ bù cho những lần trận đấu phải dừng trong hiệp.",
        "Added time makes up for the stoppages of the half.",
        "Bù giờ là thời gian cộng thêm cuối mỗi hiệp để bù cho các lần dừng trận như thay "
        "người, chấn thương, VAR hay ăn mừng bàn thắng. Bảng báo bù giờ là thời gian tối "
        "thiểu; trọng tài có thể cho đá lâu hơn.",
        "Added time is played at the end of each half to make up for stoppages such as "
        "substitutions, injuries, VAR reviews and goal celebrations. The board shows the "
        "minimum; the referee may play longer.",
    ),
    "xg": RuleText(
        "xG (bàn thắng kỳ vọng)",
        "xG (expected goals)",
        "xG ước lượng khả năng một cú sút thành bàn, từ 0 đến 1.",
        "xG estimates how likely a shot is to become a goal, from 0 to 1.",
        "xG (bàn thắng kỳ vọng) ước lượng khả năng một cú sút thành bàn, từ 0 đến 1, dựa trên "
        "vị trí và kiểu dứt điểm. Tổng xG cao cho thấy một đội tạo được nhiều cơ hội tốt, dù "
        "chưa chắc đã ghi bàn.",
        "xG (expected goals) estimates how likely a shot is to become a goal, from 0 to 1, "
        "based on where and how it was taken. A high total xG shows a team creates good "
        "chances, even if it has not scored.",
    ),
    "possession": RuleText(
        "Kiểm soát bóng",
        "Possession",
        "Kiểm soát bóng: phần trăm thời gian mỗi đội giữ bóng khi bóng đang lăn.",
        "Possession: the share of the ball-in-play time each team had the ball.",
        "Kiểm soát bóng ở đây tính theo thời gian bóng lăn: mỗi đội giữ bóng bao nhiêu phần "
        "trăm thời gian bóng đang trong cuộc, không tính lúc bóng chết. Cầm bóng nhiều hơn "
        "chưa chắc đã nguy hiểm hơn.",
        "Possession here is measured on ball-in-play time: the share of the time the ball is "
        "live that each team had it, leaving out dead-ball time. More of the ball does not "
        "always mean more danger.",
    ),
    "momentum": RuleText(
        "Momentum (thế trận)",
        "Momentum",
        "Momentum so sánh mức đe doạ tấn công của hai đội, từ -1 đến 1.",
        "Momentum compares the attacking threat of the two teams, from -1 to 1.",
        "Momentum (thế trận) so sánh mức đe doạ tấn công của hai đội trong một khoảng thời "
        "gian, từ -1 đến 1, tính từ cú sút, số lần vào 1/3 cuối sân và vòng cấm, phạt góc và "
        "số lần đoạt bóng ở phần sân đối phương. Số dương nghĩa là thế trận nghiêng về đội "
        "đang xét.",
        "Momentum compares the attacking threat of the two teams over a period, from -1 to 1, "
        "built from shots, entries into the final third and the box, corners and balls won in "
        "the opponents' half. A positive value means the game leans towards the team in "
        "question.",
    ),
    "ppda": RuleText(
        "PPDA (cường độ pressing)",
        "PPDA (pressing)",
        "PPDA càng thấp, pressing càng rát.",
        "The lower the PPDA, the harder the press.",
        "PPDA đo cường độ pressing: số đường chuyền đối phương được thực hiện cho mỗi pha "
        "phòng ngự (tắc bóng, cắt bóng, phạm lỗi) của đội mình ở phần sân phía trên. Càng "
        "thấp nghĩa là pressing càng rát.",
        "PPDA measures pressing: the passes the opponents are allowed per defensive action "
        "(tackle, interception, foul) of the team in the upper part of the pitch. The lower "
        "it is, the harder the press.",
    ),
    "chaos": RuleText(
        "Kiểm soát hay hỗn loạn",
        "Control or chaos",
        "Chỉ số từ 0 đến 1: càng cao, trận đấu càng hỗn loạn.",
        "From 0 to 1: the higher it is, the more chaotic the game.",
        "Chỉ số kiểm soát hay hỗn loạn, từ 0 đến 1: cao khi bóng đổi chân liên tục và không "
        "đội nào làm chủ; thấp khi trận đấu có trật tự hoặc một đội áp đảo rõ.",
        "The control-or-chaos index runs from 0 to 1: high when the ball keeps changing hands "
        "and nobody is in charge; low when the game is orderly or one team clearly dominates.",
    ),
    "tempo": RuleText(
        "Nhịp độ",
        "Tempo",
        "Nhịp độ: số đường chuyền mỗi phút một đội giữ bóng.",
        "Tempo: passes per minute of a team's own possession.",
        "Nhịp độ là số đường chuyền mỗi phút một đội giữ bóng: cao nghĩa là đội đó chuyền "
        "nhanh, đẩy nhịp trận lên.",
        "Tempo is the number of passes a team plays per minute of its own possession: a high "
        "tempo means quick passing that speeds the game up.",
    ),
}

# Football rules before statistics; the first rule a viewer has not seen yet is explained
RULE_PRIORITY: tuple[RuleId, ...] = (
    "offside", "var", "penalty", "second_yellow", "red_card", "yellow_card", "corner",
    "substitution", "added_time", "xg", "possession", "momentum", "ppda", "chaos", "tempo",
)  # fmt: skip
REPEAT_SHORT: frozenset[RuleId] = frozenset({"penalty", "red_card", "second_yellow"})
STAT_RULES: dict[RuleId, str] = {  # statistics rule -> metric of stats/registry.py
    "xg": "xg",
    "possession": "possession",
    "momentum": "momentum",
    "ppda": "ppda",
    "chaos": "chaos_index",
    "tempo": "tempo",
}
DIGITS_ALLOWED: dict[RuleId, frozenset[str]] = {  # ranges of a definition, never match numbers
    "xg": frozenset({"0", "1"}),
    "momentum": frozenset({"1", "3"}),  # "-1 đến 1", "1/3 cuối sân"
    "chaos": frozenset({"0", "1"}),
}


def rule_view(rule: RuleId, language: Lang, depth: str) -> RuleView:
    text = RULES[rule]
    vi = language == "vi"
    if depth == "full":
        body = text.full_vi if vi else text.full_en
    else:
        body = text.short_vi if vi else text.short_en
    return RuleView(
        rule=rule,
        title=text.title_vi if vi else text.title_en,
        text=body,
        depth="full" if depth == "full" else "short",
    )


def ordered(rules: tuple[RuleId, ...] | list[RuleId]) -> list[RuleId]:
    """Rules in priority order, without duplicates."""
    present = set(rules)
    return [r for r in RULE_PRIORITY if r in present]


# Words that name the metric of a statistics rule in a commentary text (section 9.2)
STAT_TERMS: dict[RuleId, tuple[str, ...]] = {
    "xg": ("xg", "bàn thắng kỳ vọng", "expected goals"),
    "possession": ("kiểm soát bóng", "cầm bóng", "possession"),
    "momentum": ("momentum", "thế trận"),
    "ppda": ("ppda", "pressing"),
    "chaos": ("chaos", "hỗn loạn"),
    "tempo": ("tempo", "nhịp độ", "nhịp trận"),
}


@cache
def _stat_terms() -> list[Term[RuleId]]:
    return [Term(word, rule, vi_mode(word)) for rule, words in STAT_TERMS.items() for word in words]


def mentioned_rules(rules: Sequence[RuleId], texts: Sequence[str]) -> tuple[RuleId, ...]:
    """The rules of an item: a statistics rule stays only when one of its texts names the
    metric, so a beginner is never taught about xG under a line that does not say xG."""
    named: set[RuleId] = set()
    for text in texts:
        named |= {hit.payload for hit in find_terms(Text.of(text), _stat_terms())}
    return tuple(rule for rule in rules if rule not in STAT_TERMS or rule in named)
