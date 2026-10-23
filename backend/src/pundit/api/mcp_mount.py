"""MCP on /mcp of the API (docs/day6-design.md sections 3.2 and 3.7).

The tools read the API's sessions first, as far as each has been played, then generated
matches. A session is pinned at the start of a call: its replay goes on, the answer does not
move. The MCP SDK is optional at run time (design D8): if it cannot be imported the app runs
without /mcp and /healthz says so.
"""

import contextlib
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Literal

from fastapi import FastAPI
from starlette.routing import Route

from pundit.api.sessions import MatchSession, SessionManager
from pundit.llm.settings import Settings
from pundit.mcp_server.errors import ToolInputError
from pundit.mcp_server.limits import RateLimiter
from pundit.mcp_server.sources import (
    LiveEntry,
    ResolvedView,
    StaticMatches,
    check_match_id,
    seq_for,
)
from pundit.mcp_server.tools import rules_only

log = logging.getLogger("pundit.api")
MCP_PATH = "/mcp"
McpStatus = Literal["ok", "disabled", "unavailable"]


def pinned(session: MatchSession, match_id: str, as_of: str | None) -> ResolvedView:
    """The session at `as_of` (now by default). Synchronous on purpose: no replay step can
    come between reading the events and reading the moments."""
    view = session.state.view()
    complete = session.status == "finished"
    seq = seq_for(list(view), as_of, complete=complete)
    return ResolvedView(
        view=view if seq == view.last_seq else session.view_at(seq),
        source="live",
        match_id=match_id,
        moments=tuple(m for m in session.moments if m.seq <= seq),
        claims=session,  # read-only: the verifier reads views, nothing is published
        complete=complete,
    )


def live_entry(match_id: str, session: MatchSession) -> LiveEntry:
    snapshot = session.snapshot()
    home, away = session.meta.team_ids
    return LiveEntry(
        match_id=match_id,
        scenario=session.meta.scenario,
        seed=session.meta.seed,
        status=session.status,
        minute=snapshot.clock_display,
        score=f"{snapshot.score.get(home, 0)}-{snapshot.score.get(away, 0)}",
    )


class LiveSessions:
    """The API's sessions as an MCP source; a match id with no session is generated."""

    def __init__(self, manager: SessionManager, fallback: StaticMatches | None = None) -> None:
        self._manager = manager
        self._fallback = fallback or StaticMatches()

    async def resolve(self, match_id: str, as_of: str | None) -> ResolvedView:
        key = check_match_id(match_id)
        session = self._manager.get(key)
        if session is not None:
            return pinned(session, key, as_of)
        if "~" in key:
            raise ToolInputError(
                f"session {key!r} is not running: it has ended or never existed "
                "(list_matches lists the shared matches)"
            )
        return await self._fallback.resolve(key, as_of)

    def live(self) -> list[LiveEntry]:
        """The shared sessions: a private one (with a token) is never listed (section 6)."""
        return [
            live_entry(key, session)
            for key, session in self._manager.sessions.items()
            if "~" not in key
        ]


@dataclass
class McpMount:
    """What the app runs and reports of MCP."""

    status: McpStatus
    stats_view: Callable[[], dict[str, object]] | None = None
    run: Callable[[], AbstractAsyncContextManager[None]] = field(default=contextlib.nullcontext)

    def health(self) -> dict[str, object]:
        block: dict[str, object] = {
            "enabled": self.status == "ok",
            "status": self.status,
            "transport": "streamable-http",
            "path": MCP_PATH,
        }
        if self.stats_view is not None:
            block.update(self.stats_view())
        return block


def mount_mcp(app: FastAPI, manager: SessionManager, settings: Settings) -> McpMount:
    """Route /mcp and /mcp/ (before the UI's catch-all mount) unless MCP_ENABLED=false."""
    if not settings.mcp_enabled:
        return McpMount(status="disabled")
    try:  # the module itself, never a package attribute: an import failure is always seen
        from pundit.mcp_server.server import ROUTE_METHODS, HttpEndpoint, build_server
    except (ImportError, OSError) as exc:  # D8: everything else keeps running
        log.warning("MCP is off: the MCP SDK cannot be imported (%s)", exc)
        return McpMount(status="unavailable")
    limiter = RateLimiter(settings.mcp_rate_per_minute)
    server, stats = build_server(
        LiveSessions(manager),
        claims=rules_only(settings),
        limiter=limiter,
        trust_proxy=settings.trust_proxy,
    )
    endpoint = HttpEndpoint(server, allowed_hosts=settings.mcp_allowed_hosts)
    for path in (MCP_PATH, MCP_PATH + "/"):
        app.router.routes.append(Route(path, endpoint=endpoint, methods=ROUTE_METHODS))
    return McpMount(status="ok", stats_view=lambda: stats.view(limiter), run=endpoint.running)
