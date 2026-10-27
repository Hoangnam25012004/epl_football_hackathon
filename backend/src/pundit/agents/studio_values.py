"""Values of a FactPack for the studio templates: names, minutes and the displays of its facts.

Every number a template writes comes from here, and here only from `Fact.display` (principle
S2): a template cannot compute or round a number on its own.
"""

from pundit.agents.contracts import FactPack, Lang
from pundit.agents.studio_text import (
    find_fact,
    find_key,
    home_away,
    minute_vi,
    player_name,
    score_line,
    team_name,
    team_short,
)
from pundit.agents.wording import window_label
from pundit.domain.matchtime import RollingWindow

# metric -> short placeholder name ("poss_home", "shots_team", ...)
SHORT: dict[str, str] = {
    "possession": "poss",
    "shots": "shots",
    "xg": "xg",
    "corners": "corners",
    "offsides": "offsides",
    "shots_on_target": "sot",
    "box_entries": "box",
    "passes": "passes",
    "tempo": "tempo",
    "red_cards": "red",
    "yellow_cards": "yellow",
    "tackles": "tackles",
    "fouls": "fouls",
}
VAR_REASON: dict[str, tuple[str, str]] = {
    "offside": ("vì lỗi việt vị", "for offside"),
    "foul": ("vì lỗi trong tình huống trước đó", "for a foul in the build-up"),
    "handball": ("vì lỗi chạm tay", "for handball"),
    "none": ("sau khi xem lại", "after the review"),
}
VAR_CHECK: dict[str, tuple[str, str]] = {
    "goal": ("bàn thắng", "goal"),
    "penalty": ("quả phạt đền", "penalty"),
    "red_card": ("thẻ đỏ", "red card"),
}


def _fate(outcome: str | None, keeper: str | None, lang: Lang) -> str | None:
    """What happened to a shot, in words that agree with the Guardrail's outcome check."""
    vi = lang == "vi"
    match outcome:
        case "saved":
            if keeper:
                return f"thủ môn {keeper} cứu thua" if vi else f"{keeper} saves"
            return "thủ môn cứu thua" if vi else "the goalkeeper saves"
        case "blocked":
            return "cú sút bị chặn" if vi else "it is blocked"
        case "off_target":
            return "bóng đi chệch khung thành" if vi else "it goes wide"
        case "post":
            return "bóng dội cột" if vi else "it hits the post"
        case "goal":
            return "bóng đi thẳng vào lưới" if vi else "it flies into the net"
    return None


def pack_values(pack: FactPack, lang: Lang) -> dict[str, str]:
    match, trigger, facts = pack.match, pack.trigger, pack.facts
    home, away = home_away(match)
    vi = lang == "vi"
    values: dict[str, str] = {
        "minute": minute_vi(trigger.display_minute) if vi else trigger.display_minute,
        "home": team_name(match, home),
        "away": team_name(match, away),
        "home_s": team_short(match, home),
        "away_s": team_short(match, away),
        "score": score_line(match, lang),
        "half": (
            f"hiệp {trigger.period}" if vi else ("first half", "second half")[trigger.period - 1]
        )
        if trigger.period in (1, 2)
        else "",
    }
    for minutes in (5, 10, 15):  # "10 phút gần nhất", shorter early in the match
        window = RollingWindow.last(minutes, trigger.match_ms)
        values[f"last{minutes}"] = window_label(window, lang)
    sides = {"home": home, "away": away}
    if trigger.team is not None:
        opp = match.opponent(trigger.team)
        values |= {
            "team": team_name(match, trigger.team),
            "opp": team_name(match, opp),
            "team_s": team_short(match, trigger.team),
            "opp_s": team_short(match, opp),
        }
        sides |= {"team": trigger.team, "opp": opp}
    for role, player in trigger.roles.items():
        values[role] = player_name(match, player)
    if "goalkeeper" in values:
        values["keeper"] = values["goalkeeper"]
    for metric, short in SHORT.items():
        for side, team in sides.items():
            fact = find_fact(facts, metric, team=team)
            if fact is not None:
                values[f"{short}_{side}"] = fact.display
    for prefix, name in (("shot_xg:", "shot_xg"), ("shot_speed:", "shot_speed"),
                         ("spell_shots:", "spell_shots"),
                         ("spell_box_entries:", "spell_entries")):  # fmt: skip
        fact = find_key(facts, prefix)
        if fact is not None:
            values[name] = fact.display
    if trigger.team is not None:
        momentum = find_fact(facts, "momentum", team=trigger.team)
        if momentum is not None:
            values["momentum"] = momentum.display
    chaos = find_fact(facts, "chaos_index")
    if chaos is not None:
        values["chaos"] = chaos.display
    scorer = trigger.roles.get("scorer")
    if scorer is not None:
        goals = find_fact(facts, "score", team=match.player(scorer).team, player=scorer)
        if goals is not None:
            values["scorer_goals"] = goals.display
    carded = trigger.roles.get("carded")
    if carded is not None:
        fouls = find_fact(facts, "fouls", team=match.player(carded).team, player=carded)
        if fouls is not None:
            values["fouls_player"] = fouls.display
    if "tick_minute" in trigger.details:
        values["tick"] = trigger.details["tick_minute"]
    reason = trigger.details.get("var_reason")
    if reason in VAR_REASON:
        values["reason"] = VAR_REASON[reason][0 if vi else 1]
    check_type = trigger.details.get("check_type")
    if check_type in VAR_CHECK:
        values["check"] = VAR_CHECK[check_type][0 if vi else 1]
    fate = _fate(trigger.outcome, values.get("keeper"), lang)
    if fate is not None:
        values["fate"] = fate
    return values
