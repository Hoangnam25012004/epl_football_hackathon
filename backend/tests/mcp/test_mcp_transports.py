"""MCP transports (docs/day6-design.md sections 3.7, 10.2 and 13): stdio in a child process,
/mcp in the API (paths, hosts, origins, sizes, methods, lifespan, rate limit, healthz, the SDK
missing) and the standalone `pundit mcp --http` app."""

import json
import os
import queue
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

import pytest
from fastapi.testclient import TestClient
from helpers import api_settings

from pundit.api.app import create_app
from pundit.engine.replay import SteppingClock
from pundit.mcp_server.server import MAX_BODY_BYTES, build_server, standalone_app
from pundit.mcp_server.sources import StaticMatches

BACKEND = Path(__file__).resolve().parents[2]
BASE = "http://127.0.0.1:8000"  # a Host the default MCP_ALLOWED_HOSTS serves
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
# the child process: on a machine where the venv's python.exe may not run, the parent's Python
# with the parent's import path (site directories processed for their .pth files)
BOOT = (
    "import site, sys\n"
    "for path in list(sys.path):\n"
    "    if path.rstrip('/\\\\').endswith('site-packages'):\n"
    "        site.addsitedir(path)\n"
    "from pundit.cli import main\n"
    "raise SystemExit(main(sys.argv[1:]))\n"
)


def rpc(method: str, params: dict[str, Any] | None = None, n: int = 1) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": n, "method": method}
    if params is not None:
        message["params"] = params
    return message


def call(name: str, arguments: dict[str, Any], n: int = 1) -> dict[str, Any]:
    return rpc("tools/call", {"name": name, "arguments": arguments}, n)


INITIALIZE = rpc(
    "initialize",
    {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pundit-tests", "version": "1"},
    },
)


@pytest.fixture
def app_client(tmp_path: Path) -> Iterator[TestClient]:
    (tmp_path / "index.html").write_text("<html>pundit ui</html>", encoding="utf-8")
    settings = api_settings(mcp_enabled=True, ui_dir=str(tmp_path))
    with TestClient(create_app(settings, clock_factory=SteppingClock), base_url=BASE) as client:
        yield client


def post(client: TestClient, message: dict[str, Any], path: str = "/mcp", **headers: str) -> Any:
    return client.post(path, json=message, headers={**HEADERS, **headers})


def reader(stream: IO[bytes]) -> "queue.Queue[bytes | None]":
    """Lines of a child's pipe, read in a thread: a silent child fails the test, never hangs it."""
    lines: queue.Queue[bytes | None] = queue.Queue()

    def pump() -> None:
        for line in iter(stream.readline, b""):
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    return lines


def drain(lines: "queue.Queue[bytes | None]") -> list[str]:
    found: list[str] = []
    while (line := lines.get(timeout=60)) is not None:
        found.append(line.decode("utf-8"))
    return found


