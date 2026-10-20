"""Guardrail (design section 7): number matching, context, verdict words, unsafe content,
evidence, and a mutation test over every template sentence."""

from functools import cache

import pytest
from helpers import DL, claim_context, generated, view_at

from pundit.agents.analyst_rules import analyze_claim
from pundit.agents.collector import collect
from pundit.agents.contracts import ClaimAnalysis, GuardrailReport, JudgedClaim
from pundit.agents.explain_templates import MAX_TEXT_CHARS, render
from pundit.agents.guardrail import check, check_judged, guard_context, rounding_match
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


# ----------------------------------------------------------------------------- relations


@pytest.mark.parametrize(
    ("claim", "text", "code"),
    [
        (
            "Kestrel had more corners than Harbor in the second half",
            "7 corners in the second half for HAR is less than 0 for KES",
            "WRONG_COMPARISON",
        ),
        (
            "Kestrel had more corners than Harbor in the second half",
            "Kestrel Athletic có nhiều phạt góc hơn, 0 so với 7.",
            "WRONG_COMPARISON",
        ),
        (
            "Kestrel won despite lower xG",
            "Kestrel Athletic had a higher xG than Harbor Lions FC, 0.96 to 2.73.",
            "WRONG_COMPARISON",
        ),
        (
            "Đội nhà áp đảo hiệp 2",
            "Harbor Lions FC kiểm soát bóng 39%, Kestrel Athletic 61%.",
            "WRONG_ATTRIBUTION",
        ),
        (
            "Đội nhà áp đảo hiệp 2",
            "Kestrel Athletic had 15 shots and Harbor Lions FC had 1.",
            "WRONG_ATTRIBUTION",
        ),
        (
            "Calder ghi bàn trong hiệp 2",
            "Trong hiệp 2, Ribas ghi 1 bàn.",
            "WRONG_ATTRIBUTION",
        ),
        (  # a comma ends the comparison: the 15 is given to Kestrel again
            "Đội nhà áp đảo hiệp 2",
            "Harbor Lions FC dứt điểm nhiều hơn, Kestrel Athletic có 15 cú sút.",
            "WRONG_ATTRIBUTION",
        ),
        (  # qwen2.5:3b, eval e04
            "Đội nhà áp đảo hiệp 2",
            "HAR chỉ kiểm soát bóng 61% trong hiệp 2, kém KES nhiều hơn 39%.",
            "WRONG_COMPARISON",
        ),
        (  # qwen2.5:3b, eval e03: after "hơn" the pair is (subject, the other side)
            "Kestrel áp đảo hiệp 2",
            "KES có thế trận tốt hơn trong hiệp 2 với tỷ lệ số cú sút cao hơn HAR 15-1.",
            "WRONG_ATTRIBUTION",
        ),
        (  # "difference" excuses the 1 of its own clause only
            "Kestrel scored more goals than Harbor",
            "Kestrel's goal difference is 1, Harbor scored 2.",
            "WRONG_ATTRIBUTION",
        ),
        (  # qwen2.5:3b, final evaluation e03: one number, the sides compared on the facts
            "Kestrel áp đảo hiệp 2",
            "KES kiểm soát bóng cao hơn HAR 61% trong hiệp 2.",
            "WRONG_COMPARISON",
        ),
        (  # "PPDA cao hơn" names the number itself: literally higher, which is false
            "HAR pressing mạnh hơn KES trong hiệp 2",
            "HAR có PPDA cao hơn KES trong hiệp 2 với 5.34 so với 13.20.",
            "WRONG_COMPARISON",
        ),
        (
            "Đội nhà áp đảo hiệp 2",
            "Harbor Lions FC took more shots than Kestrel Athletic and Kestrel Athletic had 15.",
            "WRONG_ATTRIBUTION",
        ),
    ],
)
def test_wrong_relations_are_caught(claim: str, text: str, code: str) -> None:
    assert code in codes(text, claim), report(text, claim)


