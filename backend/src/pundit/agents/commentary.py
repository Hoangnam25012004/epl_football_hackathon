"""Commentary templates (docs/day3-design.md section 8): every trigger in four versions,
Vietnamese and English, analyst and casual.

Analyst lines cite at least one verified number when the pack has one; casual lines use at most
one number and no jargon. Several phrasings per case, chosen by `crc32(trigger.id)`; a later
tier is used only when no phrasing of an earlier tier has all its values. The same templates
are the mock LLM and the fallback of the real one (principle L7).
"""

from collections.abc import Sequence
from typing import Literal

from pundit.agents.contracts import CommentaryText, FactPack, Lang, Register, Trigger
from pundit.agents.phrasebank import pick
from pundit.agents.studio_values import pack_values

Tiers = tuple[tuple[str, ...], ...]
Bank = dict[tuple[Lang, Register], Tiers]

BANKS: dict[str, Bank] = {
    "goal": {
        ("vi", "analyst"): (
            (
                "{minute}, {scorer} ghi bàn cho {team} với cú dứt điểm xG {shot_xg}. Tỷ số: "
                "{score}.",
                "Bàn thắng của {scorer} ở {minute}, cơ hội có xG {shot_xg}. {score}.",
                "{scorer} lập công cho {team} ở {minute} (xG {shot_xg}). Tỷ số hiện tại: {score}.",
            ),
            ("{minute}, {team} ghi bàn. Tỷ số: {score}.",),
        ),
        ("vi", "casual"): (
            (
                "VÀOOO! {scorer} ghi bàn cho {team}! {score}.",
                "Bàn thắng! {scorer} đưa bóng vào lưới, {score}.",
                "{scorer} lập công cho {team}! {score}.",
            ),
            ("VÀOOO! {team} ghi bàn! {score}.",),
        ),
        ("en", "analyst"): (
            (
                "{scorer} scores for {team} at {minute} with a {shot_xg} xG chance. {score}.",
                "Goal for {team}: {scorer} finishes a {shot_xg} xG chance at {minute}. {score}.",
                "{minute}: {scorer} scores for {team} (xG {shot_xg}). It is now {score}.",
            ),
            ("{team} score at {minute}. {score}.",),
        ),
        ("en", "casual"): (
            (
                "GOAL! {scorer} scores for {team}! {score}.",
                "{scorer} scores! It is {score}.",
                "What a moment: {scorer} finds the net for {team}! {score}.",
            ),
            ("GOAL for {team}! {score}.",),
        ),
    },
    "goal_penalty": {
        ("vi", "analyst"): (
            (
                "{minute}, {scorer} thực hiện thành công quả phạt đền cho {team} "
                "(xG {shot_xg}). {score}.",
                "Phạt đền thành công! {scorer} ghi bàn cho {team} ở {minute}, xG {shot_xg}. "
                "{score}.",
                "{scorer} lập công từ chấm phạt đền ở {minute} (xG {shot_xg}). Tỷ số: {score}.",
            ),
        ),
        ("vi", "casual"): (
            (
                "VÀOOO! {scorer} sút phạt đền thành công! {score}.",
                "{scorer} không bỏ lỡ từ chấm phạt đền! {score}.",
                "Phạt đền thành công cho {team}! {score}.",
            ),
        ),
        ("en", "analyst"): (
            (
                "{scorer} scores the penalty for {team} at {minute} (xG {shot_xg}). {score}.",
                "Penalty scored: {scorer} for {team} at {minute}, a {shot_xg} xG chance. {score}.",
                "{minute}: {scorer} converts from the spot (xG {shot_xg}). It is {score}.",
            ),
        ),
        ("en", "casual"): (
            (
                "GOAL! {scorer} converts the penalty! {score}.",
                "{scorer} makes no mistake from the spot! {score}.",
                "Penalty scored for {team}! {score}.",
            ),
        ),
    },
    "own_goal": {
        ("vi", "analyst"): (
            (
                "{minute}: phản lưới nhà, bàn thắng được tính cho {team}. {score}.",
                "{minute}, một pha phản lưới nhà mang về bàn thắng cho {team}. Tỷ số: {score}.",
                "Bàn phản lưới nhà ở {minute}, {team} được hưởng lợi. {score}.",
            ),
        ),
        ("vi", "casual"): (
            (
                "Phản lưới nhà! {score}.",
                "Bàn phản lưới nhà! Tỷ số giờ là {score}.",
                "Một pha phản lưới nhà! {score}.",
            ),
        ),
        ("en", "analyst"): (
            (
                "{minute}: an own goal, credited to {team}. {score}.",
                "{minute}: an own goal hands {team} a goal. It is {score}.",
                "Own goal at {minute}, and {team} benefit. {score}.",
            ),
        ),
        ("en", "casual"): (
            (
                "Own goal! {score}.",
                "An own goal! It is {score}.",
                "Own goal! The score is now {score}.",
            ),
        ),
    },
    "goal_overturned": {
        ("vi", "analyst"): (
            (
                "{minute}: VAR không công nhận bàn thắng của {scorer} {reason}. "
                "Tỷ số vẫn là {score}.",
                "Bàn thắng của {scorer} bị huỷ {reason} sau khi VAR kiểm tra. {score}.",
                "VAR vào cuộc ở {minute}, bàn của {scorer} bị huỷ {reason}. Tỷ số: {score}.",
            ),
            ("{minute}: VAR không công nhận bàn thắng. Tỷ số: {score}.",),
        ),
        ("vi", "casual"): (
            (
                "Ôi không! VAR huỷ bàn thắng của {scorer} {reason}.",
                "VAR lên tiếng: bàn của {scorer} không được tính!",
                "Bàn thắng bị huỷ! {scorer} mừng hụt.",
            ),
            ("VAR không công nhận bàn thắng!",),
        ),
        ("en", "analyst"): (
            (
                "{minute}: VAR rules out the goal by {scorer} {reason}. Still {score}.",
                "The goal by {scorer} is disallowed {reason} after a VAR check. {score}.",
                "VAR overturns the goal by {scorer} {reason}; the score stays {score}.",
            ),
            ("{minute}: VAR rules the goal out. {score}.",),
        ),
        ("en", "casual"): (
            (
                "No goal! VAR rules out the strike by {scorer} {reason}.",
                "VAR says no: the goal by {scorer} does not count!",
                "Heartbreak for {scorer}: the goal is ruled out.",
            ),
            ("VAR says no goal!",),
        ),
    },
    "red_card": {
        ("vi", "analyst"): (
            (
                "{minute}: {carded} ({team}) nhận thẻ đỏ; {team} có {red_team} thẻ đỏ trong trận.",
                "Thẻ đỏ cho {carded} ở {minute}. {team} có {red_team} thẻ đỏ và phải chơi thiếu "
                "người.",
                "{carded} bị truất quyền thi đấu ở {minute}; số thẻ đỏ của {team}: {red_team}.",
            ),
        ),
        ("vi", "casual"): (
            (
                "Thẻ đỏ! {carded} phải rời sân, {team} chơi thiếu người.",
                "{carded} bị đuổi khỏi sân!",
                "Trọng tài rút thẻ đỏ với {carded}!",
            ),
        ),
        ("en", "analyst"): (
            (
                "{minute}: {carded} ({team}) is sent off; red cards for {team}: {red_team}.",
                "Red card for {carded} at {minute}. Red cards for {team}: {red_team}.",
                "Sent off: {carded} ({team}) at {minute}. Red cards for {team}: {red_team}.",
            ),
        ),
        ("en", "casual"): (
            (
                "Red card! {carded} is off and {team} are a player down.",
                "{carded} is sent off!",
                "Off goes {carded}: a red card!",
            ),
        ),
    },
    "yellow_card": {
        ("vi", "analyst"): (
            (
                "{minute}: thẻ vàng cho {carded} ({team}); {team} đã có {yellow_team} thẻ vàng.",
                "{carded} nhận thẻ vàng ở {minute}, sau {fouls_player} lần phạm lỗi trong trận.",
                "Thẻ vàng: {carded}, {minute}. Số thẻ vàng của {team}: {yellow_team}.",
            ),
        ),
        ("vi", "casual"): (
            (
                "Thẻ vàng cho {carded}!",
                "{carded} bị cảnh cáo bằng thẻ vàng.",
                "Trọng tài rút thẻ vàng với {carded}.",
            ),
        ),
        ("en", "analyst"): (
            (
                "{minute}: yellow card for {carded} ({team}); yellow cards for {team}: "
                "{yellow_team}.",
                "{carded} is booked at {minute}; fouls by {carded} in the match: {fouls_player}.",
                "Booking for {carded} at {minute}. Yellow cards for {team}: {yellow_team}.",
            ),
        ),
        ("en", "casual"): (
            (
                "Yellow card for {carded}!",
                "{carded} goes into the referee's book.",
                "A booking for {carded}.",
            ),
        ),
    },
    "big_chance": {
        ("vi", "analyst"): (
            (
                "{minute}: cơ hội lớn cho {shooter} (xG {shot_xg}) nhưng {fate}.",
                "{shooter} có cơ hội rõ ràng với xG {shot_xg}, nhưng {fate}.",
                "Cơ hội ngon ăn ở {minute}: {shooter} dứt điểm (xG {shot_xg}), {fate}.",
            ),
        ),
        ("vi", "casual"): (
            (
                "Suýt nữa! {shooter} có cơ hội rất rõ nhưng {fate}.",
                "Ôi! {shooter} bỏ lỡ cơ hội ngon ăn, {fate}.",
                "{shooter} dứt điểm, {fate}! Tiếc quá.",
            ),
        ),
        ("en", "analyst"): (
            (
                "{minute}: big chance for {shooter} ({shot_xg} xG), but {fate}.",
                "{shooter} has a clear chance worth {shot_xg} xG, but {fate}.",
                "Clear chance at {minute}: {shooter} shoots ({shot_xg} xG) and {fate}.",
            ),
        ),
        ("en", "casual"): (
            (
                "So close! {shooter} had a great chance but {fate}.",
                "Chance! {shooter} shoots and {fate}.",
                "What a chance for {shooter}, but {fate}!",
            ),
        ),
    },
    "rocket_shot": {
        ("vi", "analyst"): (
            (
                "{minute}: {shooter} tung cú sút {shot_speed}, {fate}.",
                "Cú sút của {shooter} đạt tốc độ {shot_speed}; {fate}.",
                "{shooter} sút căng {shot_speed} ở {minute}, {fate}.",
            ),
        ),
        ("vi", "casual"): (
            (
                "Sút cực mạnh! {shooter} dứt điểm, {fate}.",
                "{shooter} nã đại bác, {fate}!",
                "Cú sút rất mạnh của {shooter}, {fate}.",
            ),
        ),
        ("en", "analyst"): (
            (
                "{minute}: {shooter} hits it at {shot_speed}, and {fate}.",
                "A {shot_speed} strike from {shooter}; {fate}.",
                "{shooter} unleashes a {shot_speed} shot at {minute}, and {fate}.",
            ),
        ),
        ("en", "casual"): (
            (
                "Rocket from {shooter}, and {fate}!",
                "{shooter} lets fly and {fate}!",
                "Huge strike by {shooter}, and {fate}.",
            ),
        ),
    },
    "shot": {
        ("vi", "analyst"): (
            (
                "{minute}: {shooter} dứt điểm (xG {shot_xg}), {fate}.",
                "{shooter} thử vận may với cú sút xG {shot_xg}: {fate}.",
                "Cú sút của {shooter} (xG {shot_xg}), {fate}.",
            ),
        ),
        ("vi", "casual"): (
            (
                "{shooter} dứt điểm, {fate}.",
                "Cú sút của {shooter}, {fate}.",
                "{shooter} thử sút, {fate}.",
            ),
        ),
        ("en", "analyst"): (
            (
                "{minute}: {shooter} shoots ({shot_xg} xG) and {fate}.",
                "Effort from {shooter} worth {shot_xg} xG: {fate}.",
                "{shooter} tries a shot ({shot_xg} xG) and {fate}.",
            ),
        ),
        ("en", "casual"): (
            (
                "{shooter} shoots and {fate}.",
                "Effort from {shooter}, and {fate}.",
                "{shooter} has a go, and {fate}.",
            ),
        ),
    },
    "shot_penalty": {
        ("vi", "analyst"): (
            (
                "{minute}: {shooter} thực hiện quả phạt đền (xG {shot_xg}), {fate}.",
                "Phạt đền cho {team} ở {minute}: {shooter} sút (xG {shot_xg}), {fate}.",
                "{shooter} đối mặt thủ môn từ chấm phạt đền ở {minute} (xG {shot_xg}): {fate}.",
            ),
        ),
        ("vi", "casual"): (
            (
                "Phạt đền! {shooter} sút, {fate}.",
                "{shooter} sút phạt đền, {fate}!",
                "Cơ hội từ chấm phạt đền cho {shooter}, nhưng {fate}.",
            ),
        ),
        ("en", "analyst"): (
            (
                "{minute}: {shooter} takes the penalty ({shot_xg} xG) and {fate}.",
                "Penalty for {team} at {minute}: {shooter} shoots ({shot_xg} xG) and {fate}.",
                "{minute}: {shooter} faces the keeper from the spot ({shot_xg} xG), and {fate}.",
            ),
        ),
        ("en", "casual"): (
            (
                "Penalty! {shooter} shoots and {fate}.",
                "{shooter} steps up for the penalty, and {fate}!",
                "A penalty for {shooter}, but {fate}.",
            ),
        ),
    },
    "dangerous_spell": {
        ("vi", "analyst"): (
            (
                "{team} dồn ép với {spell_shots} cú sút trong pha tấn công này.",
                "Áp lực liên tục từ {team}: {spell_shots} cú sút liên tiếp.",
                "{team} ép sân, {spell_shots} cú sút chỉ trong ít phút.",
            ),
            (
                "{team} dồn ép với {spell_entries} lần đưa bóng vào vòng cấm.",
                "{team} liên tục ép sân: {spell_entries} lần vào vòng cấm.",
            ),
        ),
        ("vi", "casual"): (
            (
                "{team} tấn công liên tục!",
                "Sóng gió trước khung thành {opp}!",
                "{team} đang tạo sức ép lớn lên khung thành {opp}.",
            ),
        ),
        ("en", "analyst"): (
            (
                "{team} pile on the pressure: {spell_shots} shots in this spell.",
                "Sustained pressure from {team}, {spell_shots} shots in a few minutes.",
                "{team} keep coming: {spell_shots} shots in quick succession.",
            ),
            (
                "{team} keep coming: {spell_entries} entries into the box.",
                "Pressure from {team}, with {spell_entries} box entries in this spell.",
            ),
        ),
        ("en", "casual"): (
            (
                "{team} keep attacking!",
                "Wave after wave from {team}!",
                "{team} are knocking on the door!",
            ),
        ),
    },
    "momentum_shift": {
        ("vi", "analyst"): (
            (
                "Thế trận đổi chiều: momentum {last10} của {team} là {momentum}.",
                "{team} vùng lên, momentum {momentum} và kiểm soát bóng {poss_team} trong "
                "{last10}.",
                "Thế trận nghiêng về {team}: {shots_team} cú sút trong {last10}.",
            ),
            ("Thế trận đổi chiều: momentum {last10} của {team} là {momentum}.",),
        ),
        ("vi", "casual"): (
            (
                "Gió đã đổi chiều, {team} đang lên!",
                "{team} chơi đầy hưng phấn ở những phút này!",
                "{team} đang dần lấy lại thế trận!",
            ),
        ),
        ("en", "analyst"): (
            (
                "Momentum swing: {team} at {momentum} over {last10}.",
                "{team} take over, momentum {momentum} with {poss_team} possession in {last10}.",
                "The game tilts towards {team}: {shots_team} shots in {last10}.",
            ),
            ("Momentum swing: {team} at {momentum} over {last10}.",),
        ),
        ("en", "casual"): (
            (
                "{team} are on the rise!",
                "The tide is turning towards {team}!",
                "{team} have found their rhythm!",
            ),
        ),
    },
    "substitution": {
        ("vi", "analyst"): (
            (
                "{minute}: {team} thay người, {player_in} vào thay {player_out}.",
                "Thay người bên phía {team}: {player_out} rời sân, {player_in} vào sân.",
                "{team} điều chỉnh nhân sự ở {minute}: {player_in} thay {player_out}.",
            ),
        ),
        ("vi", "casual"): (
            (
                "{player_in} vào sân thay {player_out}.",
                "Thay người: {player_out} ra, {player_in} vào.",
                "{team} tung {player_in} vào sân.",
            ),
        ),
        ("en", "analyst"): (
            (
                "{minute}: change for {team}, {player_in} replaces {player_out}.",
                "Substitution for {team}: {player_out} off, {player_in} on.",
                "{team} make a change at {minute}: {player_in} for {player_out}.",
            ),
        ),
        ("en", "casual"): (
            (
                "{player_in} comes on for {player_out}.",
                "Fresh legs: {player_in} replaces {player_out}.",
                "{team} bring on {player_in}.",
            ),
        ),
    },
    "period_start_1": {
        ("vi", "analyst"): (
            (
                "Trận đấu bắt đầu: {home} gặp {away}.",
                "Bóng lăn! {home} tiếp đón {away}.",
                "Tiếng còi khai cuộc vang lên, {home} đối đầu {away}.",
            ),
        ),
        ("vi", "casual"): (
            (
                "Trận đấu bắt đầu rồi! {home} gặp {away}.",
                "Bóng đã lăn! {home} đấu với {away}.",
                "Khai cuộc! {home} tiếp đón {away}.",
            ),
        ),
        ("en", "analyst"): (
            (
                "Kick-off: {home} against {away}.",
                "We are under way, {home} v {away}.",
                "The referee gets {home} against {away} started.",
            ),
        ),
        ("en", "casual"): (
            (
                "Kick-off! {home} against {away}.",
                "Here we go: {home} v {away}!",
                "We are off: {home} against {away}!",
            ),
        ),
    },
    "period_start_2": {
        ("vi", "analyst"): (
            (
                "Hiệp 2 bắt đầu, {score}.",
                "Bóng lăn trở lại cho hiệp 2. Tỷ số: {score}.",
                "Hai đội bước vào hiệp 2 với tỷ số {score}.",
            ),
        ),
        ("vi", "casual"): (
            ("Hiệp 2 bắt đầu! {score}.", "Bóng lăn trở lại, {score}.", "Hiệp 2 đã bắt đầu."),
        ),
        ("en", "analyst"): (
            (
                "The second half is under way, {score}.",
                "Back under way for the second half: {score}.",
                "The second half begins with {score}.",
            ),
        ),
        ("en", "casual"): (
            (
                "Second half! {score}.",
                "We are back under way: {score}.",
                "The second half has begun!",
            ),
        ),
    },
    "period_end": {
        ("vi", "analyst"): (
            (
                "Hết hiệp 1: {score}. {home} kiểm soát bóng {poss_home}, dứt điểm {shots_home} "
                "lần; {away} có {poss_away} và {shots_away} cú sút.",
                "Nghỉ giữa hiệp với tỷ số {score}. xG: {home_s} {xg_home}, {away_s} {xg_away}.",
                "Hiệp 1 khép lại, {score}. {home} có {shots_home} cú sút, {away} có {shots_away}.",
            ),
            ("Hết hiệp 1: {score}.",),
        ),
        ("vi", "casual"): (
            (
                "Hết hiệp 1! {score}.",
                "Hai đội vào nghỉ giữa hiệp, {score}.",
                "Nghỉ giữa hiệp: {score}.",
            ),
        ),
        ("en", "analyst"): (
            (
                "Half-time: {score}. {home} had {poss_home} possession and {shots_home} shots; "
                "{away} {poss_away} and {shots_away}.",
                "Half-time, {score}. xG: {home_s} {xg_home}, {away_s} {xg_away}.",
                "The break arrives with {score}. Shots: {home_s} {shots_home}, "
                "{away_s} {shots_away}.",
            ),
            ("Half-time: {score}.",),
        ),
        ("en", "casual"): (
            (
                "Half-time! {score}.",
                "That is the first half: {score}.",
                "Time for the break: {score}.",
            ),
        ),
    },
    "full_time": {
        ("vi", "analyst"): (
            (
                "Hết trận: {score}. {home} kiểm soát bóng {poss_home}, dứt điểm {shots_home} lần; "
                "{away} có {poss_away} và {shots_away} cú sút.",
                "Trận đấu kết thúc, {score}. xG: {home_s} {xg_home}, {away_s} {xg_away}.",
                "Tiếng còi mãn cuộc: {score}. {home} có {shots_home} cú sút, {away} có "
                "{shots_away}.",
            ),
            ("Hết trận: {score}.",),
        ),
        ("vi", "casual"): (
            (
                "Hết trận! {score}.",
                "Trận đấu khép lại với tỷ số {score}.",
                "Kết thúc! {score}.",
            ),
        ),
        ("en", "analyst"): (
            (
                "Full time: {score}. {home} had {poss_home} possession and {shots_home} shots; "
                "{away} {poss_away} and {shots_away}.",
                "It ends {score}. xG: {home_s} {xg_home}, {away_s} {xg_away}.",
                "Final whistle: {score}. Shots: {home_s} {shots_home}, {away_s} {shots_away}.",
            ),
            ("Full time: {score}.",),
        ),
        ("en", "casual"): (
            ("Full time! {score}.", "That is it: {score}.", "It is all over: {score}."),
        ),
    },
    "corner": {
        ("vi", "analyst"): (
            (
                "{minute}: phạt góc cho {team}; {team} đã có {corners_team} quả phạt góc.",
                "{taker} chuẩn bị đá phạt góc, {team} có {corners_team} quả phạt góc trong trận.",
                "Phạt góc của {team} ({corners_team} quả trong trận).",
            ),
        ),
        ("vi", "casual"): (
            (
                "Phạt góc cho {team}!",
                "{team} được hưởng phạt góc.",
                "{taker} chuẩn bị đá phạt góc.",
            ),
        ),
        ("en", "analyst"): (
            (
                "{minute}: corner to {team}; corners for {team}: {corners_team}.",
                "{taker} to take a corner for {team} (corners: {corners_team}).",
                "Corner for {team}, {corners_team} in the match so far.",
            ),
        ),
        ("en", "casual"): (
            ("Corner for {team}!", "{team} win a corner.", "{taker} will take the corner."),
        ),
    },
    "offside": {
        ("vi", "analyst"): (
            (
                "{minute}: {offside_player} việt vị; {team} đã việt vị {offsides_team} lần.",
                "Cờ việt vị được phất lên với {offside_player} ({team}: {offsides_team} lần "
                "việt vị).",
                "{offside_player} rơi vào thế việt vị, {team} có {offsides_team} lần việt vị "
                "trong trận.",
            ),
        ),
        ("vi", "casual"): (
            (
                "Việt vị! {offside_player} đứng sai vị trí.",
                "Trợ lý trọng tài phất cờ việt vị với {offside_player}.",
                "{offside_player} bị bắt việt vị.",
            ),
        ),
        ("en", "analyst"): (
            (
                "{minute}: {offside_player} is caught offside; offsides for {team}: "
                "{offsides_team}.",
                "Flag up for {offside_player} ({team} offsides: {offsides_team}).",
                "Offside given against {offside_player}; offsides for {team} in the match: "
                "{offsides_team}.",
            ),
        ),
        ("en", "casual"): (
            (
                "Offside! {offside_player} went too early.",
                "The flag goes up: {offside_player} is offside.",
                "{offside_player} is caught offside.",
            ),
        ),
    },
    "var_review_stands": {
        ("vi", "analyst"): (
            (
                "{minute}: VAR kiểm tra {check} và giữ nguyên quyết định.",
                "VAR xem lại {check} ở {minute}: quyết định được giữ nguyên.",
                "VAR kiểm tra {check} ở {minute}, trọng tài giữ nguyên quyết định.",
            ),
        ),
        ("vi", "casual"): (
            (
                "VAR kiểm tra xong, quyết định giữ nguyên.",
                "Không có gì thay đổi sau khi VAR xem lại.",
                "VAR đã xem lại, mọi thứ vẫn như cũ.",
            ),
        ),
        ("en", "analyst"): (
            (
                "{minute}: VAR checks the {check} and the decision stands.",
                "VAR review of the {check} at {minute}: the decision stands.",
                "{minute}: the VAR check on the {check} is complete and the call stands.",
            ),
        ),
        ("en", "casual"): (
            (
                "VAR has a look, and the decision stands.",
                "No change after the VAR check.",
                "VAR checked it: no change.",
            ),
        ),
    },
    "var_review_overturned": {
        ("vi", "analyst"): (
            (
                "{minute}: sau khi VAR kiểm tra {check}, trọng tài thay đổi quyết định.",
                "VAR vào cuộc ở {minute}, quyết định về {check} được thay đổi.",
                "Trọng tài đổi quyết định về {check} ở {minute} sau khi VAR can thiệp.",
            ),
        ),
        ("vi", "casual"): (
            (
                "VAR vào cuộc, trọng tài đổi quyết định!",
                "Quyết định thay đổi sau khi VAR xem lại!",
                "VAR can thiệp, quyết định bị đảo ngược!",
            ),
        ),
        ("en", "analyst"): (
            (
                "{minute}: after a VAR check of the {check}, the referee changes the decision.",
                "VAR steps in at {minute}: the {check} decision is changed.",
                "{minute}: the referee reverses the {check} decision after VAR.",
            ),
        ),
        ("en", "casual"): (
            (
                "VAR steps in and the decision changes!",
                "The decision is changed after a VAR check!",
                "VAR changes the call!",
            ),
        ),
    },
    "added_time": {
        ("vi", "analyst"): (
            (
                "Trận đấu bước vào thời gian bù giờ của {half}, {score}.",
                "Bù giờ {half}: {score}.",
                "Những phút bù giờ của {half} bắt đầu, tỷ số {score}.",
            ),
        ),
        ("vi", "casual"): (
            (
                "Những phút cuối cùng của {half}, {score}.",
                "Bù giờ rồi! {score}.",
                "Đã sang phút bù giờ của {half}!",
            ),
        ),
        ("en", "analyst"): (
            (
                "Added time in the {half}, {score}.",
                "We are into added time in the {half}: {score}.",
                "Added time begins in the {half} with {score}.",
            ),
        ),
        ("en", "casual"): (
            (
                "Into added time! {score}.",
                "Added time now, {score}.",
                "Just added time left in the {half}!",
            ),
        ),
    },
    "stat_tick": {
        ("vi", "analyst"): (
            (
                "Sau {tick} phút: {home} kiểm soát bóng {poss_home}, {shots_home} cú sút, xG "
                "{xg_home}; {away} có {poss_away}, {shots_away} cú sút, xG {xg_away}.",
                "Sau {tick} phút, kiểm soát bóng {home_s} {poss_home}, {away_s} {poss_away}; "
                "chỉ số hỗn loạn {chaos}.",
                "Phút {tick}: {home} có {shots_home} cú sút (xG {xg_home}), {away} có "
                "{shots_away} (xG {xg_away}).",
            ),
            ("Sau {tick} phút, {score}.",),
        ),
        ("vi", "casual"): (
            (
                "Sau {tick} phút, {home} cầm bóng {poss_home}.",
                "{tick} phút đã trôi qua, {score}.",
                "Đã {tick} phút, {home} giữ bóng {poss_home}.",
            ),
            ("{tick} phút đã trôi qua, {score}.",),
        ),
        ("en", "analyst"): (
            (
                "After {tick} minutes: {home} {poss_home} possession, {shots_home} shots, xG "
                "{xg_home}; {away} {poss_away}, {shots_away} shots, xG {xg_away}.",
                "{tick} minutes in: possession {home_s} {poss_home}, {away_s} {poss_away}; "
                "chaos index {chaos}.",
                "Minute {tick}: {home} have {shots_home} shots (xG {xg_home}), {away} "
                "{shots_away} (xG {xg_away}).",
            ),
            ("After {tick} minutes, {score}.",),
        ),
        ("en", "casual"): (
            (
                "After {tick} minutes, {home} have {poss_home} of the ball.",
                "{tick} minutes gone: {score}.",
                "{tick} minutes played, {home} with {poss_home} possession.",
            ),
            ("{tick} minutes gone: {score}.",),
        ),
    },
    "lull": {
        ("vi", "analyst"): (
            (
                "Trong {last5}, kiểm soát bóng {home_s} {poss_home}, {away_s} {poss_away}.",
                "Trận đấu chậm lại, {home} cầm bóng {poss_home} trong {last5}.",
                "Nhịp trận chùng xuống: {home} chuyền {passes_home} lần, {away} {passes_away} lần "
                "trong {last5}.",
            ),
            ("Trận đấu đang tạm lắng ở {minute}.",),
        ),
        ("vi", "casual"): (
            (
                "Trận đấu đang tạm lắng.",
                "Hai đội đang thăm dò nhau.",
                "{home} kiên nhẫn cầm bóng ở những phút này.",
            ),
        ),
        ("en", "analyst"): (
            (
                "Over {last5}, possession {home_s} {poss_home}, {away_s} {poss_away}.",
                "A quieter spell: {home} have had {poss_home} of the ball over {last5}.",
                "The game slows down: {home} {passes_home} passes, {away} {passes_away} in "
                "{last5}.",
            ),
            ("A quiet spell in the game at {minute}.",),
        ),
        ("en", "casual"): (
            (
                "A quiet spell in the game.",
                "Both teams are feeling each other out.",
                "{home} are keeping the ball patiently.",
            ),
        ),
    },
}
FALLBACK: dict[tuple[Lang, Register], str] = {
    ("vi", "analyst"): "Trận đấu tiếp tục ở {minute}.",
    ("vi", "casual"): "Trận đấu tiếp tục.",
    ("en", "analyst"): "Play goes on at {minute}.",
    ("en", "casual"): "Play goes on.",
}
VERSIONS: tuple[tuple[Lang, Register], ...] = (
    ("vi", "analyst"),
    ("vi", "casual"),
    ("en", "analyst"),
    ("en", "casual"),
)
SPOKEN_KINDS = frozenset({"stat_tick", "added_time", "period_start", "period_end", "full_time"})


