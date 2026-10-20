"""The Stats Tools catalog (design section 9.4): one definition for every consumer.

- the EvidenceCollector calls `get_stat` directly (agents/collector.py), so its trace looks like
  an agent's tool call;
- Day 3 agents receive `maf_tools(view)` as Microsoft Agent Framework function tools;
- the Day 6 MCP server will publish `catalog()`.
Results shown to a model list at most 10 evidence ids plus the total, to save tokens; the full
list stays in the ClaimResult. Tool names never change (contract for Day 3+).
"""

from typing import Any

from agent_framework import FunctionTool
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from pundit.agents.collector import get_stat
from pundit.agents.contracts import StatQuery, ToolCall
from pundit.domain.log import LogView
from pundit.domain.matchtime import FULL
from pundit.llm.dto import LlmWindow, to_window
from pundit.stats import REGISTRY, StatResult, display

GET_STAT = "get_stat"
COMPARE_STAT = "compare_stat"
MAX_EVIDENCE_FOR_LLM = 10


def _metric_field() -> Any:
    names: list[JsonValue] = [*sorted(REGISTRY)]
    return Field(description="Metric name from the catalog", json_schema_extra={"enum": names})


class GetStatArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str = _metric_field()
    team: str | None = Field(default=None, description="Team id (needed by team metrics)")
    player: str | None = Field(default=None, description="Player id (player metrics/filter)")
    window: LlmWindow | None = Field(default=None, description="Time window; null = whole match")


class CompareStatArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str = _metric_field()
    window: LlmWindow | None = Field(default=None, description="Time window; null = whole match")


def _query(view: LogView, args: GetStatArgs) -> StatQuery:
    last = view.last
    as_of_ms = last.match_ms if last is not None else 0
    window = to_window(args.window, as_of_ms, "window") if args.window is not None else FULL
    return StatQuery(metric=args.metric, team=args.team, player=args.player, window=window)


def llm_result(result: StatResult) -> dict[str, Any]:
    """What a model sees of a StatResult: value, display and a few evidence ids."""
    evidence = result.evidence_event_ids
    return {
        "metric": result.metric,
        "team": result.team,
        "player": result.player,
        "window": result.window.label(),
        "value": result.value,
        "display": display(result),
        "unit": result.unit,
        "status": result.status,
        "evidence_event_ids": list(evidence[:MAX_EVIDENCE_FOR_LLM]),
        "evidence_total": len(evidence),
    }


def run_get_stat(view: LogView, args: GetStatArgs) -> ToolCall:
    return get_stat(view, _query(view, args))


def run_compare_stat(view: LogView, args: CompareStatArgs) -> tuple[ToolCall, ToolCall]:
    home, away = view.meta.team_ids
    calls = [
        run_get_stat(view, GetStatArgs(metric=args.metric, team=team, window=args.window))
        for team in (home, away)
    ]
    return calls[0], calls[1]


def catalog() -> list[dict[str, Any]]:
    """Name, description and JSON Schema of every tool (for MCP and documentation)."""
    return [
        {
            "name": GET_STAT,
            "description": (
                "One deterministic match statistic for a team, a player or the whole match, in "
                "a time window. Returns the value, its display text and the event ids it is "
                "computed from."
            ),
            "input_schema": GetStatArgs.model_json_schema(),
        },
        {
            "name": COMPARE_STAT,
            "description": "The same statistic for both teams in a window (home first).",
            "input_schema": CompareStatArgs.model_json_schema(),
        },
    ]


def maf_tools(view: LogView) -> list[FunctionTool]:
    """Function tools bound to a pinned view, for Microsoft Agent Framework agents (Day 3)."""

    def get_stat_tool(**kwargs: Any) -> dict[str, Any]:
        return llm_result(run_get_stat(view, GetStatArgs.model_validate(kwargs)).result)

    def compare_stat_tool(**kwargs: Any) -> dict[str, Any]:
        home, away = run_compare_stat(view, CompareStatArgs.model_validate(kwargs))
        return {"home": llm_result(home.result), "away": llm_result(away.result)}

    spec = {entry["name"]: entry for entry in catalog()}
    return [
        FunctionTool(
            name=GET_STAT,
            description=str(spec[GET_STAT]["description"]),
            func=get_stat_tool,
            input_model=GetStatArgs,
        ),
        FunctionTool(
            name=COMPARE_STAT,
            description=str(spec[COMPARE_STAT]["description"]),
            func=compare_stat_tool,
            input_model=CompareStatArgs,
        ),
    ]
