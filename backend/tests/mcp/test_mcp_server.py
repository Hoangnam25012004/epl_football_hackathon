"""The MCP server through the SDK's in-memory client (docs/day6-design.md 3.3-3.9 and 10.2).

To accept an intended change of the tool contract: `UPDATE_MCP_GOLDEN=1 pytest
tests/mcp/test_mcp_server.py`, review the diff of golden_tools.json and commit it with the code.
"""

import json
import logging
import os
import typing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jsonschema
import pytest
from helpers import run
from mcp import Client
from mcp.types import Tool
from mcp_helpers import CV, DL, RC, call, listing, static

from pundit.llm.dto import LlmWindow
from pundit.mcp_server.limits import RateLimiter
from pundit.mcp_server.outputs import DISCLAIMER
from pundit.mcp_server.params import WindowArg
from pundit.mcp_server.server import build_server, client_key
from pundit.mcp_server.sources import LiveEntry, ResolvedView
from pundit.orchestration.tools import (
    COMPARE_STAT,
    GET_STAT,
    CompareStatArgs,
    GetStatArgs,
    catalog,
)
from pundit.stats import REGISTRY

GOLDEN = Path(__file__).with_name("golden_tools.json")
TOOLS = {
    "list_matches", "list_metrics", GET_STAT, COMPARE_STAT, "get_scoreboard", "get_events",
    "get_moments", "verify_claim", "list_players",
}  # fmt: skip
CLAIM = "Harbor dominated the second half"


def snapshot(found: list[Tool]) -> list[dict[str, Any]]:
    """What a client relies on: names, annotations, the input schema, the output fields."""
    rows = [
        {
            "name": tool.name,
            "title": tool.title,
            "annotations": tool.annotations.model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
            if tool.annotations is not None
            else None,
            "input_schema": tool.input_schema,
            "output_fields": sorted((tool.output_schema or {}).get("properties", {})),
        }
        for tool in sorted(found, key=lambda tool: tool.name)
    ]
    result: list[dict[str, Any]] = json.loads(json.dumps(rows))
    return result


def test_tools_list_is_the_golden_contract() -> None:
    current = snapshot(listing(build_server(static())[0]))
    if os.environ.get("UPDATE_MCP_GOLDEN") == "1":
        text = json.dumps(current, indent=2, ensure_ascii=False) + "\n"
        GOLDEN.write_text(text, encoding="utf-8", newline="\n")
    assert current == json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_every_tool_is_read_only() -> None:
    found = listing(build_server(static())[0])
    assert {tool.name for tool in found} == TOOLS
    for tool in found:
        hints = tool.annotations
        assert hints is not None, tool.name
        flags = (hints.read_only_hint, hints.destructive_hint, hints.idempotent_hint)
        assert flags == (True, False, True), tool.name
        assert hints.open_world_hint is False, tool.name
        assert tool.title and tool.description, tool.name
        assert tool.output_schema is not None, tool.name


def test_the_stat_tools_are_the_agents_tools() -> None:
    """One catalog for the agents (in process) and for MCP (design D1)."""
    found = {tool.name: tool for tool in listing(build_server(static())[0])}
    described = {entry["name"]: entry["description"] for entry in catalog()}
    for name, args in ((GET_STAT, GetStatArgs), (COMPARE_STAT, CompareStatArgs)):
        assert found[name].description == described[name]
        assert set(found[name].input_schema["properties"]) == set(args.model_fields) | {
            "match_id",
            "as_of",
        }
        assert found[name].input_schema["properties"]["metric"]["enum"] == sorted(REGISTRY)
    assert set(WindowArg.model_fields) == set(LlmWindow.model_fields)
    window_types = typing.get_args(WindowArg.model_fields["type"].annotation)
    assert window_types == typing.get_args(LlmWindow.model_fields["type"].annotation)


