"""Typed messages exchanged by the agents (docs/day2-design.md section 3).

Every model is frozen and rejects unknown fields. The public ones (ClaimRequest, ClaimResult,
Moment, WsMessage) are exported as JSON Schema by `pundit schema` and checked by contract tests.
Changing or removing a field means bumping `SPEC_VERSION` / `WS_VERSION`.
"""

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pundit.domain.ids import MATCH_ID_PATTERN, PlayerId, TeamId, team_of_player
from pundit.domain.matchtime import Window
from pundit.domain.types import Side
from pundit.stats.registry import Scope
from pundit.stats.result import StatResult, Unit

SPEC_VERSION: Literal["1"] = "1"
WS_VERSION: Literal[1] = 1
MAX_CLAIM_CHARS = 300
MAX_ASSERTIONS = 3
MAX_HEADLINE = 4

Lang = Literal["vi", "en"]
Verdict = Literal["TRUE", "PARTLY_TRUE", "FALSE", "INSUFFICIENT_DATA"]
Outcome = Literal["SUPPORTED", "PARTIAL", "CONTRADICTED", "INSUFFICIENT"]
ReasonCode = Literal[
    "ok",
    "unsupported_claim",
    "unknown_entity",
    "ambiguous_entity",
    "future_window",
    "no_data_in_window",
    "low_sample",
    "as_of_out_of_range",
]
Degraded = Literal[
    "analyst_fallback",  # the LLM analyst failed (timeout, error, invalid output twice)
    "analyst_disagreement",  # the LLM spec contradicted the rule parser; the rule spec was used
    "explainer_fallback",  # the published explanation is the deterministic template
    "circuit_open",  # LLM calls skipped because the circuit breaker is open
    "orchestrator_fallback",  # the workflow failed; the deterministic pipeline answered
    "writer_fallback",  # studio: a model text failed or was rejected; the template was used
    "llm_skipped",  # studio: no model call (no time left in the budget)
]
Op = Literal[">", ">=", "<", "<=", "==", "~"]
AssertionKind = Literal["dominance", "compare", "value"]
SubjectKind = Literal["team", "player", "both_teams"]
ViolationCode = Literal[
    "UNVERIFIED_NUMBER",
    "MISSING_EVIDENCE",
    "VERDICT_MISMATCH",
    "UNSAFE_CONTENT",
    "EMPTY",
    "TOO_LONG",
    "WRONG_COMPARISON",  # "7 is less than 0": every number is true, the relation is not
    "WRONG_ATTRIBUTION",  # a true number given to the wrong team or player
    "FRAGMENT",  # an explanation that does not stand alone ("... và KES chỉ có 1 cú sút")
    "UNGROUNDED",  # a model's sentence about the match without any number from the facts
    "WRONG_ENTITY",  # studio: a player who is not part of the moment
    "WRONG_OUTCOME",  # studio: "ghi bàn" for a shot that was saved, the wrong card colour
    "WRONG_LANGUAGE",  # studio: an English text where Vietnamese was asked, or the reverse
    "WRONG_REGISTER",  # studio: casual text with jargon or several numbers, analyst without any
]
MomentKind = Literal[
    "goal",
    "goal_overturned",
    "red_card",
    "yellow_card",
    "big_chance",
    "rocket_shot",
    "dangerous_spell",
    "momentum_shift",
    "substitution",
    "period_start",
    "period_end",
    "full_time",
]
SpanKind = Literal["agent", "tool", "llm", "guardrail", "fallback"]
SpanStatus = Literal["ok", "error", "retry", "fallback", "rejected"]
WsType = Literal[
    "hello",
    "event",
    "snapshot",
    "moment",
    "claim",
    "status",
    "resync",
    "talking_point",  # Day 3: clients must ignore types they do not know
    "lower_third",
    "commentary",
    "recap",
    "talking_point_status",
    "on_air",  # Day 4: an editor's decision about the lower-third on air
]
# Day 3 studio (docs/day3-design.md section 3)
CueKind = Literal["shot", "corner", "offside", "var_review", "added_time", "stat_tick", "lull"]
RuleId = Literal[
    "offside", "var", "penalty", "second_yellow", "red_card", "yellow_card", "corner",
    "substitution", "added_time",  # football rules
    "xg", "possession", "momentum", "ppda", "chaos", "tempo",  # statistics
]  # fmt: skip
EventOutcome = Literal[
    "goal", "saved", "blocked", "off_target", "post", "yellow", "second_yellow", "red",
    "offside", "overturned", "stands",
]  # fmt: skip
Register = Literal["analyst", "casual"]
ItemSource = Literal["llm", "template"]
StudioBranch = Literal["talking_point", "lower_third", "commentary"]
TalkingAngle = Literal["headline", "stat", "story"]
TalkingPointStatus = Literal["new", "approved", "dismissed", "pinned"]
LowerThirdTemplate = Literal["score_update", "team_compare", "player_card", "momentum"]
RecapScope = Literal["H1", "FULL"]


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _sorted_unique(ids: tuple[str, ...], what: str) -> None:
    if tuple(sorted(set(ids))) != ids:
        raise ValueError(f"{what} must be unique and sorted by seq")


