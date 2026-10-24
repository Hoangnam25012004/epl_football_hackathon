"""The MCP server (docs/day6-design.md sections 3.3, 3.6 and 3.7).

The tools of tools.py on the MCP SDK: read-only (every tool says so in its annotations), a rate
limit per client, one log line per call (never the words of a claim). `pundit mcp` serves it on
stdio with generated matches; the API serves it on /mcp with its live sessions first
(api/mcp_mount.py). This is the only module that knows the SDK and its HTTP transport.
"""

import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from pundit.domain.clients import client_ip
from pundit.mcp_server import tools
from pundit.mcp_server.errors import ToolInputError
from pundit.mcp_server.limits import RateLimiter
from pundit.mcp_server.outputs import (
    DISCLAIMER,
    ClaimOut,
    ComparisonOut,
    EventsOut,
    MatchesOut,
    MetricsOut,
    MomentsOut,
    PlayersOut,
    ScoreboardOut,
    StatOut,
)
from pundit.mcp_server.params import WindowArg, as_of_text
from pundit.mcp_server.sources import MatchSource
from pundit.orchestration.service import ClaimService
from pundit.orchestration.tools import COMPARE_STAT, GET_STAT, catalog
from pundit.stats import REGISTRY

log = logging.getLogger("pundit.mcp")

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
VERIFY_COST = 3  # a claim runs the whole verifier: it counts as three calls
MAX_BODY_BYTES = 64 * 1024  # a claim is 300 characters; 50 event ids are about 1.5 KB
MCP_PATH = "/mcp"
# Every method is routed to the endpoint, so a catch-all mount (the UI) never answers /mcp with a
# 404, which a client would read as "session gone". Only POST is served: stateless, a GET would
# open an SSE stream that never carries anything and holds a connection for good; 405 is the
# protocol's answer for "no stream here".
ROUTE_METHODS = ["GET", "POST", "DELETE"]
ALLOW_POST = {"Allow": "POST"}
LOCAL = "local"
INSTRUCTIONS = (
    "Pundit Copilot: deterministic statistics and claim checks for synthetic football matches. "
    "Every number comes from these tools with the event ids it is computed from: quote them and "
    "never compute or invent a number yourself. Teams and players are fictional. Start with "
    "list_matches; a match id is a scenario and a seed (dominant_loser-42) or a live session."
)
ABOUT = (
    "Pundit Copilot turns synthetic football event data into explainable match intelligence: "
    "a claim checker for commentators, talking points, lower-thirds and Vietnamese commentary "
    "with a voice. These MCP tools are read-only and deterministic: statistics come from one "
    "registry of metrics, claims are checked by the rule analyst, and every number carries the "
    f"ids of the events it is computed from. {DISCLAIMER}"
)

MatchId = Annotated[
    str,
    Field(
        description="Match id from list_matches: a scenario and a seed (dominant_loser-42) "
        "or a live session id"
    ),
]
AsOf = Annotated[
    str | float | None,
    Field(
        description='Answer at this minute: "63\'" (or 63), "45+2\'", "HT" or "FT"; '
        "empty for the latest event"
    ),
]
Metric = Annotated[
    str,
    Field(description="Metric name (list_metrics)", json_schema_extra={"enum": sorted(REGISTRY)}),
]
Team = Annotated[
    str | None,
    Field(description="Team: id, short name (HAR), name, or home / away"),
]
Player = Annotated[str | None, Field(description="Player: id (harbor_lions-09) or name")]
Window = Annotated[
    WindowArg | None,
    Field(description="Time window; empty for the whole match (so far)"),
]
EventIds = Annotated[
    list[str] | None,
    Field(description="Event ids (at most 50), e.g. the evidence of a statistic"),
]
LastEvents = Annotated[int | None, Field(ge=1, le=50, description="The latest N events")]
EventTypes = Annotated[
    list[str] | None,
    Field(description="Only these event types: goal, shot, card, var_check, offside..."),
]
Importance = Annotated[
    int,
    Field(ge=1, le=3, description="3: goals, red cards, VAR; 2: also big chances and momentum"),
]
MomentLimit = Annotated[int, Field(ge=1, le=50, description="At most this many moments")]
ClaimText = Annotated[
    str,
    Field(min_length=3, max_length=300, description="What the commentator said (VI or EN)"),
]