def bank_key(trigger: Trigger) -> str:
    kind = trigger.kind
    if kind == "goal":
        if trigger.details.get("own_goal") == "true":
            return "own_goal"
        return "goal_penalty" if trigger.details.get("situation") == "penalty" else "goal"
    if kind in ("shot", "big_chance") and trigger.details.get("situation") == "penalty":
        return "shot_penalty"
    if kind == "period_start":
        return f"period_start_{trigger.period}"
    if kind == "var_review":
        return f"var_review_{trigger.outcome or 'stands'}"
    return kind


def render_version(pack: FactPack, language: Lang, mode: Register) -> str:
    """One version of the commentary of a pack (template)."""
    values = pack_values(pack, language)
    key = f"{pack.trigger.id}:{mode}"  # the same phrasing in both languages: the same numbers
    tiers: Sequence[Sequence[str]] = BANKS.get(bank_key(pack.trigger), {}).get((language, mode), ())
    for tier in tiers:
        text = pick(tier, key, values)
        if text is not None:
            return text
    return FALLBACK[(language, mode)].format(**values)


def render_texts(pack: FactPack) -> tuple[CommentaryText, ...]:
    return tuple(
        CommentaryText(
            language=language,
            mode=mode,
            text=render_version(pack, language, mode),
            source="template",
        )
        for language, mode in VERSIONS
    )


Excitement = Literal[0, 1, 2, 3]
EXCITEMENT: dict[str, Excitement] = {
    "goal_overturned": 3,
    "red_card": 2,
    "big_chance": 2,
    "rocket_shot": 1,
    "momentum_shift": 1,
    "dangerous_spell": 1,
}


def excitement(pack: FactPack) -> Excitement:
    """How loud the line is read (Day 5 SSML): a goal in a tight or late game is the top."""
    trigger = pack.trigger
    if trigger.kind == "goal":
        home, away = pack.match.teams[0].id, pack.match.teams[1].id
        tight = abs(pack.match.score.get(home, 0) - pack.match.score.get(away, 0)) <= 1
        late = trigger.period == 2 and trigger.clock_ms >= 35 * 60_000  # from the 80th minute
        return 3 if tight or late else 2
    return EXCITEMENT.get(trigger.kind, 0)


def speak(pack: FactPack) -> bool:
    return pack.trigger.importance >= 2 or pack.trigger.kind in SPOKEN_KINDS
