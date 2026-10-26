"""A guardrail rule of Day 7, from the eval on the Space (docs/day7-design.md section 7.4): the
model wrote "giành chiến thắng 2-0" for a team that lost, and the pair passed as two sides of a
stat. After a word of the result, a pair is a scoreline and must be the score."""

from functools import cache

import pytest
from helpers import DL, claim_context, view_at

from pundit.agents.analyst_rules import analyze_claim
from pundit.agents.collector import collect
from pundit.agents.contracts import ClaimAnalysis, JudgedClaim
from pundit.agents.explain_templates import render
from pundit.agents.guardrail import check_judged
from pundit.agents.judge import judge

CLAIM = "Đội nhà áp đảo hiệp 2"  # eval e01: TRUE, and Harbor lost the match
SPACE_E01 = (
    "Đội nhà kiểm soát bóng và số cú sút cao hơn trong hiệp 2, tạo điều kiện cho họ giành "
    "chiến thắng 2-0."
)


@cache
def judged(text: str = CLAIM) -> JudgedClaim:
    ctx = claim_context(text)
    analysis = ClaimAnalysis(context=ctx, spec=analyze_claim(ctx), source="rule")
    return judge(collect(analysis, view_at(*DL)))


def codes(text: str) -> list[str]:
    report = check_judged(text, judged(), model_text=True)
    return [v.code for v in report.violations]


def score() -> tuple[int, int]:
    match = judged().context
    return tuple(match.score[team.id] for team in match.teams)  # type: ignore[return-value]


def test_what_the_space_model_wrote_for_e01_is_refused() -> None:
    assert "WRONG_OUTCOME" in codes(SPACE_E01)


@pytest.mark.parametrize(
    "template",
    [
        "Harbor Lions FC dominated the second half (61% possession, 15 shots to 1) but lost {s}.",
        "Đội nhà áp đảo hiệp 2 với 15 cú sút so với 1, nhưng thua {s}.",
    ],
)
def test_the_true_score_after_a_result_word_passes(template: str) -> None:
    home, away = score()
    assert "WRONG_OUTCOME" not in codes(template.format(s=f"{home}-{away}"))


def test_a_stat_pair_away_from_any_result_word_is_left_alone() -> None:
    text = "Trong hiệp 2, Harbor Lions FC có 15 cú sút so với 1 của Kestrel Athletic, xG 2.24-0.24."
    assert "WRONG_OUTCOME" not in codes(text)


def test_the_template_of_every_verdict_still_passes() -> None:
    claim = judged()
    draft = render(claim)
    assert check_judged(draft, claim, model_text=False).passed
