"""The MCP tools as plain functions (docs/day6-design.md section 3.3).

A source, the arguments a model sent, an output model. Nothing here knows the MCP SDK: server.py
registers these functions; tests call them directly and through an in-memory client. Every number
comes from the Stats Tools registry (one definition, design D1); nothing changes any state (D2).
"""

import itertools
from collections.abc import Callable, Sequence

from pundit.agents.context import build_match_context
from pundit.agents.contracts import MAX_CLAIM_CHARS, ClaimRequest, MatchContext
from pundit.domain.errors import PunditError
from pundit.domain.events import EVENT_TYPES, Event
from pundit.domain.ids import team_of_player
from pundit.domain.matchtime import format_minute
from pundit.llm.settings import Settings
from pundit.mcp_server.errors import ToolInputError
from pundit.mcp_server.outputs import (
    AsOf,
    AssertionOut,
    ClaimOut,
    ComparisonOut,
    EventOut,
    EventsOut,
    MatchesOut,
    MetricOut,
    MetricsOut,
    MomentOut,
    MomentsOut,
    PlayerOut,
    PlayersOut,
    Scalar,
    ScoreboardOut,
    StatOut,
    TeamScore,
    fact_out,
    stat_value,
)
from pundit.mcp_server.params import (
    WindowArg,
    metric_spec,
    resolve_player,
    resolve_team,
    resolve_window,
)
from pundit.mcp_server.sources import MatchSource, ResolvedView, StaticMatches
from pundit.orchestration.service import ClaimService
from pundit.stats import REGISTRY, Comparison, MetricParams, StatResult, compare, compute

MAX_EVENTS = 50
DEFAULT_LAST = 20
MAX_MOMENTS = 50
MAX_CLAIM_EVIDENCE = 50
HIDDEN_EVENTS = frozenset({"player_physical"})  # never listed (as the API's panels)
_BASE_FIELDS = frozenset(
    {"type", "id", "match_id", "seq", "period", "clock_ms", "match_ms", "minute", "second",
     "team", "player", "x", "y", "possession_id", "possession_team"}
)  # fmt: skip
_claim_numbers = itertools.count(1)


def rules_only(settings: Settings | None = None) -> ClaimService:
    """The claim verifier of MCP: the rule analyst and the templates, never an LLM (design D3)."""
    base = settings or Settings()
    return ClaimService(
        base.model_copy(
            update={
                "use_mock_llm": True,
                "llm_provider": None,
                "mock_llm_faults": "",
                "mock_llm_latency_ms": 0.0,
            }
        )
    )


def _names(context: MatchContext) -> dict[str, str]:
    names = {team.id: team.name for team in context.teams}
    names.update({player.id: player.name for player in context.players})
    return names


def _as_of(resolved: ResolvedView) -> AsOf:
    return AsOf(seq=resolved.as_of_seq, minute=resolved.as_of_minute)


def _checked[T](action: str, run: Callable[[], T]) -> T:
    try:
        return run()
    except ToolInputError:
        raise
    except (ValueError, PunditError) as exc:  # a scope, team or player the registry refuses
        raise ToolInputError(f"{action}: {exc}") from None


async def list_matches(source: MatchSource) -> MatchesOut:
    return MatchesOut(live=source.live(), scenarios=StaticMatches.scenarios())


def list_metrics(scope: str | None = None) -> MetricsOut:
    if scope is not None and scope not in ("team", "match", "player"):
        raise ToolInputError(f"scope {scope!r}: use team, match or player")
    specs = sorted(
        (spec for spec in REGISTRY.values() if scope is None or spec.scope == scope),
        key=lambda spec: spec.name,
    )
    return MetricsOut(
        metrics=[
            MetricOut(
                name=spec.name,
                unit=spec.unit,
                scope=spec.scope,
                supports_player=spec.supports_player,
                definition=spec.definition,
                description_en=spec.description_en,
                description_vi=spec.description_vi,
            )
            for spec in specs
        ]
    )


