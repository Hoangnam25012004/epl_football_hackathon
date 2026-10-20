"""Metamorphic tests of the claim verifier (design section 13.3, C1-C8).

C1-C4 run the deterministic agents directly (fast, hundreds of claims); C6-C8 check full
ClaimResults from the service (MAF workflow, mock LLM).
"""

import re
from functools import cache

import pytest
from helpers import claim_context, generated, source, verify, view_at

from pundit.agents.analyst_rules import analyze_claim
from pundit.agents.collector import collect
from pundit.agents.contracts import ClaimAnalysis, ClaimResult, JudgedClaim
from pundit.agents.guardrail import rounding_match
from pundit.agents.wording import window_numbers
from pundit.stats import MetricParams, compute
from pundit.stats.select import in_window

MATCHES = [("dominant_loser", 42), ("comeback_var", 7), ("red_card_swing", 7), ("balanced", 3)]
VI_COMPARE = {
    "shots": "{a} có nhiều cú sút hơn {b} {w}",
    "corners": "{a} có nhiều quả phạt góc hơn {b} {w}",
    "fouls": "{a} phạm lỗi nhiều hơn {b} {w}",
    "passes": "{a} có nhiều đường chuyền hơn {b} {w}",
    "possession": "{a} kiểm soát bóng nhiều hơn {b} {w}",
    "xg": "{a} có xG cao hơn {b} {w}",
    "box_entries": "{a} có nhiều lần vào vòng cấm hơn {b} {w}",
    "offsides": "{a} có nhiều lần việt vị hơn {b} {w}",
}
EN_COMPARE = {
    "shots": "{a} had more shots than {b} {w}",
    "corners": "{a} had more corners than {b} {w}",
    "fouls": "{a} committed more fouls than {b} {w}",
    "passes": "{a} had more passes than {b} {w}",
    "possession": "{a} had more possession than {b} {w}",
    "xg": "{a} had a higher xG than {b} {w}",
    "box_entries": "{a} had more box entries than {b} {w}",
    "offsides": "{a} had more offsides than {b} {w}",
}
VI_WINDOWS = {"H1": "trong hiệp 1", "H2": "trong hiệp 2", "FULL": "cả trận"}
EN_WINDOWS = {"H1": "in the first half", "H2": "in the second half", "FULL": "overall"}
FLIP = {
    "TRUE": "FALSE",
    "FALSE": "TRUE",
    "PARTLY_TRUE": "PARTLY_TRUE",
    "INSUFFICIENT_DATA": "INSUFFICIENT_DATA",
}


@cache
def judged(text: str, scenario: str, seed: int) -> JudgedClaim:
    from pundit.agents.judge import judge

    ctx = claim_context(text, scenario=scenario, seed=seed)
    analysis = ClaimAnalysis(context=ctx, spec=analyze_claim(ctx), source="rule")
    return judge(collect(analysis, view_at(scenario, seed)))


def short_names(scenario: str, seed: int) -> tuple[str, str]:
    meta = generated(scenario, seed).meta
    return meta.home.team.short_name, meta.away.team.short_name


def facts_of(result: JudgedClaim) -> dict[str, float]:
    return {f.key: f.value for f in result.all_facts() if not f.derived}


@pytest.mark.parametrize(("scenario", "seed"), MATCHES)
def test_c1_swapping_teams_flips_the_verdict(scenario: str, seed: int) -> None:
    home, away = short_names(scenario, seed)
    for metric, template in VI_COMPARE.items():
        for window in VI_WINDOWS.values():
            forward = judged(template.format(a=home, b=away, w=window), scenario, seed)
            backward = judged(template.format(a=away, b=home, w=window), scenario, seed)
            assert forward.spec.assertions, (metric, window)
            values = [f.value for f in forward.results[0].facts if not f.derived]
            if values and values[0] == values[1]:  # equal: "more" is false both ways
                assert forward.verdict == backward.verdict, (metric, window)
            else:
                assert backward.verdict == FLIP[forward.verdict], (metric, window, forward.verdict)


@pytest.mark.parametrize(("scenario", "seed"), MATCHES)
def test_c2_negation_flips_the_verdict(scenario: str, seed: int) -> None:
    home, away = short_names(scenario, seed)
    for team in (home, away):
        for window in VI_WINDOWS.values():
            plain = judged(f"{team} áp đảo {window}", scenario, seed)
            negated = judged(f"{team} không áp đảo {window}", scenario, seed)
            assert negated.verdict == FLIP[plain.verdict]
        for window in EN_WINDOWS.values():
            plain = judged(f"{team} dominated {window}", scenario, seed)
            negated = judged(f"{team} did not dominate {window}", scenario, seed)
            assert negated.verdict == FLIP[plain.verdict]
        compare = judged(f"{team} có nhiều cú sút hơn đối thủ cả trận", scenario, seed)
        not_compare = judged(f"{team} không có nhiều cú sút hơn đối thủ cả trận", scenario, seed)
        assert not_compare.verdict == FLIP[compare.verdict]


