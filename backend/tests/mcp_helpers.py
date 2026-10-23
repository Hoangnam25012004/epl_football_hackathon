"""Shared helpers of the MCP tests (docs/day6-design.md section 10): one cache of generated
matches for the whole run, and calls through the SDK's in-memory client."""

from functools import cache
from typing import Any

from helpers import run
from mcp import Client
from mcp.server import MCPServer
from mcp.types import CallToolResult, Tool

from pundit.mcp_server.sources import StaticMatches

DL = "dominant_loser-42"
CV = "comeback_var-7"
RC = "red_card_swing-7"
MATCHES = (DL, CV, RC)  # the three eval matches


@cache
def static() -> StaticMatches:
    """One cache for the whole run: a match takes about a second to generate."""
    return StaticMatches()


def call(server: MCPServer, tool: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
    async def go() -> CallToolResult:
        async with Client(server) as client:
            return await client.call_tool(tool, arguments or {})

    return run(go())


def listing(server: MCPServer) -> list[Tool]:
    async def go() -> list[Tool]:
        async with Client(server) as client:
            return list((await client.list_tools()).tools)

    return run(go())
