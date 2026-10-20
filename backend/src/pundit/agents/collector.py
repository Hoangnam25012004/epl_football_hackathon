"""EvidenceCollector: ClaimSpec -> Stats Tools calls (docs/day2-design.md section 5.1).

The only place where the claim verifier reads statistics. Every call goes through `get_stat`,
which is also the `get_stat` tool of the catalog (orchestration/tools.py) and of the Day 6 MCP
server, so a tool call recorded here looks exactly like one made by an LLM agent.
"""

import time
from collections.abc import Callable
from typing import Any

from pundit.agents.contracts import (
    Assertion,
    ClaimAnalysis,
    EvidenceBundle,
    MatchContext,
    StatQuery,
    ToolCall,
)
from pundit.domain.log import LogView
from pundit.domain.matchtime import MinuteWindow, RollingWindow
from pundit.stats import MetricParams, StatResult, compute

DOMINANCE_METRICS = ("possession", "shots", "xg", "momentum")
COVERAGE_METRIC = "ball_in_play_time"  # its evidence proves the window had play

QueryKey = tuple[str, str | None, str | None, str]


def query_key(query: StatQuery) -> QueryKey:
    return (query.metric, query.team, query.player, query.window.model_dump_json())


def stat_key(
    metric: str, team: str | None, player: str | None, window: MinuteWindow | RollingWindow
) -> QueryKey:
    return (metric, team, player, window.model_dump_json())


def tool_args(query: StatQuery) -> dict[str, Any]:
    args: dict[str, Any] = {"metric": query.metric, "window": query.window.model_dump(mode="json")}
    if query.team is not None:
        args["team"] = query.team
    if query.player is not None:
        args["player"] = query.player
    return args


def get_stat(
    view: LogView, query: StatQuery, *, clock: Callable[[], float] = time.perf_counter
) -> ToolCall:
    """The `get_stat` tool: one metric for one team / player / the match, in one window."""
    started = clock()
    result = compute(
        view,
        query.metric,
        MetricParams(team=query.team, player=query.player, window=query.window),
    )
    return ToolCall(
        tool="get_stat",
        args=tool_args(query),
        result=result,
        duration_ms=(clock() - started) * 1000,
    )


def _sides(assertion: Assertion, ctx: MatchContext) -> list[tuple[str | None, str | None]]:
    """(team, player) of every subject the assertion needs a value for.

    A player query also names the player's team: team metrics filtered to one player.
    """
    subject, other = assertion.subject, assertion.other
    if subject.kind == "both_teams":
        return [(team.id, None) for team in ctx.teams]
    first = (subject.team_id, subject.player)
    if assertion.kind == "value":
        return [first]
    if other is None:
        assert subject.team is not None
        other_side: tuple[str | None, str | None] = (ctx.opponent(subject.team), None)
    else:
        other_side = (other.team_id, other.player)
    return [first, other_side]


def plan_queries(assertions: tuple[Assertion, ...], ctx: MatchContext) -> list[StatQuery]:
    """Every stat the Judge needs, without duplicates, in a stable order."""
    planned: dict[QueryKey, StatQuery] = {}

    def add(metric: str, team: str | None, player: str | None, window: Any) -> None:
        query = StatQuery(metric=metric, team=team, player=player, window=window)
        planned.setdefault(query_key(query), query)

    for assertion in assertions:
        window = assertion.window
        add(COVERAGE_METRIC, None, None, window)
        if assertion.kind == "dominance":
            assert assertion.subject.team is not None
            for team in (assertion.subject.team, ctx.opponent(assertion.subject.team)):
                for metric in DOMINANCE_METRICS:
                    add(metric, team, None, window)
            continue
        assert assertion.metric is not None
        for side_team, side_player in _sides(assertion, ctx):
            add(assertion.metric, side_team, side_player, window)
    return list(planned.values())


def collect(
    analysis: ClaimAnalysis,
    view: LogView,
    *,
    call: Callable[[StatQuery], ToolCall] | None = None,
) -> EvidenceBundle:
    """Run every planned query. `call` lets the orchestrator wrap each call in a trace span."""
    run = call or (lambda query: get_stat(view, query))
    queries = plan_queries(analysis.spec.assertions, analysis.context.match)
    return EvidenceBundle(analysis=analysis, tool_calls=tuple(run(q) for q in queries))


def results_by_key(bundle: EvidenceBundle) -> dict[QueryKey, StatResult]:
    results: dict[QueryKey, StatResult] = {}
    for tool_call in bundle.tool_calls:
        args = tool_call.args
        window = tool_call.result.window
        key = stat_key(args["metric"], args.get("team"), args.get("player"), window)
        results[key] = tool_call.result
    return results
