"""Guardrail (design section 7): number matching, context, verdict words, unsafe content,
evidence, and a mutation test over every template sentence."""

from functools import cache

import pytest
from helpers import DL, claim_context, generated, view_at

from pundit.agents.analyst_rules import analyze_claim
from pundit.agents.collector import collect
from pundit.agents.contracts import ClaimAnalysis, GuardrailReport, JudgedClaim
from pundit.agents.explain_templates import MAX_TEXT_CHARS, render
from pundit.agents.guardrail import check_judged, guard_context, rounding_match
from pundit.agents.judge import judge
from pundit.agents.language import Text
from pundit.agents.numbers import extract_numbers

EVAL_TEXTS = (
    "Đội nhà áp đảo hiệp 2",
    "Harbor dominated the second half",
    "Kestrel áp đảo hiệp 2",
    "HAR có nhiều cú sút hơn KES trong hiệp 2",
    "Harbor had twice as many shots as Kestrel in the second half",
    "KES chỉ có 1 cú sút trong hiệp 2",
    "HAR kiểm soát bóng khoảng 60% trong hiệp 2",
    "Kestrel had more corners than Harbor in the second half",
    "KES chưa có quả phạt góc nào trong hiệp 2",
    "Hai đội phạm lỗi ngang nhau trong hiệp 2",
    "HAR pressing mạnh hơn KES trong hiệp 2",
    "HAR áp đảo hiệp 2 nhưng vẫn thua",
    "HAR có 14 cú sút trong hiệp 2",
    "Calder ghi bàn trong hiệp 2",
    "Kestrel won despite lower xG",
)


@cache
def judged(
    text: str, scenario: str = DL[0], seed: int = DL[1], at: str | None = None
) -> JudgedClaim:
    ctx = claim_context(text, scenario=scenario, seed=seed, at=at)
    analysis = ClaimAnalysis(context=ctx, spec=analyze_claim(ctx), source="rule")
    return judge(collect(analysis, view_at(scenario, seed, at)))


def report(text: str, claim: str = "Đội nhà áp đảo hiệp 2") -> GuardrailReport:
    return check_judged(text, judged(claim))


def codes(text: str, claim: str = "Đội nhà áp đảo hiệp 2") -> list[str]:
    return [v.code for v in report(text, claim).violations]


@pytest.mark.parametrize("claim", EVAL_TEXTS)
def test_templates_pass(claim: str) -> None:
    result = judged(claim)
    text = render(result)
    assert check_judged(text, result).passed, (text, check_judged(text, result))
    assert 0 < len(text) <= MAX_TEXT_CHARS


@pytest.mark.parametrize(
    "text",
    [
        "Trong hiệp 2, Harbor Lions FC kiểm soát bóng 61%, dứt điểm 15 lần so với 1.",
        "Harbor Lions FC had 61% possession and 15 shots to 1 in the second half.",
        "xG 2.24 so với 0.24, hai đội có cùng số lỗi.",
        "xG 2,24 for Harbor Lions FC.",
        "Momentum của Kestrel Athletic là -0.91.",
        "Harbor Lions FC 15-1 Kestrel Athletic on shots.",
        "Score at 90+6': Harbor Lions FC 1-2 Kestrel Athletic.",
        "Tính tới phút 90+6, Kestrel Athletic không thua.",
        "Harbor Lions FC dứt điểm gấp 15 lần.",
        "Harbor Lions FC kiểm soát bóng 61 phần trăm.",
    ],
)
def test_true_numbers_and_context_pass(text: str) -> None:
    assert report(text).passed, report(text)


@pytest.mark.parametrize(
    "text",
    [
        "Harbor Lions FC kiểm soát bóng 65%.",
        "Harbor Lions FC kiểm soát bóng 15%.",  # 15 is a count, not a percentage
        "Harbor Lions FC có 61 cú sút.",  # 61 is a percentage, not a count
        "xG 2.3 so với 0.24.",
        "Momentum của Kestrel Athletic là -0.5.",
        "Harbor Lions FC 2-2 Kestrel Athletic.",
        "Calder ghi bàn ở phút 63.",  # not a minute of this claim
        "Kestrel Athletic có ba quả phạt góc.",
        "Harbor Lions FC có 15.5 cú sút.",
    ],
)
def test_wrong_numbers_are_unverified(text: str) -> None:
    assert "UNVERIFIED_NUMBER" in codes(text), report(text)