def test_stdio_writes_json_rpc_only_on_stdout() -> None:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path), "PYTHONIOENCODING": "utf-8"}
    child = subprocess.Popen(
        [sys.executable, "-c", BOOT, "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=BACKEND,
        env=env,
    )
    assert child.stdin is not None and child.stdout is not None and child.stderr is not None
    out, err = reader(child.stdout), reader(child.stderr)

    def send(message: dict[str, Any]) -> None:
        assert child.stdin is not None
        child.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        child.stdin.flush()

    def exchange(message: dict[str, Any]) -> dict[str, Any]:
        send(message)
        line = out.get(timeout=60)
        assert line is not None, "the server closed stdout"
        answer: dict[str, Any] = json.loads(line.decode("utf-8"))
        return answer

    try:
        hello = exchange(INITIALIZE)
        assert hello["result"]["serverInfo"]["name"] == "pundit"
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed = exchange(rpc("tools/list", n=2))
        assert len(listed["result"]["tools"]) == 9
        text = "Đội nhà áp đảo hiệp 2"
        checked = exchange(call("verify_claim", {"match_id": "dominant_loser-42", "text": text}, 3))
        content = checked["result"]["structuredContent"]
        assert (content["claim"], content["verdict"]) == (text, "TRUE")
        child.stdin.close()
        assert child.wait(timeout=60) == 0
        rest, logs = drain(out), "".join(drain(err))
    finally:
        if child.poll() is None:
            child.kill()
    for line in rest:  # anything left on stdout must be JSON-RPC too
        assert json.loads(line)["jsonrpc"] == "2.0"
    assert "mcp.call tool=verify_claim" in logs  # the log lines went to stderr
    assert text not in logs


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_both_paths_answer(app_client: TestClient, path: str) -> None:
    hello = post(app_client, INITIALIZE, path)
    assert hello.status_code == 200
    assert hello.headers["content-type"].startswith("application/json")
    assert "mcp-session-id" not in hello.headers  # stateless: nothing kept in memory
    listed = post(app_client, rpc("tools/list", n=2), path)
    assert len(listed.json()["result"]["tools"]) == 9


def test_the_ui_still_owns_every_other_path(app_client: TestClient) -> None:
    assert "pundit ui" in app_client.get("/").text
    assert app_client.get("/healthz").json()["status"] == "ok"
    assert app_client.get("/mcp/other").status_code == 404


def test_a_call_over_http(app_client: TestClient) -> None:
    answer = post(app_client, call("get_scoreboard", {"match_id": "comeback_var-7", "as_of": "HT"}))
    content = answer.json()["result"]["structuredContent"]
    assert (content["home"]["goals"], content["away"]["goals"], content["source"]) == (
        0,
        1,
        "replay",
    )


def test_get_has_no_stream_here(app_client: TestClient) -> None:
    for method in ("GET", "DELETE"):
        answer = app_client.request(method, "/mcp", headers={"Accept": "text/event-stream"})
        assert (answer.status_code, answer.headers["allow"]) == (405, "POST")  # never the UI's 404


def test_another_host_is_refused(app_client: TestClient) -> None:
    assert post(app_client, rpc("tools/list"), Host="evil.example").status_code == 421


@pytest.mark.parametrize(
    ("origin", "status"),
    [
        ("https://evil.example", 403),
        ("http://localhost:6274", 200),  # MCP Inspector on this machine
        ("http://127.0.0.1:3000", 200),
    ],
)
def test_origins(app_client: TestClient, origin: str, status: int) -> None:
    assert post(app_client, rpc("tools/list"), Origin=origin).status_code == status


def test_a_body_too_large_is_refused(app_client: TestClient) -> None:
    body = b'{"padding": "' + b"x" * MAX_BODY_BYTES + b'"}'
    assert app_client.post("/mcp", content=body, headers=HEADERS).status_code == 413


def test_healthz_counts_the_calls(app_client: TestClient) -> None:
    post(app_client, call("get_scoreboard", {"match_id": "dominant_loser-42"}))
    post(app_client, call("get_scoreboard", {"match_id": "nope-1"}, 2))
    block = app_client.get("/healthz").json()["mcp"]
    assert (block["enabled"], block["status"], block["path"]) == (True, "ok", "/mcp")
    assert (block["calls"], block["refused"], block["errors"]) == (2, 1, 0)
    assert block["by_tool"] == {"get_scoreboard": 2}


def test_outside_the_apps_run_mcp_answers_503(tmp_path: Path) -> None:
    app = create_app(api_settings(mcp_enabled=True), clock_factory=SteppingClock)
    outside = TestClient(app, base_url=BASE)  # no `with`: the lifespan has not run
    assert post(outside, rpc("tools/list")).status_code == 503
    for _ in range(2):  # the SDK's session manager runs once: each run builds a new one
        with TestClient(app, base_url=BASE) as inside:
            assert post(inside, rpc("tools/list")).status_code == 200


def test_mcp_disabled_is_the_day_5_app() -> None:
    app = create_app(api_settings(mcp_enabled=False), clock_factory=SteppingClock)
    with TestClient(app, base_url=BASE) as client:
        assert post(client, rpc("tools/list")).status_code == 404
        assert "mcp" not in client.get("/healthz").json()


def test_without_the_sdk_the_app_still_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pundit.mcp_server.server", None)  # import fails
    app = create_app(api_settings(mcp_enabled=True), clock_factory=SteppingClock)
    with TestClient(app, base_url=BASE) as client:
        assert post(client, rpc("tools/list")).status_code == 404
        block = client.get("/healthz").json()["mcp"]
        assert (block["enabled"], block["status"]) == (False, "unavailable")
        created = client.post("/matches", json={"scenario": "balanced", "seed": 3, "speed": 1000})
        assert created.status_code == 200


def rate_limited(client: TestClient, n: int, **headers: str) -> bool:
    answer = post(client, call("get_scoreboard", {"match_id": "dominant_loser-42"}, n), **headers)
    result = answer.json()["result"]
    return bool(result.get("isError")) and "rate limited" in result["content"][0]["text"]


def test_the_rate_limit_is_per_client() -> None:
    settings = api_settings(mcp_enabled=True, mcp_rate_per_minute=2)
    app = create_app(settings, clock_factory=SteppingClock)
    with TestClient(app, base_url=BASE) as client:
        assert [rate_limited(client, n) for n in range(3)] == [False, False, True]
        # without TRUST_PROXY a forwarded address is ignored: the same client
        assert rate_limited(client, 4, **{"X-Forwarded-For": "198.51.100.1"})
        assert client.get("/healthz").json()["mcp"]["rate_limited"] == 2


def test_behind_a_trusted_proxy_the_forwarded_client_counts() -> None:
    settings = api_settings(mcp_enabled=True, mcp_rate_per_minute=1, trust_proxy=True)
    app = create_app(settings, clock_factory=SteppingClock)
    with TestClient(app, base_url=BASE) as client:
        first = {"X-Forwarded-For": "203.0.113.9, 198.51.100.1"}
        second = {"X-Forwarded-For": "203.0.113.9, 198.51.100.2"}  # a forged first hop
        assert not rate_limited(client, 1, **first)
        assert rate_limited(client, 2, **first)
        assert not rate_limited(client, 3, **second)


def test_the_standalone_http_app() -> None:
    server, stats = build_server(StaticMatches())
    app = standalone_app(server, allowed_hosts=["127.0.0.1", "localhost"])
    with TestClient(app, base_url=BASE) as client:
        for path in ("/mcp", "/mcp/"):
            assert post(client, rpc("tools/list"), path).status_code == 200
        assert client.get("/mcp").status_code == 405
        assert post(client, rpc("tools/list"), Host="evil.example").status_code == 421
        answer = post(client, call("list_matches", {}))
        assert len(answer.json()["result"]["structuredContent"]["scenarios"]) == 4
    assert stats.calls == 1