# --------------------------------------------------------------------------- request / context


class ClaimRequest(Contract):
    match_id: str = Field(pattern=MATCH_ID_PATTERN, max_length=64)
    text: str = Field(min_length=3, max_length=MAX_CLAIM_CHARS)
    language: Literal["auto", "vi", "en"] = "auto"
    as_of_seq: int | None = Field(default=None, ge=0)  # None = latest event when received
    include_trace: bool = False

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if len(value.strip()) < 3:
            raise ValueError("claim text needs at least 3 non-space characters")
        return value


class TeamRef(Contract):
    id: TeamId
    name: str
    short_name: str
    side: Side
    aliases: tuple[str, ...]


class PlayerRef(Contract):
    id: PlayerId
    team: TeamId
    shirt: int
    name: str
    short_name: str
    aliases: tuple[str, ...]


class MetricRef(Contract):
    name: str
    scope: Scope
    unit: Unit
    supports_player: bool
    polarity: Literal[1, -1]  # -1: a lower value is "better / stronger" (PPDA)
    description_vi: str
    description_en: str


class MatchContext(Contract):
    """What an agent may see about the match at `as_of_seq`: names, score, clock, catalog.

    No statistics: every number an agent needs comes from a tool call.
    """

    match_id: str
    as_of_seq: int  # -1 before the first event
    as_of_ms: int  # played time (match_ms) of the last event
    as_of_clock_ms: int  # clock of the last event within its period
    period: int  # 0 before kick-off
    clock_display: str
    finished: bool
    teams: tuple[TeamRef, ...]  # home first
    players: tuple[PlayerRef, ...]
    score: dict[str, int]
    metrics: tuple[MetricRef, ...]

    def team(self, team_id: str) -> TeamRef:
        for team in self.teams:
            if team.id == team_id:
                return team
        raise KeyError(team_id)

    def player(self, player_id: str) -> PlayerRef:
        for player in self.players:
            if player.id == player_id:
                return player
        raise KeyError(player_id)

    def opponent(self, team_id: str) -> str:
        home, away = self.teams[0].id, self.teams[1].id
        if team_id not in (home, away):
            raise KeyError(team_id)
        return away if team_id == home else home

    def metric(self, name: str) -> MetricRef:
        for ref in self.metrics:
            if ref.name == name:
                return ref
        raise KeyError(name)


class ClaimContext(Contract):
    """ClaimIntake -> ClaimAnalyst: the claim pinned to `match.as_of_seq`."""

    claim_id: str
    request: ClaimRequest
    text: str  # NFC-normalised claim text
    language: Lang
    match: MatchContext


# --------------------------------------------------------------------------- claim spec