@pytest.mark.parametrize(("scenario", "seed"), MATCHES)
def test_c3_vietnamese_and_english_agree(scenario: str, seed: int) -> None:
    home, away = short_names(scenario, seed)
    for metric in VI_COMPARE:
        for key in VI_WINDOWS:
            vi = judged(
                VI_COMPARE[metric].format(a=home, b=away, w=VI_WINDOWS[key]), scenario, seed
            )
            en = judged(
                EN_COMPARE[metric].format(a=home, b=away, w=EN_WINDOWS[key]), scenario, seed
            )
            assert (vi.verdict, facts_of(vi)) == (en.verdict, facts_of(en)), (metric, key)
            assert (vi.language, en.language) == ("vi", "en")


@pytest.mark.parametrize(("scenario", "seed"), MATCHES)
def test_c4_paraphrases_agree(scenario: str, seed: int) -> None:
    home, _ = short_names(scenario, seed)
    for window in ("hiệp 1", "hiệp 2"):
        verdicts = {
            judged(f"{home} {verb} {window}", scenario, seed).verdict
            for verb in ("áp đảo", "lấn lướt", "làm chủ thế trận", "vượt trội", "chiếm ưu thế")
        }
        assert len(verdicts) == 1, (window, verdicts)
    en = {
        judged(f"{home} {verb} the second half", scenario, seed).verdict
        for verb in ("dominated", "controlled", "were on top in", "had the upper hand in")
    }
    assert len(en) == 1, en


SERVICE_CLAIMS = [
    ("Đội nhà áp đảo hiệp 2", "dominant_loser", 42, None),
    ("HAR có nhiều cú sút hơn KES trong hiệp 2", "dominant_loser", 42, None),
    ("KES chưa có quả phạt góc nào trong hiệp 2", "dominant_loser", 42, None),
    ("Calder ghi bàn trong hiệp 2", "dominant_loser", 42, None),
    ("HAR có nhiều cú sút hơn KES trong 15 phút cuối", "dominant_loser", 42, "70'"),
    ("HAR pressing mạnh hơn KES trong hiệp 2", "dominant_loser", 42, None),
    ("Kestrel won despite lower xG", "dominant_loser", 42, None),
    ("HAR kiểm soát bóng khoảng 55% từ phút 60", "dominant_loser", 42, "80'"),
    ("Ironvale dominated the second half", "comeback_var", 7, None),
    ("SAL có 1 bàn bị VAR huỷ", "comeback_var", 7, None),
    ("Saltmarsh had more shots than Ironvale in the first half", "comeback_var", 7, "45+2'"),
    ("Kestrel nhận 1 thẻ đỏ", "red_card_swing", 7, None),
    ("Harbor dominated after minute 55", "red_card_swing", 7, None),
    ("Hai đội phạm lỗi ngang nhau", "balanced", 3, None),
    ("IRO có xG cao hơn SAL trong hiệp 2", "balanced", 3, "70'"),
]


@cache
def service_result(text: str, scenario: str, seed: int, at: str | None) -> ClaimResult:
    return verify(text, scenario=scenario, seed=seed, at=at)


@pytest.mark.parametrize(("text", "scenario", "seed", "at"), SERVICE_CLAIMS)
def test_c6_every_fact_matches_an_independent_recomputation(
    text: str, scenario: str, seed: int, at: str | None
) -> None:
    result = service_result(text, scenario, seed, at)
    assert result.verdict != "INSUFFICIENT_DATA", result.reason_code
    view = view_at(scenario, seed, at)
    assert view.last_seq == result.as_of_seq
    for item in result.assertions:
        for fact in item.facts:
            if fact.derived:
                continue
            assert fact.metric is not None
            params = MetricParams(team=fact.team, player=fact.player, window=item.assertion.window)
            assert compute(view, fact.metric, params).value == pytest.approx(fact.value), fact.key


@pytest.mark.parametrize(("text", "scenario", "seed", "at"), SERVICE_CLAIMS)
def test_c7_evidence_exists_and_counts_lie_in_their_window(
    text: str, scenario: str, seed: int, at: str | None
) -> None:
    result = service_result(text, scenario, seed, at)
    view = view_at(scenario, seed, at)
    assert result.evidence_event_ids
    for event_id in result.evidence_event_ids:
        assert view.has(event_id)
    for item in result.assertions:
        for fact in item.facts:
            if fact.unit != "count" or fact.derived or fact.value == 0:
                continue
            for event_id in fact.evidence_event_ids:
                assert in_window(view.get(event_id), item.assertion.window), (fact.key, event_id)


@pytest.mark.parametrize(("text", "scenario", "seed", "at"), SERVICE_CLAIMS)
def test_c8_no_unknown_number_is_published(
    text: str, scenario: str, seed: int, at: str | None
) -> None:
    result = service_result(text, scenario, seed, at)
    allowed = {f.value for item in result.assertions for f in item.facts}
    allowed |= {float(n) for n in re.findall(r"\d+", result.as_of_display)}
    allowed |= set(result_scores(result))
    for item in result.assertions:
        allowed |= window_numbers(item.assertion.window)
    for written in re.findall(r"(?<![\d.])-?\d+(?:\.\d+)?", result.explanation):
        decimals = len(written.split(".")[1]) if "." in written else 0
        assert any(rounding_match(float(written), decimals, v) for v in allowed), written


def result_scores(result: ClaimResult) -> list[float]:
    """Both scores at the claim's as_of (a scoreline is allowed context)."""
    scenario, seed = result.match_id.rsplit("-", 1)
    view = source(scenario, int(seed)).view_at(result.as_of_seq)
    return [compute(view, "score", MetricParams(team=t)).value or 0.0 for t in view.meta.team_ids]
