"""Templates (design section 6.1): every (verdict x assertion kind x language) combination and
every INSUFFICIENT reason renders, stays short, has no verdict word and passes the Guardrail
(principle L4: the last fallback can never be blocked)."""

from functools import cache

import pytest
from helpers import claim_context, run, view_at
from hypothesis import given, settings
from hypothesis import strategies as st

from pundit.agents.analyst_rules import analyze_claim
from pundit.agents.collector import collect
from pundit.agents.contracts import ClaimAnalysis, ClaimSpec, JudgedClaim
from pundit.agents.explain_templates import (
    MAX_TEXT_CHARS,
    TemplateExplainer,
    render,
    template_draft,
)
from pundit.agents.guardrail import check_judged
from pundit.agents.judge import judge
from pundit.llm.mock import hallucinate


@cache
def judged(
    text: str, scenario: str = "dominant_loser", seed: int = 42, at: str | None = None
) -> JudgedClaim:
    ctx = claim_context(text, scenario=scenario, seed=seed, at=at)
    analysis = ClaimAnalysis(context=ctx, spec=analyze_claim(ctx), source="rule")
    return judge(collect(analysis, view_at(scenario, seed, at)))


def assert_publishable(claim: JudgedClaim) -> str:
    text = render(claim)
    report = check_judged(text, claim)
    assert report.passed, (claim.text, text, report.violations)
    assert 0 < len(text) <= MAX_TEXT_CHARS
    return text


# (claim, expected verdict, language) covering verdict x kind x language
COMBINATIONS = [
    # dominance
    ("Đội nhà áp đảo hiệp 2", "TRUE", "vi"),
    ("Harbor dominated the second half", "TRUE", "en"),
    ("Kestrel áp đảo hiệp 2", "FALSE", "vi"),
    ("Kestrel dominated the second half", "FALSE", "en"),
    # compare (incl. ratio, equality, polarity, result)
    ("HAR có nhiều cú sút hơn KES trong hiệp 2", "TRUE", "vi"),
    ("Kestrel had more corners than Harbor in the second half", "FALSE", "en"),
    ("HAR sút gấp đôi KES trong hiệp 2", "TRUE", "vi"),
    ("Harbor had three times as many corners as Kestrel", "PARTLY_TRUE", "en"),
    ("Hai đội phạm lỗi ngang nhau trong hiệp 2", "TRUE", "vi"),
    ("Both teams committed the same number of fouls", "FALSE", "en"),
    ("HAR pressing mạnh hơn KES trong hiệp 2", "TRUE", "vi"),
    ("Kestrel pressed harder than Harbor in the second half", "FALSE", "en"),
    ("Kestrel thắng trận", "TRUE", "vi"),
    ("Harbor won", "FALSE", "en"),
    # value
    ("KES chỉ có 1 cú sút trong hiệp 2", "TRUE", "vi"),
    ("Kestrel had only one shot in the second half", "TRUE", "en"),
    ("HAR kiểm soát bóng 70% trong hiệp 2", "FALSE", "vi"),
    ("Harbor had 14 shots in the second half", "PARTLY_TRUE", "en"),
    ("Calder ghi bàn trong hiệp 2", "TRUE", "vi"),
    ("Calder scored in the first half", "FALSE", "en"),
    ("HAR giữ bóng khoảng 16 phút trong hiệp 2", "TRUE", "vi"),
    # several clauses
    ("HAR áp đảo hiệp 2 nhưng vẫn thua", "TRUE", "vi"),
    ("Harbor dominated the second half and won", "PARTLY_TRUE", "en"),
]


@pytest.mark.parametrize(("text", "verdict", "lang"), COMBINATIONS)
def test_combinations(text: str, verdict: str, lang: str) -> None:
    claim = judged(text)
    assert (claim.verdict, claim.language) == (verdict, lang), claim.spec.notes
    assert_publishable(claim)


@pytest.mark.parametrize(
    ("text", "at", "scenario", "seed"),
    [
        ("HAR có nhiều đường chuyền hơn KES", "63'", "dominant_loser", 42),  # FULL, not finished
        ("Harbor had more passes than Kestrel", "45+2'", "dominant_loser", 42),
        ("HAR thắng trận", "70'", "dominant_loser", 42),  # score so far
        ("Harbor won the second half", None, "dominant_loser", 42),  # a half's score
        ("HAR áp đảo 15 phút cuối", "80'", "dominant_loser", 42),  # rolling window
        ("Harbor had more shots than Kestrel since minute 60", None, "dominant_loser", 42),
        ("HAR có nhiều cú sút hơn KES từ phút 60 đến phút 75", None, "dominant_loser", 42),
        ("Ironvale had more corners before the 30th minute", None, "comeback_var", 7),
    ],
)
def test_windows_and_clocks(text: str, at: str | None, scenario: str, seed: int) -> None:
    claim = judged(text, scenario, seed, at)
    assert claim.verdict != "INSUFFICIENT_DATA", (text, claim.reason_code)
    assert_publishable(claim)