class Subject(Contract):
    kind: SubjectKind
    team: TeamId | None = None
    player: PlayerId | None = None

    @model_validator(mode="after")
    def _check_subject(self) -> Self:
        if self.kind == "team" and (self.team is None or self.player is not None):
            raise ValueError("a team subject needs `team` and no `player`")
        if self.kind == "player":
            if self.player is None:
                raise ValueError("a player subject needs `player`")
            if self.team is not None and team_of_player(self.player) != self.team:
                raise ValueError(f"player {self.player} does not play for {self.team}")
        if self.kind == "both_teams" and (self.team is not None or self.player is not None):
            raise ValueError("a both_teams subject takes no team or player")
        return self

    @property
    def team_id(self) -> str | None:
        """The team of a team or player subject."""
        if self.player is not None:
            return team_of_player(self.player)
        return self.team


class Assertion(Contract):
    kind: AssertionKind
    subject: Subject
    other: Subject | None = None  # compare: defaults to the subject's opponent
    metric: str | None = None  # REGISTRY name (compare / value)
    op: Op | None = None  # "~" = approximately equal
    value: float | None = None  # value: the number that was said, in the metric's unit
    ratio: float | None = None  # "twice as many" -> 2.0
    window: Window
    negated: bool = False
    approx: bool = False
    text_span: str = ""

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if self.kind == "dominance":
            extra = [
                name
                for name in ("other", "metric", "op", "value", "ratio")
                if getattr(self, name) is not None
            ]
            if extra:
                raise ValueError(f"a dominance assertion takes no {', '.join(extra)}")
        elif self.kind == "compare":
            if self.metric is None or self.op is None:
                raise ValueError("a compare assertion needs `metric` and `op`")
            if self.value is not None:
                raise ValueError("a compare assertion takes no `value`")
        else:
            if self.metric is None or self.op is None or self.value is None:
                raise ValueError("a value assertion needs `metric`, `op` and `value`")
            if self.other is not None or self.ratio is not None:
                raise ValueError("a value assertion takes no `other` or `ratio`")
        return self


class ClaimSpec(Contract):
    spec_version: Literal["1"] = SPEC_VERSION
    language: Lang
    assertions: tuple[Assertion, ...] = Field(default=(), max_length=MAX_ASSERTIONS)
    unsupported_reason: ReasonCode | None = None  # set iff `assertions` is empty
    notes: tuple[str, ...] = ()  # which rules matched / a short LLM note

    @model_validator(mode="after")
    def _check_reason(self) -> Self:
        if not self.assertions and self.unsupported_reason in (None, "ok"):
            raise ValueError("an empty spec needs an unsupported_reason")
        if self.assertions and self.unsupported_reason is not None:
            raise ValueError("unsupported_reason is only for an empty spec")
        return self


class AnalystOutcome(Contract):
    """ClaimAnalyst -> RuleAnalyst: the LLM spec, or why there is none."""

    context: ClaimContext
    spec: ClaimSpec | None
    failure: Literal["timeout", "error", "invalid_output", "circuit_open", "budget"] | None = None


class ClaimAnalysis(Contract):
    """ClaimAnalyst / RuleAnalyst -> EvidenceCollector."""

    context: ClaimContext
    spec: ClaimSpec
    source: Literal["llm", "rule"]


# --------------------------------------------------------------------------- evidence and judgement


class StatQuery(Contract):
    metric: str
    team: TeamId | None = None
    player: PlayerId | None = None
    window: Window


class ToolCall(Contract):
    tool: str
    args: dict[str, Any]
    result: StatResult
    duration_ms: float


class EvidenceBundle(Contract):
    """EvidenceCollector -> Judge: every Stats Tools call made for the spec."""

    analysis: ClaimAnalysis
    tool_calls: tuple[ToolCall, ...]


class Fact(Contract):
    """A number that may be said out loud. Shared by the claim verifier, moments and Day 3."""

    key: str  # "possession:harbor_lions:H2"
    label_vi: str
    label_en: str
    value: float
    unit: Unit
    display: str  # Day 1 display(), or the derived-fact format
    evidence_event_ids: tuple[str, ...]
    derived: bool = False  # difference / ratio computed by the Judge from two facts
    metric: str | None = None
    team: TeamId | None = None
    player: PlayerId | None = None

    @model_validator(mode="after")
    def _check_fact(self) -> Self:
        _sorted_unique(self.evidence_event_ids, "evidence ids")
        if not self.display:
            raise ValueError("a fact needs a display string")
        return self