@dataclass
class McpStats:
    calls: int = 0
    errors: int = 0
    refused: int = 0  # arguments the model can correct (ToolError), rate limits included
    by_tool: dict[str, int] = field(default_factory=dict)

    def view(self, limiter: RateLimiter | None) -> dict[str, object]:
        return {
            "calls": self.calls,
            "errors": self.errors,
            "refused": self.refused,
            "rate_limited": limiter.refused if limiter is not None else 0,
            "by_tool": dict(sorted(self.by_tool.items())),
        }


def client_key(ctx: Context, *, trust_proxy: bool = False) -> str:
    """The client's IP on HTTP (behind a trusted proxy, the hop it added); "local" in memory
    and on stdio."""
    try:
        request = ctx.request_context.request
    except (AttributeError, LookupError, RuntimeError, ValueError):
        return LOCAL
    if request is None:
        return LOCAL
    host = getattr(getattr(request, "client", None), "host", None)
    peer = host if isinstance(host, str) and host else None
    headers = getattr(request, "headers", None)
    forwarded = headers.get("x-forwarded-for") if headers is not None else None
    if peer is None and not (trust_proxy and forwarded):
        return LOCAL
    return client_ip(peer, forwarded, trust_proxy=trust_proxy)


def _package_version() -> str:
    try:
        return version("pundit")
    except PackageNotFoundError:  # pragma: no cover - always installed in the project
        return "0"


