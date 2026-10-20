"""Judge: EvidenceBundle -> JudgedClaim, fully deterministic (docs/day2-design.md section 5).

Facts carry the evidence of their StatResult. A value of zero has no event to point at, so it
carries *coverage evidence*: the possession-opening events of the window (the evidence of
`ball_in_play_time`), which prove the window was played. Every verdict other than
INSUFFICIENT_DATA therefore cites at least one event.
"""

from pundit.agents.collector import COVERAGE_METRIC, QueryKey, results_by_key, stat_key
from pundit.agents.contracts import (
    MAX_HEADLINE,
    Assertion,
    AssertionResult,
    EvidenceBundle,
    Fact,
    JudgedClaim,
    MatchContext,
    ReasonCode,
    Subject,
)
from pundit.agents.rubric import (
    MIN_IN_PLAY_MINUTES,
    DominanceInputs,
    Judgement,
    aggregate,
    judge_compare,
    judge_dominance,
    judge_value,
    negate,
)
from pundit.agents.timescope import is_future
from pundit.agents.wording import fact_labels, format_value, ratio_text
from pundit.domain.matchtime import MinuteWindow, RollingWindow
from pundit.stats import StatResult, display

Window = MinuteWindow | RollingWindow


class _Insufficient(Exception):
    def __init__(self, reason: ReasonCode, rationale: str) -> None:
        super().__init__(rationale)
        self.reason = reason
        self.rationale = rationale


def _who(ctx: MatchContext, team: str | None, player: str | None) -> str:
    if player is not None:
        return ctx.player(player).short_name
    if team is not None:
        return ctx.team(team).short_name
    return "match"


def _merge(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({eid for group in groups for eid in group}))


class Evidence:
    """The stat results of a bundle, looked up by (metric, team, player, window)."""

    def __init__(self, bundle: EvidenceBundle) -> None:
        self.ctx = bundle.analysis.context.match
        self.results: dict[QueryKey, StatResult] = results_by_key(bundle)

    def result(
        self, metric: str, window: Window, team: str | None = None, player: str | None = None
    ) -> StatResult:
        return self.results[stat_key(metric, team, player, window)]

    def coverage(self, window: Window) -> tuple[str, ...]:
        return self.result(COVERAGE_METRIC, window).evidence_event_ids

    def in_play_minutes(self, window: Window) -> float:
        return self.result(COVERAGE_METRIC, window).value or 0.0

    def fact(self, result: StatResult) -> Fact:
        if result.value is None:
            raise _Insufficient("no_data_in_window", f"{result.metric}_unavailable")
        who = _who(self.ctx, result.team, result.player)
        label = result.window.label()
        evidence = result.evidence_event_ids or self.coverage(result.window)
        label_vi, label_en = fact_labels(result.metric, who, result.window)
        return Fact(
            key=f"{result.metric}:{result.player or result.team or 'match'}:{label}",
            label_vi=label_vi,
            label_en=label_en,
            value=result.value,
            unit=result.unit,
            display=display(result),
            evidence_event_ids=evidence,
            metric=result.metric,
            team=result.team,
            player=result.player,
        )


def _side(subject: Subject) -> tuple[str | None, str | None]:
    """(team, player) as the collector queried it."""
    return subject.team_id, subject.player


def derived_diff(a: Fact, b: Fact) -> Fact:
    """The gap between two facts, as a positive number ("a gap of 2.00 xG")."""
    value = abs(a.value - b.value)
    return Fact(
        key=f"{a.key}|diff|{b.key}",
        label_vi=f"Chênh lệch {a.label_vi} so với {b.label_vi}",
        label_en=f"Difference {a.label_en} vs {b.label_en}",
        value=value,
        unit=a.unit,
        display=format_value(value, a.unit),
        evidence_event_ids=_merge(a.evidence_event_ids, b.evidence_event_ids),
        derived=True,
        metric=a.metric,
    )


def derived_ratio(a: Fact, b: Fact) -> Fact | None:
    if b.value <= 0 or a.value < 0:
        return None
    value = a.value / b.value
    return Fact(
        key=f"{a.key}|ratio|{b.key}",
        label_vi=f"Tỷ lệ {a.label_vi} so với {b.label_vi}",
        label_en=f"Ratio {a.label_en} to {b.label_en}",
        value=value,
        unit="ratio",
        display=ratio_text(value),
        evidence_event_ids=_merge(a.evidence_event_ids, b.evidence_event_ids),
        derived=True,
        metric=a.metric,
    )


def _check_window(evidence: Evidence, window: Window) -> None:
    if is_future(window, evidence.ctx):
        raise _Insufficient("future_window", "future_window")
    if evidence.in_play_minutes(window) <= 0:
        raise _Insufficient("no_data_in_window", "no_ball_in_play")