class AssertionResult(Contract):
    assertion: Assertion
    outcome: Outcome  # after negation
    facts: tuple[Fact, ...]
    rationale_code: str
    reason_code: ReasonCode = "ok"  # why an INSUFFICIENT outcome is insufficient

    @model_validator(mode="after")
    def _check_reason(self) -> Self:
        if (self.outcome == "INSUFFICIENT") == (self.reason_code == "ok"):
            raise ValueError("reason_code is set iff the outcome is INSUFFICIENT")
        return self


class JudgedClaim(Contract):
    """Judge -> Explainer."""

    claim_id: str
    text: str
    context: MatchContext
    spec: ClaimSpec
    verdict: Verdict
    reason_code: ReasonCode
    results: tuple[AssertionResult, ...]
    headline: tuple[Fact, ...] = Field(max_length=MAX_HEADLINE)

    @property
    def language(self) -> Lang:
        return self.spec.language

    def all_facts(self) -> tuple[Fact, ...]:
        seen: dict[str, Fact] = {}
        for result in self.results:
            for fact in result.facts:
                seen.setdefault(fact.key, fact)
        return tuple(seen.values())


# --------------------------------------------------------------------------- explanation, guardrail


class ExplanationDraft(Contract):
    text: str
    language: Lang
    source: Literal["llm", "template"]
    attempt: int = Field(ge=1)


class GuardrailViolation(Contract):
    code: ViolationCode
    detail: str
    span: str | None = None


class GuardrailReport(Contract):
    passed: bool
    violations: tuple[GuardrailViolation, ...] = ()

    @model_validator(mode="after")
    def _check_report(self) -> Self:
        if self.passed == bool(self.violations):
            raise ValueError("passed iff there are no violations")
        return self


class ExplainRequest(Contract):
    """Judge / Guardrail -> Explainer (attempt 2 carries the guardrail feedback)."""

    judged: JudgedClaim
    attempt: int = Field(ge=1)
    feedback: tuple[GuardrailViolation, ...] = ()


class TemplateRequest(Contract):
    """-> TemplateExplainer: INSUFFICIENT_DATA, LLM unavailable, or guardrail retries used up."""

    judged: JudgedClaim
    reason: Literal["insufficient", "llm_unavailable", "llm_failed", "guardrail_exhausted"]


class DraftForReview(Contract):
    """Explainer / TemplateExplainer -> Guardrail."""

    judged: JudgedClaim
    draft: ExplanationDraft
    content_filtered: bool = False  # the provider blocked the text (counts as UNSAFE_CONTENT)


class Publication(Contract):
    """Guardrail -> Publisher: the explanation to publish (empty if even the template failed)."""

    judged: JudgedClaim
    draft: ExplanationDraft
    report: GuardrailReport


# --------------------------------------------------------------------------- trace and result


class TraceSpan(Contract):
    trace_id: str  # = claim_id
    span_id: str  # f"{claim_id}-s{k:02d}", deterministic
    parent_id: str | None
    agent: str  # "ClaimAnalyst", "EvidenceCollector", ...
    kind: SpanKind
    name: str  # e.g. the tool name "get_stat"
    input: dict[str, Any]  # summary, at most 2 KB
    output: dict[str, Any]
    status: SpanStatus
    attempt: int
    start_ms: float  # from the start of the claim, monotonic clock
    duration_ms: float
    model: str | None  # "mock" | deployment name
    error: str | None


