"""Judge on hand-built matches (design sections 5.1 and 5.5)."""

from builders import MatchBuilder

from pundit.agents.collector import collect
from pundit.agents.context import build_match_context
from pundit.agents.contracts import (
    Assertion,
    ClaimAnalysis,
    ClaimContext,
    ClaimRequest,
    ClaimSpec,
    JudgedClaim,
    Subject,
)
from pundit.agents.judge import judge
from pundit.domain.log import LogView
from pundit.domain.matchtime import FULL, H1, H2, MinuteWindow
from pundit.stats import MetricParams, compute

HAR = Subject(kind="team", team="harbor_lions")
KES = Subject(kind="team", team="kestrel_athletic")


def first_half() -> MatchBuilder:
    """H1 of about 3 minutes: a HAR shot (saved), a HAR corner, a KES foul and booking."""
    b = MatchBuilder()
    b.at("0:00").kick_off("H9", "H8")
    b.at("0:05").pass_("H8", "H7", end_x=80.0, end_y=20.0)
    b.at("0:09").shot("H7", outcome="saved", x=95.0, y=30.0)
    b.at("0:11").ball_recovery("A1", x=3.0, y=34.0)
    b.at("0:15").pass_("A1", "A5", x=3.0, y=34.0, end_x=20.0, end_y=30.0)
    b.at("0:20").pass_("A5", outcome="out", end_x=30.0, end_y=0.0)
    b.at("0:21").ball_dead("out_of_play")
    b.at("0:40").throw_in("H3", "H8", x=75.0, y=68.0)
    b.at("0:45").pass_("H8", outcome="out", end_x=105.0, end_y=60.0)
    b.at("0:46").ball_dead("out_of_play")
    b.at("1:10").corner("H11", "H5")
    b.at("1:15").foul("A4")
    b.ball_dead("foul")
    b.at("1:20").card("A4")
    b.at("1:50").free_kick("H5", "H6", x=90.0, y=34.0)
    b.at("2:50").pass_("H6", outcome="out", end_x=50.0, end_y=0.0)
    b.at("2:51").ball_dead("out_of_play")
    return b


def judged(view: LogView, *assertions: Assertion) -> JudgedClaim:
    match = build_match_context(view)
    ctx = ClaimContext(
        claim_id="t-c0001",
        request=ClaimRequest(match_id=match.match_id, text="test claim"),
        text="test claim",
        language="vi",
        match=match,
    )
    spec = ClaimSpec(language="vi", assertions=assertions)
    return judge(collect(ClaimAnalysis(context=ctx, spec=spec, source="rule"), view))


def value(subject: Subject, metric: str, op: str, stated: float, window=H1) -> Assertion:  # type: ignore[no-untyped-def]
    return Assertion(
        kind="value", subject=subject, metric=metric, op=op, value=stated, window=window
    )  # type: ignore[arg-type]


def test_value_claim_cites_the_counted_events() -> None:
    b = first_half()
    result = judged(b.view(), value(HAR, "shots", "==", 1))
    assert result.verdict == "TRUE"
    (fact,) = result.results[0].facts
    assert fact.value == 1 and fact.evidence_event_ids == (b.last("shot").id,)
    assert fact.key == "shots:harbor_lions:H1" and fact.display == "1"


def test_zero_uses_coverage_evidence() -> None:
    b = first_half()
    result = judged(b.view(), value(KES, "corners", "==", 0))
    assert result.verdict == "TRUE"
    (fact,) = result.results[0].facts
    coverage = compute(b.view(), "ball_in_play_time", MetricParams(window=H1)).evidence_event_ids
    assert fact.value == 0 and fact.evidence_event_ids == coverage and coverage


def test_compare_adds_derived_facts_with_merged_evidence() -> None:
    b = first_half()
    claim = Assertion(kind="compare", subject=HAR, other=KES, metric="shots", op=">", window=H1)
    result = judged(b.view(), claim)
    assert result.verdict == "TRUE"
    first, second, diff = result.results[0].facts[:3]
    assert diff.derived and diff.value == 1
    assert set(diff.evidence_event_ids) == set(first.evidence_event_ids) | set(
        second.evidence_event_ids
    )
    assert [f.key for f in result.headline] == [first.key, second.key]


def test_negation_flips_the_outcome() -> None:
    b = first_half()
    plain = judged(b.view(), value(KES, "cards", ">=", 1))
    negated = judged(b.view(), value(KES, "cards", ">=", 1).model_copy(update={"negated": True}))
    assert (plain.verdict, negated.verdict) == ("TRUE", "FALSE")


def test_short_windows_cannot_show_dominance() -> None:
    result = judged(first_half().view(), Assertion(kind="dominance", subject=HAR, window=H1))
    assert result.verdict == "INSUFFICIENT_DATA"
    assert result.reason_code == "low_sample" and result.results[0].facts == ()


def test_future_window() -> None:
    result = judged(first_half().view(), value(HAR, "shots", ">=", 1, H2))
    assert (result.verdict, result.reason_code) == ("INSUFFICIENT_DATA", "future_window")


def test_window_without_ball_in_play() -> None:
    b = first_half()
    b.end_period()  # the second half has started: minutes 40-44 are over but empty
    window = MinuteWindow(start_min=40, end_min=44)
    result = judged(b.view(), value(HAR, "shots", "==", 0, window))
    assert (result.verdict, result.reason_code) == ("INSUFFICIENT_DATA", "no_data_in_window")


def test_partly_true_and_insufficient_parts() -> None:
    b = first_half()
    result = judged(
        b.view(),
        value(HAR, "shots", "==", 1),
        value(HAR, "corners", "==", 5),
        value(HAR, "shots", ">=", 1, H2),
    )
    assert [r.outcome for r in result.results] == ["SUPPORTED", "CONTRADICTED", "INSUFFICIENT"]
    assert result.verdict == "PARTLY_TRUE" and result.reason_code == "ok"


def test_empty_spec_keeps_its_reason() -> None:
    b = first_half()
    match = build_match_context(b.view())
    ctx = ClaimContext(
        claim_id="t-c0002",
        request=ClaimRequest(match_id=match.match_id, text="test claim"),
        text="test claim",
        language="en",
        match=match,
    )
    spec = ClaimSpec(language="en", unsupported_reason="unknown_entity")
    result = judge(collect(ClaimAnalysis(context=ctx, spec=spec, source="rule"), b.view()))
    assert (result.verdict, result.reason_code, result.results) == (
        "INSUFFICIENT_DATA",
        "unknown_entity",
        (),
    )


def test_headline_is_capped_at_four() -> None:
    b = first_half()
    claims = [value(HAR, m, ">=", 0, FULL) for m in ("shots", "corners", "passes")]
    claims[2] = Assertion(
        kind="compare", subject=HAR, other=KES, metric="fouls", op="<", window=FULL
    )
    result = judged(b.view(), *claims)
    assert len(result.headline) == 4


def test_an_undefined_metric_is_insufficient_not_a_guess() -> None:
    """KES made no defensive action: its PPDA is undefined, so "HAR pressed harder" is refused."""
    b = first_half()
    claim = Assertion(kind="compare", subject=HAR, other=KES, metric="ppda", op="<", window=H1)
    result = judged(b.view(), claim)
    assert result.verdict == "INSUFFICIENT_DATA"
    assert result.results[0].rationale_code == "ppda_unavailable"