async def get_stat(
    source: MatchSource,
    *,
    match_id: str,
    metric: str,
    team: str | None = None,
    player: str | None = None,
    window: WindowArg | None = None,
    as_of: str | None = None,
) -> StatOut:
    resolved = await source.resolve(match_id, as_of)
    context = build_match_context(resolved.view)
    spec = metric_spec(metric)
    team_id = resolve_team(context, team)
    player_id = resolve_player(context, player)
    if spec.scope == "team" and team_id is None:
        if player_id is None:
            raise ToolInputError(
                f"{spec.name} is a team metric: give a team, or use compare_stat for both teams"
            )
        team_id = team_of_player(player_id)  # a team metric filtered on a player: their team
    params = MetricParams(
        team=team_id, player=player_id, window=resolve_window(window, resolved.view)
    )
    result: StatResult = _checked(spec.name, lambda: compute(resolved.view, spec.name, params))
    return StatOut(
        match_id=resolved.match_id,
        source=resolved.source,
        as_of=_as_of(resolved),
        stat=stat_value(result, _names(context)),
    )


async def compare_stat(
    source: MatchSource,
    *,
    match_id: str,
    metric: str,
    window: WindowArg | None = None,
    as_of: str | None = None,
) -> ComparisonOut:
    resolved = await source.resolve(match_id, as_of)
    spec = metric_spec(metric)
    if spec.scope != "team":
        raise ToolInputError(
            f"compare_stat compares the two teams; {spec.name} is a {spec.scope} metric "
            "(use get_stat)"
        )
    span = resolve_window(window, resolved.view)
    result: Comparison = _checked(spec.name, lambda: compare(resolved.view, spec.name, span))
    names = _names(build_match_context(resolved.view))
    return ComparisonOut(
        match_id=resolved.match_id,
        source=resolved.source,
        as_of=_as_of(resolved),
        metric=spec.name,
        window=span.label(),
        home=stat_value(result.home, names),
        away=stat_value(result.away, names),
        diff=result.diff,
        ratio=result.ratio,
    )


async def get_scoreboard(
    source: MatchSource, *, match_id: str, as_of: str | None = None
) -> ScoreboardOut:
    resolved = await source.resolve(match_id, as_of)
    context = build_match_context(resolved.view)
    home, away = context.teams
    return ScoreboardOut(
        match_id=resolved.match_id,
        source=resolved.source,
        as_of=_as_of(resolved),
        period=context.period,
        finished=context.finished,
        home=TeamScore(id=home.id, name=home.name, short_name=home.short_name,
                       goals=context.score.get(home.id, 0)),
        away=TeamScore(id=away.id, name=away.name, short_name=away.short_name,
                       goals=context.score.get(away.id, 0)),
    )  # fmt: skip


def _scalar(value: object) -> Scalar | None:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, tuple | list) and all(isinstance(v, str) for v in value):
        return ",".join(value)
    return None


def event_out(event: Event, names: dict[str, str]) -> EventOut:
    details: dict[str, Scalar] = {}
    for key, value in event.model_dump(mode="json").items():
        if key in _BASE_FIELDS:
            continue
        scalar = _scalar(value)
        if scalar is not None and scalar != "":
            details[key] = scalar
    return EventOut(
        id=event.id,
        minute=format_minute(event.period, event.clock_ms),
        period=event.period,
        type=event.type,
        team=event.team,
        player=event.player,
        player_name=names.get(event.player) if event.player else None,
        x=event.x,
        y=event.y,
        details=details,
    )


async def get_events(
    source: MatchSource,
    *,
    match_id: str,
    ids: Sequence[str] | None = None,
    last: int | None = None,
    types: Sequence[str] | None = None,
    as_of: str | None = None,
) -> EventsOut:
    if ids and last is not None:
        raise ToolInputError("give ids or last, not both")
    if types:
        unknown = sorted(set(types) - set(EVENT_TYPES))
        if unknown:
            raise ToolInputError(f"unknown event types {unknown}; known: {', '.join(EVENT_TYPES)}")
    resolved = await source.resolve(match_id, as_of)
    view = resolved.view
    names = _names(build_match_context(view))
    missing: list[str] = []
    truncated = False
    if ids:
        asked = list(dict.fromkeys(ids))
        if len(asked) > MAX_EVENTS:
            raise ToolInputError(f"at most {MAX_EVENTS} ids per call")
        found: list[Event] = []
        for event_id in asked:
            if view.has(event_id) and view.get(event_id).type not in HIDDEN_EVENTS:
                found.append(view.get(event_id))
            else:
                missing.append(event_id)
    else:
        count = DEFAULT_LAST if last is None else last
        if not 1 <= count <= MAX_EVENTS:
            raise ToolInputError(f"last must be between 1 and {MAX_EVENTS}")
        wanted = set(types) if types else None
        picked: list[Event] = []
        for event in reversed(list(view)):
            if event.type in HIDDEN_EVENTS or (wanted is not None and event.type not in wanted):
                continue
            if len(picked) == count:
                truncated = True
                break
            picked.append(event)
        found = list(reversed(picked))
    return EventsOut(
        match_id=resolved.match_id,
        source=resolved.source,
        as_of=_as_of(resolved),
        events=[event_out(event, names) for event in found],
        missing=missing,
        truncated=truncated,
    )