class ClaimResult(Contract):
    """Returned by POST /claims/verify and broadcast on the WebSocket (without trace)."""

    claim_id: str
    match_id: str
    as_of_seq: int
    as_of_display: str
    text: str
    language: Lang
    verdict: Verdict
    reason_code: ReasonCode
    assertions: tuple[AssertionResult, ...]
    headline: tuple[Fact, ...]
    explanation: str
    explanation_source: Literal["llm", "template"]
    analyst_source: Literal["llm", "rule"]
    model: str  # "mock" or the LLM deployment that was configured
    evidence_event_ids: tuple[str, ...]  # union of every fact's evidence, sorted, unique
    guardrail: GuardrailReport  # report of the published explanation
    degraded: tuple[Degraded, ...]
    timings_ms: dict[str, float]
    trace: tuple[TraceSpan, ...] | None = None

    @model_validator(mode="after")
    def _check_result(self) -> Self:
        _sorted_unique(self.evidence_event_ids, "evidence ids")
        return self


# --------------------------------------------------------------------------- moments and websocket


class Moment(Contract):
    id: str  # f"{match_id}-m{n:04d}"
    match_id: str
    kind: MomentKind
    importance: Literal[1, 2, 3]
    team: TeamId | None
    player: PlayerId | None
    period: int
    clock_ms: int
    display_minute: str
    trigger_event_ids: tuple[str, ...]
    facts: tuple[Fact, ...]
    seq: int  # seq of the event that triggered the moment
    related_moment_ids: tuple[str, ...] = ()  # goal_overturned -> the goal moment


class WsMessage(Contract):
    v: Literal[1] = WS_VERSION
    type: WsType
    n: int = Field(ge=0)  # increases by one per message within a session
    match_id: str
    payload: dict[str, Any]


# --------------------------------------------------------------------------- studio (Day 3)


class Cue(Contract):
    """A commentary-worthy event that is not a moment (docs/day3-design.md section 4)."""

    id: str  # f"{match_id}-q{n:04d}", numbered in detection order
    match_id: str
    kind: CueKind
    team: TeamId | None
    player: PlayerId | None
    period: int
    clock_ms: int
    display_minute: str
    seq: int
    trigger_event_ids: tuple[str, ...]
    facts: tuple[Fact, ...]


class Trigger(Contract):
    """A moment or a cue, as the studio crew sees it."""

    id: str  # = moment.id or cue.id
    match_id: str
    source: Literal["moment", "cue"]
    kind: str  # MomentKind | CueKind
    importance: Literal[1, 2, 3]
    team: TeamId | None
    player: PlayerId | None
    period: int
    clock_ms: int
    match_ms: int
    display_minute: str
    seq: int
    trigger_event_ids: tuple[str, ...]
    facts: tuple[Fact, ...]
    rules: tuple[RuleId, ...] = ()
    outcome: EventOutcome | None = None
    roles: dict[str, PlayerId] = Field(default_factory=dict)  # "scorer" -> player id, ...
    details: dict[str, str] = Field(default_factory=dict)  # "situation" -> "penalty", ...
    related_ids: tuple[str, ...] = ()  # goal_overturned -> the goal moment

    @property
    def involved_players(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.roles.values())))


class FactPack(Contract):
    """The verified numbers of one trigger, computed on the view pinned at its seq."""

    trigger: Trigger
    match: MatchContext  # names, score and clock at as_of_seq (= trigger.seq)
    facts: tuple[Fact, ...]
    headline: tuple[str, ...] = ()  # keys of the 1-3 facts to say first
    window_numbers: tuple[float, ...] = ()  # bounds and lengths of the windows used
    first_rules: tuple[RuleId, ...] = ()  # rules seen for the first time in the match

    @property
    def as_of_seq(self) -> int:
        return self.match.as_of_seq


class TalkingLine(Contract):
    text: str
    angle: TalkingAngle
    fact_keys: tuple[str, ...] = ()