def test_rounding_rule() -> None:
    assert rounding_match(61, 0, 61.29) and not rounding_match(62, 0, 61.29)
    assert rounding_match(2.2, 1, 2.24) and not rounding_match(2.3, 1, 2.24)
    assert rounding_match(2.24, 2, 2.2449) and not rounding_match(2.24, 2, 2.2451)


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Nhận định này đúng: Harbor Lions FC kiểm soát bóng 61%.", "VERDICT_MISMATCH"),
        ("Đúng một phần.", "VERDICT_MISMATCH"),
        ("This is true: Harbor Lions FC had 61% possession.", "VERDICT_MISMATCH"),
        ("The claim is false.", "VERDICT_MISMATCH"),
        ("Kestrel Athletic chơi thật vô dụng.", "UNSAFE_CONTENT"),
        ("Đồ ngu.", "UNSAFE_CONTENT"),
        ("Kestrel Athletic were useless.", "UNSAFE_CONTENT"),
        ("Harbor Lions FC are cheating.", "UNSAFE_CONTENT"),
        ("", "EMPTY"),
        ("   ", "EMPTY"),
        ("x" * (MAX_TEXT_CHARS + 1), "TOO_LONG"),
    ],
)
def test_rejections(text: str, code: str) -> None:
    assert code in codes(text)


def test_longest_verdict_phrase_is_reported_once() -> None:
    found = report("Đúng một phần.").violations
    assert [v.span for v in found] == ["đúng một phần"]


@pytest.mark.parametrize(
    "text", ["Cho Harbor Lions FC 61%.", "Ngủ ngon.", "Harbor Lions FC lao lên."]
)
def test_close_words_are_not_insults(text: str) -> None:
    assert "UNSAFE_CONTENT" not in codes(text)


def test_content_filter_signal() -> None:
    result = check_judged(
        "Harbor Lions FC kiểm soát bóng 61%.",
        judged("Đội nhà áp đảo hiệp 2"),
        content_filtered=True,
    )
    assert [v.detail for v in result.violations] == ["content_filter"]


def test_missing_evidence() -> None:
    claim = judged("KES chỉ có 1 cú sút trong hiệp 2")
    fact = claim.results[0].facts[0].model_copy(update={"evidence_event_ids": ()})
    result = claim.results[0].model_copy(update={"facts": (fact,)})
    broken = claim.model_copy(update={"results": (result,), "headline": (fact,)})
    assert "MISSING_EVIDENCE" in [
        v.code for v in check_judged("Kestrel Athletic có 1 cú sút.", broken).violations
    ]


def test_insufficient_verdicts_need_no_evidence() -> None:
    claim = judged("HAR áp đảo hiệp 2", at="30'")
    assert claim.verdict == "INSUFFICIENT_DATA"
    assert check_judged(render(claim), claim).passed
    assert guard_context(claim).clock_display == "30'"


def mutations(text: str, claim: JudgedClaim) -> list[str]:
    """Each number of `text` replaced by a value that no fact or context allows."""
    facts = [f.value for f in claim.all_facts()]
    context = guard_context(claim)
    out = []
    parsed = Text.of(text)
    for number in extract_numbers(parsed):
        if number.period or number.minute:
            continue
        for bump in (7, 13, 29, 41, 53):
            step = 1 if number.decimals == 0 else 10 ** (1 - number.decimals)
            candidate = number.signed + bump * step
            taken = [*facts, *(context.score or ())]
            if not any(rounding_match(candidate, number.decimals, v) for v in taken):
                start = number.start - (1 if number.negative else 0)
                written = f"{candidate:.{number.decimals}f}"
                out.append(parsed.raw[:start] + written + parsed.raw[number.end :])
                break
    return out


@pytest.mark.parametrize("claim_text", EVAL_TEXTS)
def test_mutation_every_changed_number_is_caught(claim_text: str) -> None:
    claim = judged(claim_text)
    text = render(claim)
    mutated = mutations(text, claim)
    assert mutated, text
    for wrong in mutated:
        assert "UNVERIFIED_NUMBER" in [v.code for v in check_judged(wrong, claim).violations], wrong


def test_other_scenarios_templates_pass() -> None:
    claims = [
        ("Ironvale lội ngược dòng và thắng trận", "comeback_var", 7),
        ("Saltmarsh had more shots than Ironvale in the first half", "comeback_var", 7),
        ("SAL có 1 bàn bị VAR huỷ", "comeback_var", 7),
        ("Kestrel nhận 1 thẻ đỏ", "red_card_swing", 7),
        ("Harbor dominated after minute 55", "red_card_swing", 7),
        ("IRO kiểm soát bóng khoảng 50% trong 15 phút cuối", "balanced", 3),
    ]
    for text, scenario, seed in claims:
        result = judged(text, scenario, seed)
        rendered = render(result)
        assert check_judged(rendered, result).passed, (text, rendered)
    assert generated("comeback_var", 7)  # cached for other tests