async def get_moments(
    source: MatchSource,
    *,
    match_id: str,
    as_of: str | None = None,
    min_importance: int = 2,
    limit: int = 30,
) -> MomentsOut:
    if not 1 <= min_importance <= 3:
        raise ToolInputError("min_importance is 1 (everything), 2 or 3 (goals, red cards, VAR)")
    if not 1 <= limit <= MAX_MOMENTS:
        raise ToolInputError(f"limit must be between 1 and {MAX_MOMENTS}")
    resolved = await source.resolve(match_id, as_of)
    names = _names(build_match_context(resolved.view))
    chosen = [m for m in resolved.moments if m.importance >= min_importance]
    kept = chosen[-limit:]  # the latest, in match order
    return MomentsOut(
        match_id=resolved.match_id,
        source=resolved.source,
        as_of=_as_of(resolved),
        moments=[
            MomentOut(
                id=moment.id,
                minute=moment.display_minute,
                kind=moment.kind,
                importance=moment.importance,
                team=moment.team,
                player=moment.player,
                player_name=names.get(moment.player) if moment.player else None,
                trigger_event_ids=list(moment.trigger_event_ids),
                facts=[fact_out(fact) for fact in moment.facts],
            )
            for moment in kept
        ],
        truncated=len(chosen) > len(kept),
    )


async def list_players(
    source: MatchSource, *, match_id: str, team: str | None = None
) -> PlayersOut:
    resolved = await source.resolve(match_id, None)
    context = build_match_context(resolved.view)
    team_id = resolve_team(context, team)
    return PlayersOut(
        match_id=resolved.match_id,
        players=[
            PlayerOut(id=p.id, name=p.name, short_name=p.short_name, team=p.team, shirt=p.shirt)
            for p in context.players
            if team_id is None or p.team == team_id
        ],
    )


async def verify_claim(
    source: MatchSource,
    service: ClaimService,
    *,
    match_id: str,
    text: str,
    as_of: str | None = None,
) -> ClaimOut:
    """The claim verifier with the rules and templates: read-only, nothing is published (D2)."""
    if not 3 <= len(text.strip()) <= MAX_CLAIM_CHARS:
        raise ToolInputError(f"a claim has 3 to {MAX_CLAIM_CHARS} characters")
    resolved = await source.resolve(match_id, as_of)
    data_id = resolved.view.meta.match_id
    request = ClaimRequest(match_id=data_id, text=text, as_of_seq=resolved.as_of_seq)
    claim_id = f"{data_id}-mcp{next(_claim_numbers):05d}"
    result = await service.verify(request, resolved.claims, claim_id=claim_id)
    return ClaimOut(
        match_id=resolved.match_id,
        source=resolved.source,
        as_of=AsOf(seq=result.as_of_seq, minute=result.as_of_display),
        claim=result.text,
        language=result.language,
        verdict=result.verdict,
        reason_code=result.reason_code,
        assertions=[
            AssertionOut(
                kind=part.assertion.kind,
                metric=part.assertion.metric,
                window=part.assertion.window.label(),
                said=part.assertion.text_span,
                outcome=part.outcome,
                reason_code=part.reason_code,
                facts=[fact_out(fact) for fact in part.facts],
            )
            for part in result.assertions
        ],
        headline=[fact_out(fact) for fact in result.headline],
        explanation=result.explanation,
        evidence_event_ids=list(result.evidence_event_ids[:MAX_CLAIM_EVIDENCE]),
        evidence_total=len(result.evidence_event_ids),
    )
