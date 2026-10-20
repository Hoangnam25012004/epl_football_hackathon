"""Generated claims against an independent oracle (design section 13.6: no wrong verdict).

Claims are built from templates (teams x metric x comparison x window x language) on every
scenario; the expected verdict is computed here, straight from the Stats Tools and the
tolerances of the design (section 5.2), without the parser or the Judge. The verifier may refuse
(INSUFFICIENT_DATA) but must never return another verdict than the oracle's; and it must read
almost every one of these simple phrasings.
"""

from collections import Counter
from dataclasses import dataclass
from functools import cache

import pytest
from helpers import claim_context, generated, view_at

from pundit.agents.analyst_rules import analyze_claim
from pundit.agents.collector import collect
from pundit.agents.contracts import ClaimAnalysis, Verdict
from pundit.agents.explain_templates import render
from pundit.agents.guardrail import check_judged
from pundit.agents.judge import judge
from pundit.domain.matchtime import FULL, H1, H2, MinuteWindow, RollingWindow
from pundit.stats import MetricParams, compute

MATCHES = [
    ("dominant_loser", 42),
    ("balanced", 3),
    pytest.param("comeback_var", 7, marks=pytest.mark.slow),
    pytest.param("red_card_swing", 7, marks=pytest.mark.slow),
]
Window = MinuteWindow | RollingWindow
WINDOWS: dict[str, tuple[Window, str, str]] = {
    "H1": (H1, "trong hiệp 1", "in the first half"),
    "H2": (H2, "trong hiệp 2", "in the second half"),
    "FULL": (FULL, "cả trận", "overall"),
    "60-": (MinuteWindow(start_min=60), "từ phút 60", "since minute 60"),
}
LAST_15 = ("15 phút cuối", "in the last 15 minutes")
# metric -> (Vietnamese noun, English noun)
COUNTS = {
    "shots": ("cú sút", "shots"),
    "shots_on_target": ("cú sút trúng đích", "shots on target"),
    "corners": ("quả phạt góc", "corners"),
    "fouls": ("lần phạm lỗi", "fouls"),
    "passes": ("đường chuyền", "passes"),
    "offsides": ("lần việt vị", "offsides"),
    "box_entries": ("lần vào vòng cấm", "box entries"),
    "cards": ("thẻ phạt", "cards"),
}
TEAM_NAMES = {
    "harbor_lions": "Harbor",
    "kestrel_athletic": "Kestrel",
    "ironvale_united": "Ironvale",
    "saltmarsh_rovers": "Saltmarsh",
}
PERCENT_TOLERANCE = 2.5  # design section 5.2, possession stated exactly
XG_TOLERANCE = 0.05


@dataclass(frozen=True)
class Case:
    text: str
    expected: Verdict


def value(
    view_key: tuple[str, int], metric: str, team: str, window: Window, player: str | None = None
) -> float:
    params = MetricParams(team=team, player=player, window=window)
    result = compute(view_at(*view_key), metric, params)
    assert result.value is not None
    return result.value


def more_than(a: float, b: float, tolerance: float) -> Verdict:
    if a - b > tolerance:
        return "TRUE"
    if b - a > tolerance:
        return "FALSE"
    return "PARTLY_TRUE" if tolerance > 0 else "FALSE"


def stated(actual: float, said: float) -> Verdict:
    if said == actual:
        return "TRUE"
    if abs(said - actual) == 1 and actual >= 5:
        return "PARTLY_TRUE"
    return "FALSE"