def build_server(
    source: MatchSource,
    *,
    claims: ClaimService | None = None,
    limiter: RateLimiter | None = None,
    trust_proxy: bool = False,
    name: str = "pundit",
) -> tuple[MCPServer, McpStats]:
    """The MCP server over `source`, and its counters (for /healthz)."""
    service = claims or tools.rules_only()
    stats = McpStats()
    server = MCPServer(
        name,
        title="Pundit Copilot",
        instructions=INSTRUCTIONS,
        version=_package_version(),
    )
    described = {entry["name"]: str(entry["description"]) for entry in catalog()}

    async def call[T](
        tool: str,
        ctx: Context,
        match_id: str | None,
        work: Callable[[], Awaitable[T]],
        *,
        cost: int = 1,
    ) -> T:
        started = time.perf_counter()
        status = "ok"
        stats.calls += 1
        stats.by_tool[tool] = stats.by_tool.get(tool, 0) + 1
        try:
            if limiter is not None:
                limiter.take(client_key(ctx, trust_proxy=trust_proxy), cost)
            return await work()
        except ToolInputError as exc:
            status = "refused"
            stats.refused += 1
            raise ToolError(str(exc)) from None
        except Exception:
            status = "error"
            stats.errors += 1
            raise  # the SDK answers a generic error; the traceback stays in this log
        finally:
            log.info(
                "mcp.call tool=%s match=%s ms=%.1f status=%s",
                tool,
                match_id or "-",
                (time.perf_counter() - started) * 1000,
                status,
            )

    @server.tool(title="List matches", annotations=READ_ONLY)
    async def list_matches(ctx: Context) -> MatchesOut:
        """Matches you can analyse: the live shared sessions and the generated scenarios (any
        seed works). Use their match_id in every other tool."""
        return await call("list_matches", ctx, None, lambda: tools.list_matches(source))

    @server.tool(title="List metrics", annotations=READ_ONLY)
    async def list_metrics(
        ctx: Context,
        scope: Literal["team", "match", "player"] | None = None,
    ) -> MetricsOut:
        """Every metric with its unit, scope (team, match or player) and definition."""

        async def work() -> MetricsOut:
            return tools.list_metrics(scope)

        return await call("list_metrics", ctx, None, work)

    @server.tool(
        name=GET_STAT,
        title="Get a statistic",
        annotations=READ_ONLY,
        description=described[GET_STAT],
    )
    async def get_stat(
        ctx: Context,
        match_id: MatchId,
        metric: Metric,
        team: Team = None,
        player: Player = None,
        window: Window = None,
        as_of: AsOf = None,
    ) -> StatOut:
        return await call(
            GET_STAT,
            ctx,
            match_id,
            lambda: tools.get_stat(
                source,
                match_id=match_id,
                metric=metric,
                team=team,
                player=player,
                window=window,
                as_of=as_of_text(as_of),
            ),
        )

    @server.tool(
        name=COMPARE_STAT,
        title="Compare the two teams",
        annotations=READ_ONLY,
        description=described[COMPARE_STAT],
    )
    async def compare_stat(
        ctx: Context,
        match_id: MatchId,
        metric: Metric,
        window: Window = None,
        as_of: AsOf = None,
    ) -> ComparisonOut:
        return await call(
            COMPARE_STAT,
            ctx,
            match_id,
            lambda: tools.compare_stat(
                source,
                match_id=match_id,
                metric=metric,
                window=window,
                as_of=as_of_text(as_of),
            ),
        )

    @server.tool(title="Scoreboard", annotations=READ_ONLY)
    async def get_scoreboard(
        ctx: Context,
        match_id: MatchId,
        as_of: AsOf = None,
    ) -> ScoreboardOut:
        """The score, the period and the minute, the two teams."""
        return await call(
            "get_scoreboard",
            ctx,
            match_id,
            lambda: tools.get_scoreboard(
                source,
                match_id=match_id,
                as_of=as_of_text(as_of),
            ),
        )

    @server.tool(title="Match events", annotations=READ_ONLY)
    async def get_events(
        ctx: Context,
        match_id: MatchId,
        ids: EventIds = None,
        last: LastEvents = None,
        types: EventTypes = None,
        as_of: AsOf = None,
    ) -> EventsOut:
        """Events by id (the evidence of a number) or the latest ones, optionally by type."""
        return await call(
            "get_events",
            ctx,
            match_id,
            lambda: tools.get_events(
                source,
                match_id=match_id,
                ids=ids,
                last=last,
                types=types,
                as_of=as_of_text(as_of),
            ),
        )

    @server.tool(title="Key moments", annotations=READ_ONLY)
    async def get_moments(
        ctx: Context,
        match_id: MatchId,
        as_of: AsOf = None,
        min_importance: Importance = 2,
        limit: MomentLimit = 30,
    ) -> MomentsOut:
        """The moments that matter (goals, cards, VAR, big chances, momentum shifts) with the
        facts and event ids behind them, in match order."""
        return await call(
            "get_moments",
            ctx,
            match_id,
            lambda: tools.get_moments(
                source,
                match_id=match_id,
                as_of=as_of_text(as_of),
                min_importance=min_importance,
                limit=limit,
            ),
        )

    @server.tool(title="Check a claim", annotations=READ_ONLY)
    async def verify_claim(
        ctx: Context,
        match_id: MatchId,
        text: ClaimText,
        as_of: AsOf = None,
    ) -> ClaimOut:
        """Fact-check a commentator's claim against the match: TRUE, PARTLY_TRUE, FALSE or
        INSUFFICIENT_DATA, with the numbers and event ids of each part. Rules only (no language
        model), so the same claim always gets the same verdict."""
        return await call(
            "verify_claim",
            ctx,
            match_id,
            lambda: tools.verify_claim(
                source,
                service,
                match_id=match_id,
                text=text,
                as_of=as_of_text(as_of),
            ),
            cost=VERIFY_COST,
        )

    @server.tool(title="List players", annotations=READ_ONLY)
    async def list_players(
        ctx: Context,
        match_id: MatchId,
        team: Team = None,
    ) -> PlayersOut:
        """Both squads (fictional players): id, name, team, shirt number."""
        return await call(
            "list_players",
            ctx,
            match_id,
            lambda: tools.list_players(
                source,
                match_id=match_id,
                team=team,
            ),
        )

    @server.resource("pundit://about", title="About Pundit Copilot", mime_type="text/plain")
    def about() -> str:
        """What this server is and where its data comes from."""
        return ABOUT

    @server.resource("pundit://metrics", title="Metric catalog", mime_type="application/json")
    def metrics() -> str:
        """Every metric with its definition (JSON)."""
        return tools.list_metrics().model_dump_json(indent=2)

    @server.prompt(title="Fact-check a claim")
    def fact_check(claim: str, match_id: str = "dominant_loser-42") -> str:
        """Check a commentator's claim with verify_claim and report the evidence."""
        return (
            f"Fact-check this claim about match {match_id}: {json.dumps(claim, ensure_ascii=False)}"
            ". Call verify_claim, then answer with the verdict, the numbers of each part exactly "
            "as the tool displays them, and a few event ids. Do not add any number of your own."
        )

    @server.prompt(title="Match briefing")
    def match_briefing(match_id: str = "dominant_loser-42", language: str = "en") -> str:
        """A short briefing for a commentator: score, key moments, three numbers."""
        return (
            f"Write a short briefing ({language}) on match {match_id} for a commentator. Call "
            "get_scoreboard, get_moments and compare_stat for possession, shots and xg. Quote "
            "every number as the tools display it and say that the data is synthetic."
        )

    return server, stats


