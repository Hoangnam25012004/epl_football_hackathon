"""Live smoke test of MCP (docs/day6-design.md section 11, W4).

stdio: `pundit mcp` in a child process, through the SDK's client. HTTP: the API (mock LLM,
studio on) on a free port; a shared session replays dominant_loser-42 at 1000x while 20 calls
run through the SDK's Streamable HTTP client, each checked against the registry at its own
as_of; then a claim on the live session (nothing published), the paths, a foreign Host and
Origin, GET, the rate limit, /healthz, no error in the log and no task left.

    cd backend
    uv run python scripts/mcp_smoke.py
"""

import asyncio
import io
import logging
import socket
import sys
import threading
import time
from typing import Any

import httpx
import uvicorn
from mcp import Client, StdioServerParameters

from pundit.api.app import create_app
from pundit.llm.settings import Settings
from pundit.stats import MetricParams, compare, compute
from pundit.stats.result import display

MATCH = "dominant_loser-42"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


class Errors(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(f"{record.name}: {record.getMessage()}")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
    return ok


async def stdio() -> bool:
    # this interpreter with this import path: works where the venv's python.exe may not run
    boot = (
        "import site, sys\n"
        "for path in list(sys.path):\n"
        "    if path.rstrip('/\\\\').endswith('site-packages'):\n"
        "        site.addsitedir(path)\n"
        "from pundit.cli import main\n"
        "raise SystemExit(main(sys.argv[1:]))\n"
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", boot, "mcp"],
        env={"PYTHONPATH": ";".join(sys.path) if sys.platform == "win32" else ":".join(sys.path)},
    )
    started = time.perf_counter()
    async with Client(params) as client:
        tools = (await client.list_tools()).tools
        claim = await client.call_tool(
            "verify_claim", {"match_id": MATCH, "text": "Đội nhà áp đảo hiệp 2"}
        )
        board = await client.call_tool(
            "get_scoreboard", {"match_id": "comeback_var-7", "as_of": "HT"}
        )
    took = time.perf_counter() - started
    content, score = claim.structured_content or {}, board.structured_content or {}
    ok = check("stdio: 9 tools", len(tools) == 9, str(len(tools)))
    ok &= check(
        "stdio: verify_claim in Vietnamese",
        (content.get("verdict"), content.get("claim")) == ("TRUE", "Đội nhà áp đảo hiệp 2"),
        str(content.get("verdict")),
    )
    ok &= check(
        "stdio: comeback_var-7 at HT is 0-1",
        (score.get("home", {}).get("goals"), score.get("away", {}).get("goals")) == (0, 1),
    )
    print(f"      stdio session took {took:.1f} s")
    return ok


async def http(app: Any, port: int) -> bool:
    url = f"http://127.0.0.1:{port}"
    ok = True
    for path in ("/mcp", "/mcp/"):
        async with Client(url + path) as client:
            names = [tool.name for tool in (await client.list_tools()).tools]
        ok &= check(f"http {path}: 9 tools", len(names) == 9, str(names))
    async with httpx.AsyncClient(base_url=url, timeout=60) as rest:
        created = await rest.post(
            "/matches", json={"scenario": "dominant_loser", "seed": 42, "speed": 1000}
        )
        created.raise_for_status()
        session = app.state.manager.get(MATCH)
        results: list[tuple[str, dict[str, Any]]] = []
        failures: list[str] = []
        async with Client(url + "/mcp") as client:

            async def one(n: int) -> None:
                await asyncio.sleep(n * 0.02)
                if n % 2:
                    tool, arguments = "compare_stat", {"match_id": MATCH, "metric": "shots"}
                else:
                    tool = "get_stat"
                    arguments = {"match_id": MATCH, "metric": "possession", "team": "home"}
                result = await client.call_tool(tool, arguments)
                if result.is_error or result.structured_content is None:
                    text = str(result.content)
                    if "has not started" not in text:
                        failures.append(text[:120])
                    return
                results.append((tool, result.structured_content))

            await asyncio.gather(*(one(n) for n in range(20)))
            listed = await client.call_tool("list_matches", {})
            claim = await client.call_tool(
                "verify_claim", {"match_id": MATCH, "text": "Harbor dominated the second half"}
            )
        deadline = time.monotonic() + 120  # the replay runs on the server's loop: ask it
        while (await rest.get(f"/matches/{MATCH}")).json()["info"]["status"] != "finished":
            if time.monotonic() > deadline:
                raise AssertionError("the replay did not finish")
            await asyncio.sleep(0.2)
        wrong = []
        for tool, content in results:
            view = session.view_at(content["as_of"]["seq"])
            if tool == "get_stat":
                want = display(compute(view, "possession", MetricParams(team="harbor_lions")))
                if content["stat"]["display"] != want or content["source"] != "live":
                    wrong.append(f"possession at {content['as_of']['minute']}")
            else:
                both = compare(view, "shots")
                got = (content["home"]["display"], content["away"]["display"])
                if got != (display(both.home), display(both.away)):
                    wrong.append(f"shots at {content['as_of']['minute']}")
        seqs = sorted({content["as_of"]["seq"] for _, content in results})
        ok &= check("http: 20 calls during a 1000x replay, no error", not failures, str(failures))
        ok &= check("http: every answer is the registry's at its as_of", not wrong, str(wrong))
        print(f"      {len(results)} answers at {len(seqs)} different minutes of the replay")
        live = [entry["match_id"] for entry in (listed.structured_content or {}).get("live", [])]
        ok &= check("http: list_matches shows the live session", live == [MATCH], str(live))
        info = (await rest.get(f"/matches/{MATCH}")).json()["info"]
        ok &= check(
            "http: a claim through MCP is not published",
            not claim.is_error and info["claims"] == 0,
            f"claims={info['claims']}",
        )
        host = await rest.post("/mcp", json=LIST, headers={**HEADERS, "Host": "evil.example"})
        origin = await rest.post(
            "/mcp", json=LIST, headers={**HEADERS, "Origin": "https://evil.example"}
        )
        get = await rest.get("/mcp", headers={"Accept": "text/event-stream"})
        ok &= check("http: another Host is 421", host.status_code == 421, str(host.status_code))
        ok &= check(
            "http: another Origin is 403", origin.status_code == 403, str(origin.status_code)
        )
        ok &= check("http: GET is 405", get.status_code == 405, str(get.status_code))
        async with Client(url + "/mcp") as client:
            burst = await asyncio.gather(
                *(client.call_tool("get_scoreboard", {"match_id": MATCH}) for _ in range(60))
            )
        limited = [r for r in burst if r.is_error and "rate limited" in str(r.content)]
        ok &= check("http: a burst is rate limited", len(limited) > 0, f"{len(limited)} of 60")
        health = (await rest.get("/healthz")).json()["mcp"]
        ok &= check(
            "http: /healthz counts calls, no error",
            health["calls"] > 80 and health["errors"] == 0 and health["rate_limited"] > 0,
            str(health),
        )
    return ok


async def main() -> int:
    errors = Errors()
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger().addHandler(errors)
    ok = await stdio()
    app = create_app(Settings())  # mock LLM, studio on, MCP on with the default limit
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        ok &= await http(app, port)
    finally:
        server.should_exit = True
        thread.join(timeout=15)
    leftover = [
        s.match_id
        for s in app.state.manager.sessions.values()
        if s._task is not None and not s._task.done()
    ]
    ok &= check("no task left after shutdown", not leftover, str(leftover))
    ok &= check("no error in the log", not errors.records, "; ".join(errors.records[:3]))
    print("MCP SMOKE " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main()))