SAMPLES = [
    ("list_matches", {}),
    ("list_metrics", {"scope": "team"}),
    (GET_STAT, {"match_id": DL, "metric": "possession", "team": "HAR",
                "window": {"type": "second_half"}}),
    (COMPARE_STAT, {"match_id": DL, "metric": "xg", "window": {"type": "last_minutes",
                                                               "minutes": 15}}),
    ("get_scoreboard", {"match_id": CV, "as_of": "HT"}),
    ("get_events", {"match_id": RC, "types": ["card", "var_check"]}),
    ("get_moments", {"match_id": CV}),
    ("verify_claim", {"match_id": DL, "text": CLAIM}),
    ("list_players", {"match_id": DL, "team": "away"}),
]  # fmt: skip


@pytest.mark.parametrize(("tool", "arguments"), SAMPLES, ids=[name for name, _ in SAMPLES])
def test_structured_content_follows_the_output_schema(tool: str, arguments: dict[str, Any]) -> None:
    server, stats = build_server(static())
    schemas = {found.name: found.output_schema for found in listing(server)}
    result = call(server, tool, arguments)
    assert result.is_error is False
    content = result.structured_content
    assert content is not None
    jsonschema.validate(content, schemas[tool] or {})
    assert json.loads(result.content[0].text) == content  # type: ignore[union-attr]
    assert (content["synthetic"], content["disclaimer"]) == (True, DISCLAIMER)
    assert (stats.calls, stats.errors, stats.refused) == (1, 0, 0)


def test_a_wrong_argument_is_a_tool_error_with_how_to_fix() -> None:
    server, stats = build_server(static())
    result = call(server, GET_STAT, {"match_id": DL, "metric": "shots", "team": "Kestrel FC Wrong"})
    assert result.is_error is True
    text = result.content[0].text  # type: ignore[union-attr]
    assert text.startswith("Error executing tool get_stat: unknown team 'Kestrel FC Wrong'")
    assert "kestrel_athletic (KES, Kestrel Athletic)" in text
    assert (stats.refused, stats.errors) == (1, 0)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_scoreboard", {"match_id": "nope-1"}),
        ("get_scoreboard", {"match_id": DL, "as_of": "123'"}),
        ("get_events", {"match_id": DL, "ids": ["a"], "last": 2}),
        ("verify_claim", {"match_id": "comeback_var-x", "text": CLAIM}),
    ],
)
def test_refusals_are_counted_apart_from_errors(tool: str, arguments: dict[str, Any]) -> None:
    server, stats = build_server(static())
    assert call(server, tool, arguments).is_error is True
    assert (stats.calls, stats.refused, stats.errors) == (1, 1, 0)


class Broken:
    """A source that fails as nothing should: the error must stay on the server."""

    async def resolve(self, match_id: str, as_of: str | None) -> ResolvedView:
        raise RuntimeError(r"C:\secret\pundit.toml: key=abc123 exploded")

    def live(self) -> list[LiveEntry]:
        return []


def test_an_unexpected_failure_says_nothing_about_the_server() -> None:
    server, stats = build_server(Broken())
    result = call(server, "get_scoreboard", {"match_id": DL})
    assert result.is_error is True
    assert result.content[0].text == "Error executing tool get_scoreboard"  # type: ignore[union-attr]
    assert "secret" not in result.model_dump_json() and "abc123" not in result.model_dump_json()
    assert (stats.errors, stats.refused) == (1, 0)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_moments", {"match_id": DL, "min_importance": 7}),
        ("get_events", {"match_id": DL, "last": 500}),
        ("verify_claim", {"match_id": DL, "text": "x" * 301}),
        ("verify_claim", {"match_id": DL}),
        (GET_STAT, {"metric": "xg", "team": "HAR"}),
        (GET_STAT, {"match_id": DL, "metric": "xg", "team": "HAR",
                    "window": {"type": "second_half", "half": 2}}),
        ("list_metrics", {"scope": "league"}),
    ],
)  # fmt: skip
def test_the_sdk_refuses_arguments_outside_the_schema(tool: str, arguments: dict[str, Any]) -> None:
    result = call(build_server(static())[0], tool, arguments)
    assert result.is_error is True


