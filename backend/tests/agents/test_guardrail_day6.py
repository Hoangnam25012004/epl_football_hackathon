"""Two guardrail rules of Day 6, from what the independent number audit found in Ollama's
explanations (docs/day6-design.md 4.5): a ratio word must be a ratio of the facts, and a minute
said of an event must be the minute of an event of the facts."""

from functools import cache

import pytest
from helpers import DL, claim_context, generated, view_at

from pundit.agents.analyst_rules import analyze_claim
from pundit.agents.collector import collect
from pundit.agents.contracts import ClaimAnalysis, JudgedClaim
from pundit.agents.explain_templates import render
from pundit.agents.guardrail import (
    EVENT_MINUTE_TYPES,
    check_judged,
    event_minute_violations,
    ratio_violations,
)
from pundit.agents.judge import judge
from pundit.agents.language import Text
from pundit.domain.matchtime import format_minute

TWICE = "Harbor had twice as many shots as Kestrel in the second half"  # eval e06: 15 to 1
VAR = "VAR đã huỷ một bàn thắng của Saltmarsh"  # eval c04: the decision came at 36'


@cache
def judged(text: str, scenario: str = DL[0], seed: int = DL[1]) -> JudgedClaim:
    ctx = claim_context(text, scenario=scenario, seed=seed)
    analysis = ClaimAnalysis(context=ctx, spec=analyze_claim(ctx), source="rule")
    return judge(collect(analysis, view_at(scenario, seed)))


def minutes(claim: JudgedClaim, scenario: str, seed: int) -> frozenset[str]:
    view = view_at(scenario, seed)
    found = set()
    for fact in claim.all_facts():
        for event_id in fact.evidence_event_ids:
            if view.has(event_id) and view.get(event_id).type in EVENT_MINUTE_TYPES:
                event = view.get(event_id)
                found.add(format_minute(event.period, event.clock_ms))
    return frozenset(found)


def ratio_codes(text: str, claim: str = TWICE) -> list[str]:
    return [v.code for v in ratio_violations(Text.of(text), judged(claim).all_facts())]


def test_what_ollama_wrote_for_e06_is_refused() -> None:
    text = "Harbor had 15 shots, twice as many as Kestrel's 1 in the second half."
    assert ratio_codes(text) == ["WRONG_RATIO"]
    report = check_judged(text, judged(TWICE), model_text=True)
    assert not report.passed and "WRONG_RATIO" in [v.code for v in report.violations]


@pytest.mark.parametrize(
    "text",
    [
        "In the second half, Harbor Lions FC had 15 shots and Kestrel Athletic had 1. "
        "That is 15 times as many.",
        "Harbor had more than twice as many shots as Kestrel in the second half (15 to 1).",
        "Harbor dứt điểm 15 lần, gấp 15 lần Kestrel trong hiệp 2.",
        "Harbor had at least twice as many shots: 15 to 1.",
    ],
)
def test_true_ratios_pass(text: str) -> None:
    assert ratio_codes(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "Harbor had three times as many shots as Kestrel (15 to 1).",
        "Harbor dứt điểm gấp đôi Kestrel trong hiệp 2: 15 so với 1.",
        "Harbor had ten times as many shots.",
    ],
)
def test_wrong_ratios_are_refused(text: str) -> None:
    assert ratio_codes(text) == ["WRONG_RATIO"]


def test_a_ratio_word_needs_two_facts_of_one_metric() -> None:
    # possession alone (one fact): no ratio can be checked, so none may be said
    assert ratio_codes("Harbor kiểm soát bóng gấp đôi.", "HAR kiểm soát bóng 61% trong hiệp 2") == [
        "WRONG_RATIO"
    ]


def test_what_ollama_wrote_for_ch02_is_refused() -> None:
    claim = judged(VAR, "comeback_var", 7)
    known = minutes(claim, "comeback_var", 7)
    assert known == frozenset({"36'"})  # the VAR decision
    text = "VAR đã huỷ bỏ một bàn thắng của Saltmarsh ở phút 90+6."
    violations = event_minute_violations(Text.of(text), known)
    assert [v.code for v in violations] == ["WRONG_MINUTE"] and "90+6'" in violations[0].detail
    report = check_judged(text, claim, model_text=True, event_minutes=known)
    assert "WRONG_MINUTE" in [v.code for v in report.violations]


@pytest.mark.parametrize(
    "text",
    [
        "VAR đã huỷ một bàn thắng của Saltmarsh ở phút 36.",
        "VAR ruled out a Saltmarsh goal at minute 36.",
        "VAR ruled out a Saltmarsh goal in the 36th minute.",
        "VAR ruled out a Saltmarsh goal at 36'.",
        "Tính đến phút 90+6, VAR đã huỷ 1 bàn thắng của Saltmarsh.",  # the claim's minute
    ],
)
def test_the_minute_of_the_event_passes(text: str) -> None:
    assert event_minute_violations(Text.of(text), frozenset({"36'"})) == []


@pytest.mark.parametrize(
    "text",
    [
        "VAR ruled out a Saltmarsh goal at minute 40.",
        "VAR huỷ bàn thắng vào phút thứ 52.",
        "The goal was disallowed in the 90th minute.",
        "It came at 45+2'.",
    ],
)
def test_another_minute_is_refused(text: str) -> None:
    assert [v.code for v in event_minute_violations(Text.of(text), frozenset({"36'"}))] == [
        "WRONG_MINUTE"
    ]


def test_the_goal_minute_of_a_scorer() -> None:
    claim = judged("Calder ghi bàn trong hiệp 2")
    known = minutes(claim, *DL)
    goal = next(e for e in generated(*DL).events if e.type == "goal" and e.team == "harbor_lions")
    minute = format_minute(goal.period, goal.clock_ms)
    assert minute in known
    good = f"Tobias Calder ghi bàn ở phút {minute.rstrip(chr(39))}."
    # this rule lets the goal's minute through (the Day 2 number rule is stricter still: a
    # minute must be a window bound or the claim's minute, so a model text never says one)
    assert event_minute_violations(Text.of(good), known) == []
    late = "Tobias Calder ghi bàn ở phút 90+6."
    assert [v.code for v in event_minute_violations(Text.of(late), known)] == ["WRONG_MINUTE"]


@pytest.mark.parametrize(
    "claim",
    [
        TWICE,
        "Đội nhà áp đảo hiệp 2",
        "HAR có 14 cú sút trong hiệp 2",
        "Calder ghi bàn trong hiệp 2",
    ],
)
def test_templates_still_pass(claim: str) -> None:
    result = judged(claim)
    text = render(result)  # in the claim's language
    assert ratio_violations(Text.of(text), result.all_facts()) == [], text
    assert check_judged(text, result).passed, text
