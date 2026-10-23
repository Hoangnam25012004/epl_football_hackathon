"""What the MCP tools return (docs/day6-design.md section 3.5).

Every number is a Stats Tools value with its display and its evidence (event ids, capped, with
the total). Every answer says where it comes from (a live session or a replay), at which minute,
and that the data is synthetic.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pundit.agents.contracts import Fact
from pundit.mcp_server.sources import LiveEntry, Origin, ScenarioEntry
from pundit.stats.result import StatResult, display

DISCLAIMER = "Synthetic data: fictional teams and players in generated matches."
MAX_EVIDENCE = 25  # event ids per value (the total is given)
MAX_FACT_EVIDENCE = 10


class Out(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Answer(Out):
    synthetic: bool = True
    disclaimer: str = DISCLAIMER


class AsOf(Out):
    seq: int
    minute: str  # "63'", "45+2'"


class StatValue(Out):
    metric: str
    team: str | None
    team_name: str | None
    player: str | None
    player_name: str | None
    window: str  # "FULL", "H2", "H2 60-end", "62:10-77:10"
    value: float | None
    display: str
    unit: str
    numerator: float | None
    denominator: float | None
    status: str
    evidence_event_ids: list[str]
    evidence_total: int
    definition: str


class StatOut(Answer):
    match_id: str
    source: Origin
    as_of: AsOf
    stat: StatValue


class ComparisonOut(Answer):
    match_id: str
    source: Origin
    as_of: AsOf
    metric: str
    window: str
    home: StatValue
    away: StatValue
    diff: float | None  # home - away
    ratio: float | None  # home / away


class TeamScore(Out):
    id: str
    name: str
    short_name: str
    goals: int


class ScoreboardOut(Answer):
    match_id: str
    source: Origin
    as_of: AsOf
    period: int  # 0 before kick-off
    finished: bool
    home: TeamScore
    away: TeamScore


Scalar = str | int | float | bool | None


class EventOut(Out):
    id: str
    minute: str
    period: int
    type: str
    team: str | None
    player: str | None
    player_name: str | None
    x: float | None
    y: float | None
    details: dict[str, Scalar]  # outcome, card, decision, play type... as the event has them


class EventsOut(Answer):
    match_id: str
    source: Origin
    as_of: AsOf
    events: list[EventOut]
    missing: list[str]  # asked ids that are not (yet) in the match
    truncated: bool


class FactOut(Out):
    label_en: str
    label_vi: str
    display: str
    value: float
    unit: str
    metric: str | None
    team: str | None
    player: str | None
    evidence_event_ids: list[str]
    evidence_total: int


class MomentOut(Out):
    id: str
    minute: str
    kind: str
    importance: int  # 1-3
    team: str | None
    player: str | None
    player_name: str | None
    trigger_event_ids: list[str]
    facts: list[FactOut]


class MomentsOut(Answer):
    match_id: str
    source: Origin
    as_of: AsOf
    moments: list[MomentOut]
    truncated: bool


class AssertionOut(Out):
    kind: str  # dominance, compare, value
    metric: str | None
    window: str
    said: str  # the words of the claim this part checks
    outcome: str  # SUPPORTED, PARTIAL, CONTRADICTED, INSUFFICIENT
    reason_code: str
    facts: list[FactOut]


class ClaimOut(Answer):
    match_id: str
    source: Origin
    as_of: AsOf
    claim: str
    language: str
    verdict: Literal["TRUE", "PARTLY_TRUE", "FALSE", "INSUFFICIENT_DATA"]
    reason_code: str
    assertions: list[AssertionOut]
    headline: list[FactOut]
    explanation: str
    analyst: Literal["rules"] = "rules"  # always: no LLM is ever called through MCP
    evidence_event_ids: list[str]
    evidence_total: int


class MetricOut(Out):
    name: str
    unit: str
    scope: str
    supports_player: bool
    definition: str
    description_en: str
    description_vi: str


class MetricsOut(Answer):
    metrics: list[MetricOut]


class PlayerOut(Out):
    id: str
    name: str
    short_name: str
    team: str
    shirt: int


class PlayersOut(Answer):
    match_id: str
    players: list[PlayerOut]


class MatchesOut(Answer):
    live: list[LiveEntry] = Field(description="matches being played now (shared sessions)")
    scenarios: list[ScenarioEntry] = Field(
        description="generated matches: any seed works, e.g. dominant_loser-7"
    )


def stat_value(result: StatResult, names: dict[str, str]) -> StatValue:
    evidence = result.evidence_event_ids
    return StatValue(
        metric=result.metric,
        team=result.team,
        team_name=names.get(result.team) if result.team else None,
        player=result.player,
        player_name=names.get(result.player) if result.player else None,
        window=result.window.label(),
        value=result.value,
        display=display(result),
        unit=result.unit,
        numerator=result.numerator,
        denominator=result.denominator,
        status=result.status,
        evidence_event_ids=list(evidence[:MAX_EVIDENCE]),
        evidence_total=len(evidence),
        definition=result.definition,
    )


def fact_out(fact: Fact) -> FactOut:
    return FactOut(
        label_en=fact.label_en,
        label_vi=fact.label_vi,
        display=fact.display,
        value=fact.value,
        unit=fact.unit,
        metric=fact.metric,
        team=fact.team,
        player=fact.player,
        evidence_event_ids=list(fact.evidence_event_ids[:MAX_FACT_EVIDENCE]),
        evidence_total=len(fact.evidence_event_ids),
    )