def test_vietnamese_round_trips_in_utf8() -> None:
    text = "Đội nhà áp đảo hiệp 2"
    result = call(build_server(static())[0], "verify_claim", {"match_id": DL, "text": text})
    content = result.structured_content
    assert content is not None
    assert content["claim"] == text and content["language"] == "vi"
    assert "kiểm soát bóng" in content["explanation"]
    assert json.loads(result.content[0].text)["claim"] == text  # type: ignore[union-attr]
    cards = call(build_server(static())[0], "get_events", {"match_id": RC, "types": ["card"]})
    assert "Trịnh Minh Khôi" in [
        e["player_name"] for e in (cards.structured_content or {})["events"]
    ]


def test_resources_and_prompts() -> None:
    server, _ = build_server(static())

    async def go() -> tuple[Any, Any, Any, Any]:
        async with Client(server) as client:
            about = await client.read_resource("pundit://about")
            metrics = await client.read_resource("pundit://metrics")
            check = await client.get_prompt(
                "fact_check", {"claim": "Đội nhà áp đảo", "match_id": CV}
            )
            brief = await client.get_prompt("match_briefing", {"match_id": CV, "language": "vi"})
            return about, metrics, check, brief

    about, metrics, check, brief = run(go())
    assert DISCLAIMER in about.contents[0].text
    assert [m["name"] for m in json.loads(metrics.contents[0].text)["metrics"]] == sorted(REGISTRY)
    words = check.messages[0].content.text
    assert '"Đội nhà áp đảo"' in words and "verify_claim" in words and CV in words
    assert "Do not add any number of your own" in words
    assert "get_moments" in brief.messages[0].content.text


def test_a_claim_costs_three_calls_of_the_rate_limit() -> None:
    limiter = RateLimiter(4, clock=lambda: 50.0)
    server, stats = build_server(static(), limiter=limiter)
    assert call(server, "verify_claim", {"match_id": DL, "text": CLAIM}).is_error is False
    assert call(server, "get_scoreboard", {"match_id": DL}).is_error is False
    refused = call(server, "get_scoreboard", {"match_id": DL})
    assert refused.is_error is True
    assert "rate limited: retry in 15 s" in refused.content[0].text  # type: ignore[union-attr]
    view = stats.view(limiter)
    assert (view["calls"], view["refused"], view["rate_limited"]) == (3, 1, 1)
    assert view["by_tool"] == {"get_scoreboard": 2, "verify_claim": 1}


def test_one_log_line_a_call_without_the_words_of_the_claim(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    server, _ = build_server(static())
    call(server, "verify_claim", {"match_id": DL, "text": "Zanzibar wording dominated"})
    call(server, GET_STAT, {"match_id": DL, "metric": "xg", "team": "nobody"})
    lines = [r.getMessage() for r in caplog.records if r.name == "pundit.mcp"]
    assert len(lines) == 2
    assert lines[0].startswith("mcp.call tool=verify_claim match=dominant_loser-42 ms=")
    assert lines[0].endswith("status=ok") and lines[1].endswith("status=refused")
    ours = [r.getMessage() for r in caplog.records if r.name.startswith("pundit")]
    assert not any("Zanzibar" in line for line in ours)


def test_the_client_key_is_the_ip_on_http_and_local_otherwise() -> None:
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.7"))
    on_http = SimpleNamespace(request_context=SimpleNamespace(request=request))
    in_memory = SimpleNamespace(request_context=SimpleNamespace(request=None))
    assert client_key(on_http) == "203.0.113.7"  # type: ignore[arg-type]
    assert client_key(in_memory) == "local"  # type: ignore[arg-type]

    class Outside:
        @property
        def request_context(self) -> object:
            raise LookupError("no request")

    assert client_key(Outside()) == "local"  # type: ignore[arg-type]