class TalkingPoint(Contract):
    id: str  # f"{trigger.id}-tp"
    match_id: str
    trigger_id: str
    trigger_kind: str
    importance: Literal[2, 3]
    period: int
    clock_ms: int
    display_minute: str
    as_of_seq: int
    en: tuple[TalkingLine, ...] = Field(min_length=2, max_length=3)
    vi: tuple[TalkingLine, ...] = Field(min_length=2, max_length=3)
    source_en: ItemSource
    source_vi: ItemSource
    model: str
    facts: tuple[Fact, ...]
    evidence_event_ids: tuple[str, ...]
    degraded: tuple[Degraded, ...] = ()
    status: TalkingPointStatus = "new"
    trace: tuple[TraceSpan, ...] | None = None


class GraphicStat(Contract):
    key: str
    label_vi: str
    label_en: str
    home: str | None = None  # displays, for a two-team template
    away: str | None = None
    value: str | None = None  # display, for a one-side template
    highlight: Literal["home", "away"] | None = None


class TeamBadge(Contract):
    id: TeamId
    name: str
    short_name: str
    primary_color: str
    secondary_color: str


class LowerThird(Contract):
    id: str  # f"{trigger.id}-lt"
    match_id: str
    trigger_id: str
    template: LowerThirdTemplate
    title_vi: str = Field(max_length=32)
    title_en: str = Field(max_length=32)
    subtitle_vi: str = Field(max_length=48)
    subtitle_en: str = Field(max_length=48)
    stats: tuple[GraphicStat, ...] = Field(min_length=1, max_length=4)
    home: TeamBadge
    away: TeamBadge
    player: PlayerId | None = None
    period: int
    clock_ms: int
    match_clock: str
    show_at_match_ms: int
    duration_ms: int  # display time at 1x
    priority: Literal[1, 2, 3]
    supersedes: tuple[str, ...] = ()
    facts: tuple[Fact, ...]
    evidence_event_ids: tuple[str, ...]
    source: Literal["rule", "llm"]
    synthetic: Literal[True] = True
    disclaimer: str


class CommentaryText(Contract):
    language: Lang
    mode: Register
    text: str
    source: ItemSource


class CommentaryItem(Contract):
    """One line of commentary in its four versions (vi / en x analyst / casual)."""

    id: str  # f"{trigger.id}-c"
    match_id: str
    trigger_id: str
    trigger_kind: str
    importance: Literal[1, 2, 3]
    period: int
    clock_ms: int
    display_minute: str
    as_of_seq: int
    team: TeamId | None
    player: PlayerId | None
    involved_players: tuple[PlayerId, ...] = ()
    texts: tuple[CommentaryText, ...] = Field(min_length=4, max_length=4)
    facts: tuple[Fact, ...]
    evidence_event_ids: tuple[str, ...]
    rules: tuple[RuleId, ...] = ()
    first_rules: tuple[RuleId, ...] = ()
    excitement: Literal[0, 1, 2, 3] = 0
    speak: bool = False
    degraded: tuple[Degraded, ...] = ()

    def text(self, language: Lang, mode: Register) -> CommentaryText:
        for item in self.texts:
            if item.language == language and item.mode == mode:
                return item
        raise KeyError((language, mode))


class RecapText(Contract):
    language: Lang
    mode: Register
    title: str
    paragraphs: tuple[str, ...] = Field(min_length=1, max_length=6)
    source: ItemSource


class Recap(Contract):
    id: str  # f"{match_id}-recap-{scope}"
    match_id: str
    scope: RecapScope
    as_of_seq: int
    texts: tuple[RecapText, ...] = Field(min_length=4, max_length=4)
    key_moment_ids: tuple[str, ...] = ()
    stats: tuple[GraphicStat, ...] = ()
    facts: tuple[Fact, ...]
    evidence_event_ids: tuple[str, ...]
    revision: int = Field(default=1, ge=1)
    degraded: tuple[Degraded, ...] = ()

    def text(self, language: Lang, mode: Register) -> RecapText:
        for item in self.texts:
            if item.language == language and item.mode == mode:
                return item
        raise KeyError((language, mode))