@pytest.mark.parametrize(
    ("claim", "text"),
    [
        (
            "Kestrel had more corners than Harbor in the second half",
            "Harbor Lions FC had 7 corners, more than the 0 of Kestrel Athletic.",
        ),
        (
            "Kestrel had more corners than Harbor in the second half",
            "Kestrel Athletic có 0 quả phạt góc, ít hơn 7 của Harbor Lions FC.",
        ),
        (
            "Đội nhà áp đảo hiệp 2",
            "Đội nhà kiểm soát bóng cao hơn với 61% so với 39% của Đội khách trong hiệp hai.",
        ),
        ("Đội nhà áp đảo hiệp 2", "Harbor Lions FC outshot Kestrel Athletic 15 to 1."),
        (
            "Đội nhà áp đảo hiệp 2",
            "Harbor Lions FC had 61% possession and Kestrel Athletic had 39%.",
        ),
        ("Đội nhà áp đảo hiệp 2", "Cả hai đội: 61% và 39% kiểm soát bóng."),
        ("Calder ghi bàn trong hiệp 2", "Trong hiệp 2, T. Calder ghi 1 bàn."),
        (
            "HAR sút nhiều gấp đôi KES trong hiệp 2",
            "Harbor Lions FC có 15 cú sút, gấp 15 lần Kestrel Athletic.",
        ),
        (
            "Harbor had twice as many shots as Kestrel in the second half",
            "Harbor Lions FC took 15 shots, 15 times as many as Kestrel Athletic.",
        ),
        (
            "Đội nhà áp đảo hiệp 2",
            "Kestrel Athletic chỉ có 1 cú sút so với 15 của Harbor Lions FC.",
        ),
        # Vietnamese "nhiều / cao / ít" alone mean "many / high / few"; "hơn nhiều" is "much"
        (
            "Đội nhà áp đảo hiệp 2",
            "Kestrel Athletic chỉ có 39%, ít hơn nhiều so với 61% của Harbor Lions FC.",
        ),
        (
            "Đội nhà áp đảo hiệp 2",
            "Harbor Lions FC pressing tầm cao, Kestrel Athletic chỉ có 1 cú sút so với 15.",
        ),
        (
            "Đội nhà áp đảo hiệp 2",
            "Kestrel Athletic có 1 cú sút, Harbor Lions FC có nhiều cú sút với 15.",
        ),
        (
            "Đội nhà áp đảo hiệp 2",
            "Ít nhất 1 cú sút cho Kestrel Athletic, còn Harbor Lions FC có 15.",
        ),
        # after "hơn / than" the numbers may be the subject's (qwen2.5:3b, eval h01)
        (
            "Đội nhà áp đảo hiệp 2",
            "Harbor kiểm soát bóng cao hơn KES trong hiệp 2 với 61% so với 39%.",
        ),
        (
            "Đội nhà áp đảo hiệp 2",
            "Harbor Lions FC had more possession than Kestrel Athletic, 61% to 39%.",
        ),
        (
            "Đội nhà áp đảo hiệp 2",
            "Harbor Lions FC had more possession than Kestrel Athletic with 61% to 39%.",
        ),
        (
            "Đội nhà áp đảo hiệp 2",
            "Kestrel Athletic kiểm soát bóng 39%, kém Harbor Lions FC với 61%.",
        ),
        (  # qwen2.5:3b, eval h03: a difference and a ratio belong to no team
            "Kestrel scored more goals than Harbor",
            "Kestrel's goal difference is 1, and their ratio of goals to Harbor's is 2:1.",
        ),
        ("Đội nhà áp đảo hiệp 2", "HAR có nhiều cú sút hơn KES trong hiệp 2, 15-1."),
        ("Đội nhà áp đảo hiệp 2", "KES dứt điểm ít hơn HAR, 1-15."),
        ("Đội nhà áp đảo hiệp 2", "HAR kiểm soát bóng cao hơn KES, với 61% trong hiệp 2."),
        (  # the 39% is not the number of the comparison before the comma
            "Đội nhà áp đảo hiệp 2",
            "KES phạm lỗi nhiều hơn HAR, nhưng chỉ kiểm soát bóng 39%.",
        ),
        (  # "pressing tầm cao hơn" is about pressing: a lower PPDA
            "HAR pressing mạnh hơn KES trong hiệp 2",
            "HAR pressing tầm cao hơn KES với PPDA 5.34 so với 13.20.",
        ),
        ("HAR pressing mạnh hơn KES trong hiệp 2", "HAR có PPDA thấp hơn KES: 5.34 so với 13.20."),
        (  # qwen2.5:3b, evaluation h07: a difference of 7 points of pass accuracy
            "HAR có tỷ lệ chuyền chính xác cao hơn KES trong hiệp 2",
            "HAR có tỷ lệ chuyền chính xác 84%, cao hơn KES 7% trong hiệp 2.",
        ),
    ],
)
def test_right_relations_pass(claim: str, text: str) -> None:
    assert report(text, claim).passed, report(text, claim)


def swap_teams(text: str) -> str:
    marker = "\x00"
    return (
        text.replace("Harbor Lions FC", marker)
        .replace("Kestrel Athletic", "Harbor Lions FC")
        .replace(marker, "Kestrel Athletic")
    )


@pytest.mark.parametrize(
    "claim_text",
    [
        "Đội nhà áp đảo hiệp 2",
        "Kestrel áp đảo hiệp 2",
        "HAR có nhiều cú sút hơn KES trong hiệp 2",
        "Kestrel had more corners than Harbor in the second half",
        "HAR pressing mạnh hơn KES trong hiệp 2",
        "Kestrel won despite lower xG",
    ],
)
def test_swapping_the_teams_of_a_template_is_caught(claim_text: str) -> None:
    """Mutation test for attribution: the numbers stay true, the owners do not."""
    claim = judged(claim_text)
    text = render(claim)
    swapped = swap_teams(text)
    assert swapped != text
    found = [v.code for v in check_judged(swapped, claim).violations]
    assert "WRONG_ATTRIBUTION" in found or "UNVERIFIED_NUMBER" in found, swapped


