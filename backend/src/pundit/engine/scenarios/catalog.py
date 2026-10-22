"""Built-in scenarios (docs/day1-design.md section 9)."""

from pundit.domain.errors import ScenarioError
from pundit.domain.matchtime import FULL, H1, H2, MinuteWindow
from pundit.engine.scenarios.base import (
    Expect,
    Scenario,
    ScriptedCard,
    ScriptedGoal,
    Segment,
    TeamProfile,
    window_until,
)


def _profile(control: float, press: float, threat: float, discipline: float = 0.6) -> TeamProfile:
    return TeamProfile(control=control, press=press, threat=threat, discipline=discipline)


DOMINANT_LOSER = Scenario(
    key="dominant_loser",
    title_vi="Đội nhà áp đảo hiệp 2 nhưng vẫn thua",
    title_en="The home side dominates the second half but still loses",
    home="harbor_lions",
    away="kestrel_athletic",
    kickoff_utc="2026-10-24T14:00:00Z",
    weather="cloudy",
    stoppage_minutes={1: (1, 3), 2: (4, 6)},
    segments=(
        Segment(period=1, home=_profile(0.55, 0.5, 0.5), away=_profile(0.5, 0.5, 0.5)),
        Segment(period=2, home=_profile(0.82, 0.8, 0.45), away=_profile(0.3, 0.3, 0.2, 0.5)),
    ),
    goal_mode="scripted_only",
    scripted_goals=(
        ScriptedGoal(side="away", at="27'", pattern="header_from_corner"),
        ScriptedGoal(side="home", at="63'", pattern="cutback"),
        ScriptedGoal(side="away", at="78'", pattern="counter_attack"),
    ),
    expectations=(
        Expect.score_diff("away", ">=", 1),
        Expect.metric("possession", "home", H2, ">=", 60),
        Expect.metric("shots", "home", H2, ">=", 8),
        Expect.ratio("shots", H2, ">=", 2.0),
        Expect.diff("xg", H2, ">=", 0.8),
        Expect.diff("xg", FULL, ">", 0.0),
    ),
)

COMEBACK_VAR = Scenario(
    key="comeback_var",
    title_vi="Bị dẫn 0-2 rồi lội ngược dòng 3-2; một bàn bị VAR huỷ vì việt vị",
    title_en="From 0-2 down to a 3-2 win, with a goal overturned by VAR for offside",
    home="ironvale_united",
    away="saltmarsh_rovers",
    kickoff_utc="2026-10-25T16:30:00Z",
    weather="rain",
    stoppage_minutes={1: (1, 3), 2: (4, 6)},
    segments=(
        Segment(period=1, home=_profile(0.5, 0.5, 0.45), away=_profile(0.55, 0.5, 0.55)),
        Segment(period=2, home=_profile(0.55, 0.55, 0.55), away=_profile(0.5, 0.5, 0.45)),
        Segment(
            period=2,
            from_minute=60,
            home=_profile(0.68, 0.7, 0.8),
            away=_profile(0.4, 0.4, 0.3, 0.5),
        ),
    ),
    goal_mode="scripted_only",
    scripted_goals=(
        ScriptedGoal(side="away", at="18'", pattern="long_shot"),
        ScriptedGoal(side="away", at="35'", pattern="counter_attack", var_overturn=True),
        ScriptedGoal(side="away", at="52'", pattern="cutback"),
        ScriptedGoal(side="home", at="68'", pattern="header_from_corner"),
        ScriptedGoal(side="home", at="81'", pattern="cutback"),
        ScriptedGoal(side="home", at="90+2'", pattern="counter_attack"),
    ),
    expectations=(
        Expect.score_diff("away", ">=", 2, window=window_until(60)),
        Expect.score_diff("home", ">=", 1),
        Expect.metric("var_overturns", "away", FULL, ">=", 1),
        Expect.metric("offsides", "away", FULL, ">=", 1),
        Expect.diff("xg", H2, ">", 0.0),
    ),
)