class Profile(Contract):
    """Who is watching (Day 3 section 11): one per WebSocket connection."""

    mode: Register = "analyst"
    language: Lang = "vi"
    team: TeamId | None = None  # favourite club
    player: PlayerId | None = None  # player focus
    beginner: bool = False  # "Người mới xem"
    metric: str | None = None  # one metric only

    def key(self) -> str:
        beginner = "1" if self.beginner else "0"
        parts = (self.mode, self.language, self.team, self.player, beginner, self.metric)
        return "|".join(part or "-" for part in parts)


class FactView(Contract):
    label: str
    display: str


class RuleView(Contract):
    rule: RuleId
    title: str
    text: str
    depth: Literal["full", "short"]


class CommentaryView(Contract):
    """Payload of the WebSocket message `commentary`: one item for one profile."""

    id: str
    trigger_id: str
    kind: str
    period: int
    clock_ms: int
    display_minute: str
    importance: Literal[1, 2, 3]
    language: Lang
    mode: Register
    text: str
    framing: str | None = None
    emphasis: Literal["own_team", "opponent", "neutral", "focus_player"] = "neutral"
    relevance: Literal["high", "normal", "low"] = "normal"
    facts: tuple[FactView, ...] = ()
    explanation: RuleView | None = None
    player_tracker: tuple[FactView, ...] | None = None
    speak: bool = False
    excitement: int = 0
    source: ItemSource


class GraphicStatView(Contract):
    label: str
    home: str | None = None
    away: str | None = None
    value: str | None = None
    highlight: Literal["home", "away"] | None = None


class LowerThirdView(Contract):
    """Payload of `lower_third`: a lower-third in the profile's language."""

    id: str
    trigger_id: str
    template: LowerThirdTemplate
    language: Lang
    title: str
    subtitle: str
    stats: tuple[GraphicStatView, ...]
    home: TeamBadge
    away: TeamBadge
    player: PlayerId | None = None
    period: int
    clock_ms: int
    match_clock: str
    show_at_match_ms: int
    duration_ms: int
    priority: Literal[1, 2, 3]
    supersedes: tuple[str, ...] = ()
    evidence_event_ids: tuple[str, ...]
    source: Literal["rule", "llm"]
    synthetic: Literal[True] = True
    disclaimer: str


class RecapView(Contract):
    """Payload of `recap` and of GET /matches/{id}/recap."""

    id: str
    scope: RecapScope
    language: Lang
    mode: Register
    title: str
    paragraphs: tuple[str, ...]
    key_moment_ids: tuple[str, ...] = ()
    stats: tuple[GraphicStatView, ...] = ()
    revision: int
    source: ItemSource
    degraded: tuple[Degraded, ...] = ()


# Model drafts: what an LLM port returns, before the Guardrail


class TalkingDraft(Contract):
    en: tuple[str, ...]
    vi: tuple[str, ...]


class CommentaryDraft(Contract):
    analyst: str  # Vietnamese
    casual: str


class GraphicsOptions(Contract):
    templates: tuple[LowerThirdTemplate, ...]
    stat_keys: tuple[str, ...]  # candidate fact keys


class GraphicsChoice(Contract):
    template: LowerThirdTemplate
    stat_keys: tuple[str, ...]


class RecapDraft(Contract):
    title: str
    paragraphs: tuple[str, ...]


# Studio workflow messages


class StudioJob(Contract):
    trigger: Trigger
    first_rules: tuple[RuleId, ...] = ()  # rules met for the first time in the match


class StudioPlan(Contract):
    """StudioIntake -> FactPackBuilder: the job and the branches that apply to it."""

    job: StudioJob
    branches: tuple[StudioBranch, ...]


class BranchResult(Contract):
    branch: StudioBranch
    talking_point: TalkingPoint | None = None
    lower_third: LowerThird | None = None
    commentary: CommentaryItem | None = None


class StudioOutput(Contract):
    trigger_id: str
    commentary: CommentaryItem
    talking_point: TalkingPoint | None = None
    lower_third: LowerThird | None = None
    degraded: tuple[Degraded, ...] = ()
    timings_ms: dict[str, float] = Field(default_factory=dict)
    trace: tuple[TraceSpan, ...] | None = None