def test_scorelines_keep_their_team_order() -> None:
    claim = "HAR áp đảo hiệp 2 nhưng vẫn thua"
    assert report("Kết quả chung cuộc: Harbor Lions FC 1-2 Kestrel Athletic.", claim).passed
    assert report("Kestrel Athletic 2-1 Harbor Lions FC.", claim).passed
    assert "WRONG_ATTRIBUTION" in codes(
        "Kết quả chung cuộc: Kestrel Athletic 1-2 Harbor Lions FC.", claim
    )
    assert "WRONG_ATTRIBUTION" in codes("Harbor Lions FC 2-1 Kestrel Athletic.", claim)


@pytest.mark.parametrize(
    "text",
    [
        "... và Kestrel Athletic chỉ có 1 cú sút trong hiệp 2.",  # seen with qwen2.5:3b
        "… và Kestrel Athletic chỉ có 1 cú sút trong hiệp 2.",
        "Và Kestrel Athletic chỉ có 1 cú sút trong hiệp 2.",
        "nhưng Kestrel Athletic chỉ có 1 cú sút trong hiệp 2.",
        "Còn Kestrel Athletic chỉ có 1 cú sút trong hiệp 2.",
        "And Kestrel Athletic had 1 shot in the second half.",
        "Harbor Lions FC kiểm soát bóng 61%...",
        '"... but Kestrel Athletic had 1 shot."',
    ],
)
def test_fragments_are_rejected(text: str) -> None:
    assert codes(text) == ["FRAGMENT"], report(text)


@pytest.mark.parametrize(
    "text",
    [
        "Trong khi Harbor Lions FC dứt điểm 15 lần, Kestrel Athletic chỉ có 1.",
        "While Harbor Lions FC had 15 shots, Kestrel Athletic had 1.",
        "Vào hiệp 2, Harbor Lions FC kiểm soát bóng 61%.",
    ],
)
def test_sentences_that_stand_alone_pass(text: str) -> None:
    assert report(text).passed, report(text)


def test_commentary_may_use_an_ellipsis() -> None:
    """The fragment rule belongs to verdict explanations, not to the shared `check`."""
    claim = judged("Đội nhà áp đảo hiệp 2")
    text = "Và... Harbor Lions FC dứt điểm 15 lần!"
    assert check(text, claim.all_facts(), guard_context(claim)).passed
    assert "FRAGMENT" in codes(text)


def model_report(text: str, claim: str) -> GuardrailReport:
    return check_judged(text, judged(claim), model_text=True)


@pytest.mark.parametrize(
    ("claim", "text"),
    [  # qwen2.5:3b in the evaluation (e03, e13, h05): no number, and false
        ("Kestrel áp đảo hiệp 2", "KES có chênh lệch xG lớn hơn HAR trong hiệp 2."),
        (
            "HAR pressing mạnh hơn KES trong hiệp 2",
            "HAR có chênh lệch PPDA pressing cao hơn KES trong hiệp 2",
        ),
        (
            "The visitors had more shots in the first half",
            "The score difference in the first half is tied with equal shots for both teams.",
        ),
        ("Đội nhà áp đảo hiệp 2", "They dominated the second half."),
        ("Đội nhà áp đảo hiệp 2", "Harbor Lions FC kiểm soát bóng 61%. Họ áp đảo hoàn toàn."),
    ],
)
def test_model_sentences_without_a_number_are_ungrounded(claim: str, text: str) -> None:
    assert "UNGROUNDED" in [v.code for v in model_report(text, claim).violations]
    assert "UNGROUNDED" not in codes(text, claim)  # templates are not held to it


@pytest.mark.parametrize(
    ("claim", "text"),
    [
        ("Đội nhà áp đảo hiệp 2", "Harbor Lions FC kiểm soát bóng 61% trong hiệp 2."),
        ("Đội nhà áp đảo hiệp 2", "Kết quả: Harbor Lions FC 1-2 Kestrel Athletic."),
        (
            "HAR pressing mạnh hơn KES trong hiệp 2",
            "PPDA của HAR là 5.34 so với 13.20 của KES. PPDA thấp hơn nghĩa là pressing mạnh hơn.",
        ),
    ],
)
def test_grounded_and_general_sentences_pass(claim: str, text: str) -> None:
    assert model_report(text, claim).passed, model_report(text, claim)


@pytest.mark.parametrize("claim", EVAL_TEXTS)
def test_templates_also_meet_the_model_rule(claim: str) -> None:
    """The mock explainer answers with the templates: they must pass as model text too."""
    result = judged(claim)
    assert model_report(render(result), claim).passed, render(result)
