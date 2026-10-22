"""Talking-point templates (docs/day3-design.md section 6): 2-3 short lines for the studio, in
English and Vietnamese, each citing at least one verified number.

Lines have an angle: the headline (what happened), a stat, and the story of the match. Each
angle has phrasings chosen by `crc32`; a line whose values are missing is left out, and the
score line fills in so there are always at least two lines.
"""

from pundit.agents.contracts import FactPack, Lang, TalkingAngle, TalkingLine, Trigger
from pundit.agents.phrasebank import fill, pick
from pundit.agents.studio_guard import cited_keys
from pundit.agents.studio_values import pack_values

Lines = tuple[tuple[TalkingAngle, tuple[str, ...]], ...]
TALKING_KINDS = frozenset(
    {"goal", "goal_overturned", "red_card", "big_chance", "dangerous_spell", "momentum_shift",
     "period_end", "full_time"}
)  # fmt: skip

TEMPLATES: dict[str, dict[Lang, Lines]] = {
    "goal": {
        "en": (
            ("headline", (
                "{scorer} scores for {team} at {minute}: {score}.",
                "Goal for {team} at {minute}, {scorer} the scorer: {score}.",
                "{team} score through {scorer} at {minute}; it is {score}.",
            )),
            ("stat", (
                "The finish was a {shot_xg} xG chance.",
                "Chance quality: {shot_xg} xG.",
                "xG of the chance: {shot_xg}.",
            )),
            ("story", (
                "Shots in the match: {team} {shots_team}, {opp} {shots_opp}.",
                "xG in the match: {team} {xg_team}, {opp} {xg_opp}.",
                "Goals for {scorer} in this match: {scorer_goals}.",
            )),
        ),
        "vi": (
            ("headline", (
                "{scorer} ghi bàn cho {team} ở {minute}: {score}.",
                "Bàn thắng cho {team} ở {minute}, {scorer} lập công: {score}.",
                "{team} có bàn ở {minute} nhờ {scorer}; {score}.",
            )),
            ("stat", (
                "Cơ hội này có xG {shot_xg}.",
                "Chất lượng cơ hội: xG {shot_xg}.",
                "xG của cú dứt điểm: {shot_xg}.",
            )),
            ("story", (
                "Cú sút cả trận: {team} {shots_team}, {opp} {shots_opp}.",
                "xG cả trận: {team} {xg_team}, {opp} {xg_opp}.",
                "Số bàn của {scorer} trong trận: {scorer_goals}.",
            )),
        ),
    },
    "goal_overturned": {
        "en": (
            ("headline", (
                "VAR rules out the goal by {scorer} {reason}: still {score}.",
                "No goal for {scorer} after VAR {reason}; it stays {score}.",
                "VAR overturns the goal by {scorer} {reason}; the score is {score}.",
            )),
            ("story", (
                "The score stays {score}.",
                "It remains {score} after the review.",
                "No change on the scoreboard: {score}.",
            )),
        ),
        "vi": (
            ("headline", (
                "VAR huỷ bàn thắng của {scorer} {reason}: {score}.",
                "Bàn của {scorer} không được công nhận {reason}; {score}.",
                "VAR không công nhận bàn của {scorer} {reason}; tỷ số là {score}.",
            )),
            ("story", (
                "Tỷ số vẫn là {score}.",
                "Sau khi VAR xem lại, tỷ số giữ nguyên {score}.",
                "Bảng tỷ số không đổi: {score}.",
            )),
        ),
    },
    "red_card": {
        "en": (
            ("headline", (
                "{carded} ({team}) is sent off at {minute}; red cards for {team}: {red_team}.",
                "Red card for {carded} at {minute}: red cards for {team}, {red_team}.",
                "Red card at {minute}: {carded} of {team} goes off; {team} red cards: "
                "{red_team}.",
            )),
            ("stat", (
                "Possession in {last10}: {team} {poss_team}, {opp} {poss_opp}.",
                "{team} had {poss_team} of the ball in {last10}, {opp} {poss_opp}.",
                "Before the red card, possession in {last10}: {team} {poss_team}, "
                "{opp} {poss_opp}.",
            )),
            ("story", (
                "{team} now play a player down at {score}.",
                "A player down for {team}, with the score at {score}.",
                "It is {score}, and {team} now play with a man less.",
            )),
        ),
        "vi": (
            ("headline", (
                "{carded} ({team}) nhận thẻ đỏ ở {minute}; số thẻ đỏ của {team}: {red_team}.",
                "Thẻ đỏ cho {carded} ở {minute}, {team} có {red_team} thẻ đỏ.",
                "Thẻ đỏ ở {minute}: {carded} của {team} phải rời sân; số thẻ đỏ của {team}: "
                "{red_team}.",
            )),
            ("stat", (
                "Kiểm soát bóng {last10}: {team} {poss_team}, {opp} {poss_opp}.",
                "{team} cầm bóng {poss_team} trong {last10}, {opp} {poss_opp}.",
                "Trước thẻ đỏ, kiểm soát bóng {last10}: {team} {poss_team}, {opp} {poss_opp}.",
            )),
            ("story", (
                "{team} phải chơi thiếu người khi tỷ số là {score}.",
                "Tỷ số lúc {team} mất người: {score}.",
                "Tỷ số {score}, và {team} giờ phải chơi thiếu người.",
            )),
        ),
    },
    "big_chance": {
        "en": (
            ("headline", (
                "Big chance for {shooter} at {minute} ({shot_xg} xG): {fate}.",
                "{shooter} misses a {shot_xg} xG chance at {minute}: {fate}.",
                "{shooter} cannot convert a {shot_xg} xG chance at {minute}: {fate}.",
            )),
            ("stat", (
                "xG in the match: {team} {xg_team}, {opp} {xg_opp}.",
                "{team} have {xg_team} xG in the match, {opp} {xg_opp}.",
                "Expected goals in the match: {team} {xg_team}, {opp} {xg_opp}.",
            )),
            ("story", (
                "Shots in the match: {team} {shots_team}, {opp} {shots_opp}.",
                "{team} have {shots_team} shots in the match, {opp} {shots_opp}.",
                "Attempts in the match: {team} {shots_team}, {opp} {shots_opp}.",
            )),
        ),
        "vi": (
            ("headline", (
                "Cơ hội lớn cho {shooter} ở {minute} (xG {shot_xg}): {fate}.",
                "{shooter} bỏ lỡ cơ hội xG {shot_xg} ở {minute}: {fate}.",
                "{shooter} không tận dụng được cơ hội xG {shot_xg} ở {minute}: {fate}.",
            )),
            ("stat", (
                "xG cả trận: {team} {xg_team}, {opp} {xg_opp}.",
                "{team} có {xg_team} xG cả trận, {opp} {xg_opp}.",
                "Bàn thắng kỳ vọng tới lúc này: {team} {xg_team}, {opp} {xg_opp}.",
            )),
            ("story", (
                "Cú sút cả trận: {team} {shots_team}, {opp} {shots_opp}.",
                "{team} có {shots_team} cú sút cả trận, {opp} {shots_opp}.",
                "Số lần dứt điểm tới lúc này: {team} {shots_team}, {opp} {shots_opp}.",
            )),
        ),
    },
    "dangerous_spell": {
        "en": (
            ("headline", (
                "{team} turn up the pressure: {spell_shots} shots in this spell.",
                "{team} keep coming: {spell_shots} shots in a few minutes.",
                "{team} pile on the pressure with {spell_shots} shots in this spell.",
            )),
            ("headline", (
                "Pressure from {team}: {spell_entries} box entries in this spell.",
                "{team} keep arriving in the box: {spell_entries} entries in this spell.",
                "Wave after wave from {team}: {spell_entries} box entries in this spell.",
            )),
            ("stat", (
                "Box entries in {last10}: {team} {box_team}, {opp} {box_opp}.",
                "{team} have entered the box {box_team} times in {last10}, {opp} {box_opp}.",
                "In {last10}, box entries read {team} {box_team}, {opp} {box_opp}.",
            )),
            ("story", (
                "It is still {score}.",
                "The score is {score} for now.",
                "For now the scoreboard reads {score}.",
            )),
        ),
        "vi": (
            ("headline", (
                "{team} dồn ép với {spell_shots} cú sút trong pha tấn công này.",
                "{team} gây sức ép: {spell_shots} cú sút chỉ trong ít phút.",
                "{team} dồn lên với {spell_shots} cú sút trong pha ép sân này.",
            )),
            ("headline", (
                "{team} gây sức ép: {spell_entries} lần vào vòng cấm trong pha này.",
                "{team} liên tục vào vòng cấm: {spell_entries} lần trong pha này.",
                "{team} tấn công dồn dập: {spell_entries} lần vào vòng cấm trong pha này.",
            )),
            ("stat", (
                "Số lần vào vòng cấm {last10}: {team} {box_team}, {opp} {box_opp}.",
                "{team} vào vòng cấm {box_team} lần trong {last10}, {opp} {box_opp} lần.",
                "Trong {last10}, số lần vào vòng cấm: {team} {box_team}, {opp} {box_opp}.",
            )),
            ("story", (
                "Tỷ số vẫn là {score}.",
                "Lúc này tỷ số là {score}.",
                "Bảng tỷ số lúc này: {score}.",
            )),
        ),
    },
    "momentum_shift": {
        "en": (
            ("headline", (
                "Momentum swings to {team}: {momentum} over {last10}.",
                "{team} take the momentum, {momentum} in {last10}.",
                "The game turns towards {team}: momentum {momentum} in {last10}.",
            )),
            ("stat", (
                "Possession in {last10}: {team} {poss_team}, {opp} {poss_opp}.",
                "{team} have had {poss_team} of the ball in {last10}, {opp} {poss_opp}.",
                "Ball possession over {last10}: {team} {poss_team}, {opp} {poss_opp}.",
            )),
            ("story", (
                "Shots in {last10}: {team} {shots_team}, {opp} {shots_opp}.",
                "{team} have {shots_team} shots in {last10}, {opp} {shots_opp}.",
                "Attempts in {last10}: {team} {shots_team}, {opp} {shots_opp}.",
            )),
        ),
        "vi": (
            ("headline", (
                "Thế trận đổi chiều về {team}: momentum {momentum} trong {last10}.",
                "Momentum {last10} nghiêng về {team}: {momentum}.",
                "Trận đấu nghiêng về {team}: momentum {momentum} trong {last10}.",
            )),
            ("stat", (
                "Kiểm soát bóng {last10}: {team} {poss_team}, {opp} {poss_opp}.",
                "{team} cầm bóng {poss_team} trong {last10}, {opp} {poss_opp}.",
                "Tỷ lệ kiểm soát bóng trong {last10}: {team} {poss_team}, {opp} {poss_opp}.",
            )),
            ("story", (
                "Cú sút {last10}: {team} {shots_team}, {opp} {shots_opp}.",
                "{team} có {shots_team} cú sút trong {last10}, {opp} {shots_opp}.",
                "Số lần dứt điểm trong {last10}: {team} {shots_team}, {opp} {shots_opp}.",
            )),
        ),
    },
    "period_end": {
        "en": (
            ("headline", (
                "Half-time: {score}.",
                "The break arrives at {score}.",
                "Half-time, and it is {score}.",
            )),
            ("stat", (
                "First-half possession: {home} {poss_home}, {away} {poss_away}.",
                "Possession in the first half: {home} {poss_home}, {away} {poss_away}.",
                "{home} had {poss_home} of the ball before the break, {away} {poss_away}.",
            )),
            ("story", (
                "First-half xG: {home} {xg_home}, {away} {xg_away}.",
                "First-half shots: {home} {shots_home}, {away} {shots_away}.",
                "Shots on target in the first half: {home} {sot_home}, {away} {sot_away}.",
            )),
        ),
        "vi": (
            ("headline", (
                "Hết hiệp 1: {score}.",
                "Nghỉ giữa hiệp với tỷ số {score}.",
                "Hai đội vào giờ nghỉ, tỷ số {score}.",
            )),
            ("stat", (
                "Kiểm soát bóng hiệp 1: {home} {poss_home}, {away} {poss_away}.",
                "Tỷ lệ cầm bóng hiệp 1: {home} {poss_home}, {away} {poss_away}.",
                "{home} kiểm soát bóng {poss_home} trong hiệp 1, {away} {poss_away}.",
            )),
            ("story", (
                "xG hiệp 1: {home} {xg_home}, {away} {xg_away}.",
                "Cú sút hiệp 1: {home} {shots_home}, {away} {shots_away}.",
                "Sút trúng đích hiệp 1: {home} {sot_home}, {away} {sot_away}.",
            )),
        ),
    },
    "full_time": {
        "en": (
            ("headline", (
                "Full time: {score}.",
                "It ends {score}.",
                "The final whistle: {score}.",
            )),
            ("stat", (
                "Possession: {home} {poss_home}, {away} {poss_away}.",
                "Possession over the match: {home} {poss_home}, {away} {poss_away}.",
                "{home} had {poss_home} of the ball, {away} {poss_away}.",
            )),
            ("story", (
                "xG: {home} {xg_home}, {away} {xg_away}.",
                "Shots: {home} {shots_home}, {away} {shots_away}.",
                "Shots on target: {home} {sot_home}, {away} {sot_away}.",
            )),
        ),
        "vi": (
            ("headline", (
                "Hết trận: {score}.",
                "Trận đấu kết thúc với tỷ số {score}.",
                "Tiếng còi mãn cuộc: {score}.",
            )),
            ("stat", (
                "Kiểm soát bóng cả trận: {home} {poss_home}, {away} {poss_away}.",
                "Tỷ lệ cầm bóng cả trận: {home} {poss_home}, {away} {poss_away}.",
                "{home} kiểm soát bóng {poss_home}, {away} {poss_away}.",
            )),
            ("story", (
                "xG cả trận: {home} {xg_home}, {away} {xg_away}.",
                "Cú sút cả trận: {home} {shots_home}, {away} {shots_away}.",
                "Sút trúng đích: {home} {sot_home}, {away} {sot_away}.",
            )),
        ),
    },
}  # fmt: skip
SCORE_LINE: dict[Lang, str] = {"en": "Score: {score}.", "vi": "Tỷ số: {score}."}
MAX_LINES = 3
ANGLES: tuple[TalkingAngle, ...] = ("headline", "stat", "story")  # of model lines, by position