def transport_security(hosts: Sequence[str]) -> TransportSecuritySettings:
    """Another Host is refused with 421 (DNS rebinding); a page of another origin with 403. The
    allowed hosts' own origins pass (MCP Inspector on localhost); a client with no Origin header
    (Copilot, Claude, curl) is never refused for it."""
    forms = [form for host in hosts for form in (host, f"{host}:*")]
    return TransportSecuritySettings(
        allowed_hosts=forms,
        allowed_origins=[f"{scheme}://{form}" for scheme in ("http", "https") for form in forms],
    )


def http_app(server: MCPServer, *, allowed_hosts: Sequence[str]) -> Starlette:
    """The Streamable HTTP app of `server` at /mcp (it runs the session manager itself).
    Stateless with JSON answers: the tools never notify, so no session is kept in memory and any
    replica can answer any request."""
    return server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        max_request_body_size=MAX_BODY_BYTES,
        transport_security=transport_security(allowed_hosts),
    )


class HttpEndpoint:
    """/mcp inside a host app (the API routes it at /mcp and /mcp/). The SDK's session manager
    runs once per instance, so each run of the host app (`running()` in its lifespan) builds a
    fresh one; outside a run a request gets 503 instead of a crash."""

    def __init__(self, server: MCPServer, *, allowed_hosts: Sequence[str]) -> None:
        self._server = server
        self._hosts = tuple(allowed_hosts)
        self._current: ASGIApp | None = None

    @contextlib.asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        app = http_app(self._server, allowed_hosts=self._hosts)
        endpoint = next(route.endpoint for route in app.routes if isinstance(route, Route))
        async with self._server.session_manager.run():
            self._current = endpoint
            try:
                yield
            finally:
                self._current = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("method") != "POST":
            refused = PlainTextResponse("Method Not Allowed", status_code=405, headers=ALLOW_POST)
            await refused(scope, receive, send)
            return
        current = self._current
        if current is None:
            await PlainTextResponse("MCP is not running", status_code=503)(scope, receive, send)
            return
        await current(scope, receive, send)


def standalone_app(server: MCPServer, *, allowed_hosts: Sequence[str]) -> Starlette:
    """`pundit mcp --http`: the endpoint alone at /mcp and /mcp/ (generated matches only)."""
    endpoint = HttpEndpoint(server, allowed_hosts=allowed_hosts)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with endpoint.running():
            yield

    routes = [
        Route(path, endpoint=endpoint, methods=ROUTE_METHODS) for path in (MCP_PATH, MCP_PATH + "/")
    ]
    return Starlette(routes=routes, lifespan=lifespan)


async def run_stdio(server: MCPServer) -> None:
    """Serve on stdin / stdout: stdout carries JSON-RPC only (logs go to stderr)."""
    await server.run_stdio_async()