RED_CARD_SWING = Scenario(
    key="red_card_swing",
    title_vi="Đội khách nhận thẻ đỏ phút 52, thế trận đổi chiều",
    title_en="An away red card on 52' swings the momentum",
    home="harbor_lions",
    away="kestrel_athletic",
    kickoff_utc="2026-10-31T19:45:00Z",
    weather="wind",
    stoppage_minutes={1: (1, 3), 2: (4, 6)},
    segments=(
        Segment(period=1, home=_profile(0.42, 0.45, 0.35), away=_profile(0.62, 0.6, 0.65)),
        Segment(period=2, home=_profile(0.45, 0.45, 0.4), away=_profile(0.6, 0.6, 0.6)),
        Segment(
            period=2,
            from_minute=52,
            home=_profile(0.75, 0.75, 0.85),
            away=_profile(0.3, 0.3, 0.2, 0.4),
        ),
    ),
    goal_mode="scripted_only",
    scripted_goals=(
        ScriptedGoal(side="away", at="30'", pattern="counter_attack"),
        ScriptedGoal(side="home", at="63'", pattern="header_from_corner"),
        ScriptedGoal(side="home", at="77'", pattern="cutback"),
    ),
    scripted_cards=(ScriptedCard(side="away", at="52'"),),
    expectations=(
        Expect.score_diff("home", ">=", 1),
        Expect.metric("momentum", "home", H1, "<", 0.0),
        Expect.metric("momentum", "home", MinuteWindow(period=2, start_min=52), ">", 0.0),
        Expect.metric("cards", "away", FULL, ">=", 1),
    ),
)

BALANCED = Scenario(
    key="balanced",
    title_vi="Trận cân bằng, bàn thắng ngẫu nhiên theo xG",
    title_en="Evenly matched, goals sampled from xG",
    home="ironvale_united",
    away="saltmarsh_rovers",
    kickoff_utc="2026-11-01T15:00:00Z",
    weather="clear",
    stoppage_minutes={1: (1, 3), 2: (3, 6)},
    segments=(
        Segment(period=1, home=_profile(0.5, 0.5, 0.5), away=_profile(0.5, 0.5, 0.5)),
        Segment(period=2, home=_profile(0.5, 0.5, 0.5), away=_profile(0.5, 0.5, 0.5)),
    ),
    goal_mode="xg_sampled",
)

SCENARIOS: dict[str, Scenario] = {
    s.key: s for s in (DOMINANT_LOSER, COMEBACK_VAR, RED_CARD_SWING, BALANCED)
}

# Claims to try in the studio (docs/day4-design.md section 3.6), true and false ones. Those of
# dominant_loser are labelled cases of the Day 2 eval set (evals/claims_dominant_loser_42.json).
EXAMPLE_CLAIMS: dict[str, dict[str, tuple[str, ...]]] = {
    "dominant_loser": {
        "vi": (
            "Đội nhà áp đảo hiệp 2",
            "HAR kiểm soát bóng 70% trong hiệp 2",
            "HAR áp đảo hiệp 2 và thắng trận",
            "Trọng tài thiên vị Harbor",
        ),
        "en": (
            "Harbor dominated the second half",
            "Kestrel had more corners than Harbor in the second half",
            "Harbor had twice as many shots as Kestrel in the second half",
            "Kestrel won despite lower xG",
        ),
    },
    "comeback_var": {
        "vi": (
            "Ironvale áp đảo hiệp 2",
            "SAL có nhiều cú sút hơn IRO trong hiệp 1",
            "Saltmarsh có 1 bàn bị VAR huỷ",
        ),
        "en": (
            "Ironvale dominated the second half",
            "Saltmarsh had more shots than Ironvale in the first half",
            "Saltmarsh had a goal overturned by VAR",
        ),
    },
    "red_card_swing": {
        "vi": (
            "Harbor áp đảo hiệp 2",
            "KES có nhiều cú sút hơn HAR trong hiệp 1",
            "Kestrel nhận 1 thẻ đỏ",
        ),
        "en": (
            "Harbor dominated the second half",
            "Kestrel had more shots than Harbor in the first half",
            "Kestrel received a red card",
        ),
    },
    "balanced": {
        "vi": (
            "IRO kiểm soát bóng nhiều hơn SAL",
            "SAL có nhiều phạt góc hơn IRO",
            "Ironvale áp đảo hiệp 2",
        ),
        "en": (
            "Ironvale had more possession than Saltmarsh",
            "Saltmarsh had more corners than Ironvale",
            "Saltmarsh dominated the first half",
        ),
    },
}
assert set(EXAMPLE_CLAIMS) == set(SCENARIOS), "every scenario has example claims"


def get_scenario(key: str) -> Scenario:
    scenario = SCENARIOS.get(key)
    if scenario is None:
        raise ScenarioError(f"unknown scenario {key!r}; known: {sorted(SCENARIOS)}")
    return scenario