def cases(scenario: str, seed: int) -> list[Case]:
    meta = generated(scenario, seed).meta
    home, away = meta.team_ids
    key = (scenario, seed)
    out: list[Case] = []
    last = view_at(*key).last
    assert last is not None
    windows = [*WINDOWS.values(), (RollingWindow.last(15, last.match_ms), *LAST_15)]
    for window, vi_w, en_w in windows:
        for a, b in ((home, away), (away, home)):
            short_a, short_b = meta.team_info(a).short_name, meta.team_info(b).short_name
            name_a, name_b = TEAM_NAMES[a], TEAM_NAMES[b]
            for metric, (vi, en) in COUNTS.items():
                va, vb = value(key, metric, a, window), value(key, metric, b, window)
                more, fewer = more_than(va, vb, 0), more_than(vb, va, 0)
                out += [
                    Case(f"{short_a} có nhiều {vi} hơn {short_b} {vi_w}", more),
                    Case(f"{short_a} có ít {vi} hơn {short_b} {vi_w}", fewer),
                    Case(f"{name_a} had more {en} than {name_b} {en_w}", more),
                    Case(f"{name_a} had fewer {en} than {name_b} {en_w}", fewer),
                    Case(f"{short_a} chưa có {vi} nào {vi_w}", "TRUE" if va == 0 else "FALSE"),
                ]
                for said in sorted({va, va + 1, va + 3} | ({va - 1} if va >= 1 else set())):
                    out.append(Case(f"{short_a} có {said:.0f} {vi} {vi_w}", stated(va, said)))
                    out.append(Case(f"{name_a} had {said:.0f} {en} {en_w}", stated(va, said)))
            pa, pb = value(key, "possession", a, window), value(key, "possession", b, window)
            out += [
                Case(f"{short_a} kiểm soát bóng nhiều hơn {short_b} {vi_w}",
                     more_than(pa, pb, PERCENT_TOLERANCE)),
                Case(f"{name_a} had more possession than {name_b} {en_w}",
                     more_than(pa, pb, PERCENT_TOLERANCE)),
            ]  # fmt: skip
            xa, xb = value(key, "xg", a, window), value(key, "xg", b, window)
            out += [
                Case(f"{short_a} có xG cao hơn {short_b} {vi_w}", more_than(xa, xb, XG_TOLERANCE)),
                Case(f"{name_a} had a higher xG than {name_b} {en_w}",
                     more_than(xa, xb, XG_TOLERANCE)),
            ]  # fmt: skip
    out += player_cases(scenario, seed)
    return out


def player_cases(scenario: str, seed: int) -> list[Case]:
    """Scorers and the three busiest shooters of the match, per half and overall."""
    match = generated(scenario, seed)
    key = (scenario, seed)
    shooters = Counter(e.player for e in match.events if e.type == "shot" and e.player)
    scorers = {e.player for e in match.events if e.type == "goal" and e.player}
    out: list[Case] = []
    for player in sorted({p for p, _ in shooters.most_common(3)} | scorers):
        info = match.meta.players[player]
        name = info.name.split()[-1] if info.nationality != "VN" else info.short_name
        for window, vi_w, en_w in WINDOWS.values():
            goals = value(key, "score", info.team_id, window, player)
            scored = "TRUE" if goals >= 1 else "FALSE"
            out += [
                Case(f"{name} ghi bàn {vi_w}", scored),
                Case(f"{name} scored {en_w}", scored),
                Case(f"{name} chưa ghi bàn {vi_w}", "FALSE" if goals >= 1 else "TRUE"),
            ]
            shots = value(key, "shots", info.team_id, window, player)
            for said in sorted({shots, shots + 2}):
                out.append(Case(f"{name} có {said:.0f} cú sút {vi_w}", stated(shots, said)))
    return out


@cache
def verdict(text: str, scenario: str, seed: int) -> Verdict:
    ctx = claim_context(text, scenario=scenario, seed=seed)
    analysis = ClaimAnalysis(context=ctx, spec=analyze_claim(ctx), source="rule")
    return judge(collect(analysis, view_at(scenario, seed))).verdict


@pytest.mark.parametrize(("scenario", "seed"), MATCHES)
def test_no_wrong_verdict_against_the_oracle(scenario: str, seed: int) -> None:
    results = Counter[str]()
    wrong: list[tuple[str, str, str]] = []
    for case in cases(scenario, seed):
        got = verdict(case.text, scenario, seed)
        if got == case.expected:
            results["right"] += 1
        elif got == "INSUFFICIENT_DATA":
            results["refused"] += 1
        else:
            wrong.append((case.text, case.expected, got))
    assert wrong == [], wrong[:10]
    total = results["right"] + results["refused"]
    assert total > 500
    assert results["refused"] / total < 0.02, results  # these phrasings must be understood


@pytest.mark.parametrize(("scenario", "seed"), MATCHES)
def test_templates_pass_the_guardrail_as_model_text(scenario: str, seed: int) -> None:
    """The mock explainer answers with the templates, so every template must also meet the
    rules for model text (a number in each sentence about the match, no fragment)."""
    failed = []
    for case in cases(scenario, seed)[::5]:
        ctx = claim_context(case.text, scenario=scenario, seed=seed)
        analysis = ClaimAnalysis(context=ctx, spec=analyze_claim(ctx), source="rule")
        judged = judge(collect(analysis, view_at(scenario, seed)))
        report = check_judged(render(judged), judged, model_text=True)
        if not report.passed:
            failed.append((case.text, render(judged), [v.code for v in report.violations]))
    assert failed == [], failed[:5]