@pytest.mark.parametrize(
    ("text", "at", "reason"),
    [
        ("HAR áp đảo hiệp 2", "30'", "future_window"),
        ("Harbor dominated the second half", "30'", "future_window"),
        ("HAR có nhiều cú sút hơn KES từ phút 60", "30'", "future_window"),
        ("Harbor had more shots since minute 60", "30'", "future_window"),
        ("Trọng tài thiên vị Harbor", None, "unsupported_claim"),
        ("The referee was biased", None, "unsupported_claim"),
        ("Smith ghi bàn trong hiệp 2", None, "unknown_entity"),
        ("Smith scored in the second half", None, "unknown_entity"),
        ("HAR áp đảo 2 phút cuối", "10'", "low_sample"),
        ("Harbor dominated the last 2 minutes", "10'", "low_sample"),
    ],
)
def test_insufficient_reasons(text: str, at: str | None, reason: str) -> None:
    claim = judged(text, at=at)
    assert (claim.verdict, claim.reason_code) == ("INSUFFICIENT_DATA", reason)
    assert_publishable(claim)


@pytest.mark.parametrize("reason", ["no_data_in_window", "ambiguous_entity", "as_of_out_of_range"])
@pytest.mark.parametrize("lang", ["vi", "en"])
def test_every_reason_has_a_text(reason: str, lang: str) -> None:
    base = judged("Trọng tài thiên vị Harbor")
    spec = ClaimSpec(language=lang, unsupported_reason=reason)  # type: ignore[arg-type]
    claim = base.model_copy(update={"spec": spec, "reason_code": reason})
    assert_publishable(claim)


def test_long_claims_fall_back_to_compact_sentences() -> None:
    claim = judged("Kestrel áp đảo hiệp 1 nhưng Harbor áp đảo hiệp 2 và Kestrel thắng trận")
    assert len(claim.results) == 3
    text = assert_publishable(claim)
    assert text.count(".") >= 2


def test_template_explainer_port() -> None:
    claim = judged("Đội nhà áp đảo hiệp 2")
    draft = run(TemplateExplainer().explain(claim))
    assert draft == template_draft(claim) and draft.source == "template" and draft.attempt == 1


def test_hallucination_fault_always_breaks_the_guardrail() -> None:
    for text, _, _ in COMBINATIONS:
        claim = judged(text)
        wrong = hallucinate(render(claim), claim)
        assert wrong != render(claim)
        assert "UNVERIFIED_NUMBER" in [v.code for v in check_judged(wrong, claim).violations]
    insufficient = judged("Trọng tài thiên vị Harbor")
    assert "UNVERIFIED_NUMBER" in [
        v.code
        for v in check_judged(
            hallucinate(render(insufficient), insufficient), insufficient
        ).violations
    ]


SUBJECTS = ["HAR", "KES", "Harbor", "Kestrel", "Đội nhà", "Đội khách"]
VI_PREDICATES = [
    "có nhiều cú sút hơn đối thủ",
    "có ít quả phạt góc hơn đối thủ",
    "kiểm soát bóng khoảng {n}%",
    "có {n} cú sút",
    "chưa có quả phạt góc nào",
    "áp đảo",
    "không áp đảo",
    "phạm lỗi nhiều hơn đối thủ",
    "có xG cao hơn đối thủ",
    "thắng trận",
]
WINDOWS = ["trong hiệp 1", "trong hiệp 2", "cả trận", "trong 15 phút cuối", "từ phút 60"]
MATCHES = [("dominant_loser", 42), ("comeback_var", 7), ("red_card_swing", 7), ("balanced", 3)]


@settings(max_examples=120, deadline=None)
@given(
    st.sampled_from(SUBJECTS),
    st.sampled_from(VI_PREDICATES),
    st.sampled_from(WINDOWS),
    st.integers(0, 70),
    st.sampled_from(MATCHES),
)
def test_generated_claims_always_publishable(
    subject: str, predicate: str, window: str, n: int, match: tuple[str, int]
) -> None:
    scenario, seed = match
    if scenario != "dominant_loser" and subject in ("HAR", "KES", "Harbor", "Kestrel"):
        subject = {"HAR": "IRO", "KES": "SAL", "Harbor": "Ironvale", "Kestrel": "Saltmarsh"}[
            subject
        ]
        if scenario == "red_card_swing":
            subject = {"IRO": "HAR", "SAL": "KES", "Ironvale": "Harbor", "Saltmarsh": "Kestrel"}[
                subject
            ]
    text = f"{subject} {predicate.format(n=n)} {window}"
    assert_publishable(judged(text, scenario, seed))