def wants_talking_point(trigger: Trigger) -> bool:
    return trigger.kind in TALKING_KINDS and trigger.importance >= 2


def render_lines(pack: FactPack, language: Lang) -> tuple[TalkingLine, ...]:
    """2-3 template lines of a talking point in one language."""
    values = pack_values(pack, language)
    lines: list[TalkingLine] = []
    seen_angles: set[str] = set()
    for angle, variants in TEMPLATES.get(pack.trigger.kind, {}).get(language, ()):
        if angle in seen_angles or len(lines) >= MAX_LINES:
            continue  # a second headline only replaces a first one that had no values
        text = pick(variants, f"{pack.trigger.id}:{angle}", values)  # same choice in vi and en
        if text is None:
            continue
        seen_angles.add(angle)
        lines.append(TalkingLine(text=text, angle=angle, fact_keys=cited_keys(text, pack.facts)))
    if len(lines) < 2:
        text = fill(SCORE_LINE[language], values)
        if all(line.text != text for line in lines):
            lines.append(
                TalkingLine(text=text, angle="story", fact_keys=cited_keys(text, pack.facts))
            )
    return tuple(lines[:MAX_LINES])


def line_from_text(text: str, angle: TalkingAngle, pack: FactPack) -> TalkingLine:
    return TalkingLine(text=text, angle=angle, fact_keys=cited_keys(text, pack.facts))