def _value(evidence: Evidence, a: Assertion) -> tuple[Judgement, list[Fact], list[Fact]]:
    assert a.metric is not None and a.op is not None and a.value is not None
    team, player = _side(a.subject)
    fact = evidence.fact(evidence.result(a.metric, a.window, team, player))
    judgement = judge_value(fact.value, a.value, a.op, fact.unit, a.approx)
    return judgement, [fact], [fact]


def _compare(evidence: Evidence, a: Assertion) -> tuple[Judgement, list[Fact], list[Fact]]:
    assert a.metric is not None and a.op is not None
    ctx = evidence.ctx
    if a.subject.kind == "both_teams":
        home, away = ctx.teams[0].id, ctx.teams[1].id
        sides: list[tuple[str | None, str | None]] = [(home, None), (away, None)]
    else:
        other = a.other
        if other is None:
            assert a.subject.team is not None
            other = Subject(kind="team", team=ctx.opponent(a.subject.team))
        sides = [_side(a.subject), _side(other)]
    first = evidence.fact(evidence.result(a.metric, a.window, *sides[0]))
    second = evidence.fact(evidence.result(a.metric, a.window, *sides[1]))
    judgement = judge_compare(
        first.value, second.value, a.op, first.unit, ratio=a.ratio, approx=a.approx
    )
    facts = [first, second, derived_diff(first, second)]
    ratio = (
        derived_ratio(first, second)
        if a.op in (">", ">=", "==", "~")
        else derived_ratio(second, first)
    )
    if ratio is not None:
        facts.append(ratio)
    headline = [first, second]
    if a.ratio is not None and ratio is not None:
        headline.append(ratio)
    return judgement, facts, headline


def _dominance(evidence: Evidence, a: Assertion) -> tuple[Judgement, list[Fact], list[Fact]]:
    team = a.subject.team
    assert team is not None
    if evidence.in_play_minutes(a.window) < MIN_IN_PLAY_MINUTES:
        raise _Insufficient("low_sample", "low_sample")
    opponent = evidence.ctx.opponent(team)

    def pair(metric: str) -> tuple[Fact, Fact]:
        return (
            evidence.fact(evidence.result(metric, a.window, team)),
            evidence.fact(evidence.result(metric, a.window, opponent)),
        )

    possession, possession_o = pair("possession")
    shots, shots_o = pair("shots")
    xg, xg_o = pair("xg")
    momentum, momentum_o = pair("momentum")
    judgement = judge_dominance(
        DominanceInputs(
            possession=possession.value,
            shots=shots.value,
            opponent_shots=shots_o.value,
            xg=xg.value,
            opponent_xg=xg_o.value,
            momentum=momentum.value,
        )
    )
    facts = [possession, possession_o, shots, shots_o, xg, xg_o, momentum, momentum_o]
    facts.append(derived_diff(xg, xg_o))
    ratio = derived_ratio(shots, shots_o)
    if ratio is not None:
        facts.append(ratio)
    return judgement, facts, [possession, shots, xg, momentum]


def judge_assertion(evidence: Evidence, a: Assertion) -> tuple[AssertionResult, list[Fact]]:
    try:
        _check_window(evidence, a.window)
        if a.kind == "value":
            judgement, facts, headline = _value(evidence, a)
        elif a.kind == "compare":
            judgement, facts, headline = _compare(evidence, a)
        else:
            judgement, facts, headline = _dominance(evidence, a)
    except _Insufficient as missing:
        result = AssertionResult(
            assertion=a,
            outcome="INSUFFICIENT",
            facts=(),
            rationale_code=missing.rationale,
            reason_code=missing.reason,
        )
        return result, []
    outcome = negate(judgement.outcome) if a.negated else judgement.outcome
    result = AssertionResult(
        assertion=a, outcome=outcome, facts=tuple(facts), rationale_code=judgement.rationale
    )
    return result, headline


def judge(bundle: EvidenceBundle) -> JudgedClaim:
    analysis = bundle.analysis
    context = analysis.context
    spec = analysis.spec
    evidence = Evidence(bundle)
    results: list[AssertionResult] = []
    headline: dict[str, Fact] = {}
    for assertion in spec.assertions:
        result, facts = judge_assertion(evidence, assertion)
        results.append(result)
        for fact in facts:
            headline.setdefault(fact.key, fact)
    verdict = aggregate([r.outcome for r in results])
    if verdict != "INSUFFICIENT_DATA":
        reason: ReasonCode = "ok"
    elif not results:
        reason = spec.unsupported_reason or "unsupported_claim"
    else:
        reason = results[0].reason_code
    return JudgedClaim(
        claim_id=context.claim_id,
        text=context.text,
        context=context.match,
        spec=spec,
        verdict=verdict,
        reason_code=reason,
        results=tuple(results),
        headline=tuple(list(headline.values())[:MAX_HEADLINE]),
    )
