"""The fault-injecting proxy of the Day 6 fallback runs (docs/day6-design.md 5.2), called as an
ASGI app with a fake upstream: every mode answers as designed, a counted mode goes back to pass,
the cassette records, replays and names a miss."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from helpers import run

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "llm_chaos_proxy.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("llm_chaos_proxy", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["llm_chaos_proxy"] = module
    spec.loader.exec_module(module)
    return module


chaos = load()
BODY = {"model": "qwen2.5:3b", "messages": [{"role": "user", "content": "hi"}],
        "response_format": {"type": "json_object"}}  # fmt: skip


def upstream(calls: list[dict[str, Any]]) -> httpx.MockTransport:
    def answer(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=chaos.completion('{"ok": true}'))

    return httpx.MockTransport(answer)


async def call(
    app: Any, path: str = "/v1/chat/completions", body: Any = BODY, method: str = "POST"
) -> tuple[int | None, dict[str, str], bytes, bool]:
    """(status, headers, body, complete) of one request; complete is False for a cut answer."""
    sent: list[dict[str, Any]] = []
    raw = json.dumps(body).encode()
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await asyncio.sleep(0.05)
        return {"type": "http.disconnect"}  # the client gave up

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {"type": "http", "method": method, "path": path, "headers": []}
    await app(scope, receive, send)
    start = next((m for m in sent if m["type"] == "http.response.start"), None)
    chunks = [m for m in sent if m["type"] == "http.response.body"]
    headers = {k.decode(): v.decode() for k, v in (start or {}).get("headers", [])}
    complete = bool(chunks) and not chunks[-1].get("more_body", False)
    return (start or {}).get("status"), headers, b"".join(c["body"] for c in chunks), complete


def test_pass_forwards_to_the_upstream() -> None:
    calls: list[dict[str, Any]] = []
    app = chaos.ChaosProxy("http://up/v1", transport=upstream(calls))
    status, _, body, complete = run(call(app))
    assert (status, complete) == (200, True)
    assert json.loads(body)["choices"][0]["message"]["content"] == '{"ok": true}'
    assert calls == [BODY]


@pytest.mark.parametrize(
    ("mode", "status"),
    [("http:500", 500), ("http:503", 503), ("http:401", 401), ("filter", 400), ("garbage", 200),
     ("empty", 200), ("filter:finish", 200)],
)  # fmt: skip
def test_failure_modes(mode: str, status: int) -> None:
    calls: list[dict[str, Any]] = []
    app = chaos.ChaosProxy("http://up/v1", transport=upstream(calls))
    app.chaos.set(mode, None)
    got, _, body, _ = run(call(app))
    assert got == status and calls == []
    if mode == "filter":
        assert json.loads(body)["error"]["code"] == "content_filter"
    if mode == "garbage":
        with pytest.raises(json.JSONDecodeError):
            json.loads(json.loads(body)["choices"][0]["message"]["content"])
    if mode == "empty":
        assert json.loads(body)["choices"] == []
    if mode == "filter:finish":
        assert json.loads(body)["choices"][0]["finish_reason"] == "content_filter"


def test_a_rate_limit_says_when_to_retry() -> None:
    app = chaos.ChaosProxy("http://up/v1", transport=upstream([]))
    app.chaos.set("http:429", None)
    status, headers, _, _ = run(call(app))
    assert (status, headers["retry-after"]) == (429, "5")


def test_hang_answers_nothing_until_the_client_leaves() -> None:
    app = chaos.ChaosProxy("http://up/v1", transport=upstream([]))
    app.chaos.set("hang", None)
    status, _, body, _ = run(call(app))
    assert (status, body) == (None, b"")


def test_reset_cuts_the_answer() -> None:
    app = chaos.ChaosProxy("http://up/v1", transport=upstream([]))
    app.chaos.set("reset", None)
    status, headers, body, complete = run(call(app))
    assert status == 200 and not complete and len(body) < int(headers["content-length"])


def test_delay_then_pass() -> None:
    calls: list[dict[str, Any]] = []
    app = chaos.ChaosProxy("http://up/v1", transport=upstream(calls))
    app.chaos.set("delay:0.05", None)
    assert run(call(app))[0] == 200 and len(calls) == 1


def test_a_counted_mode_goes_back_to_pass() -> None:
    calls: list[dict[str, Any]] = []
    app = chaos.ChaosProxy("http://up/v1", transport=upstream(calls))
    app.chaos.set("http:500", 2)
    statuses = [run(call(app))[0] for _ in range(3)]
    assert statuses == [500, 500, 200]
    assert app.chaos.view()["mode"] == "pass"
    counters = app.chaos.view()["counters"]
    assert (counters["requests"], counters["injected"], counters["forwarded"]) == (3, 2, 1)


def test_flaky_is_seeded() -> None:
    def statuses() -> list[int | None]:
        app = chaos.ChaosProxy("http://up/v1", transport=upstream([]), seed=11)
        app.chaos.set("flaky:0.5", None)
        return [run(call(app))[0] for _ in range(12)]

    first = statuses()
    assert first == statuses() and set(first) == {200, 500}


def test_the_control_route() -> None:
    app = chaos.ChaosProxy("http://up/v1", transport=upstream([]))
    status, _, body, _ = run(call(app, "/_chaos", {"mode": "hang", "count": 3}))
    assert status == 200 and json.loads(body)["mode"] == "hang"
    status, _, body, _ = run(call(app, "/_chaos", {"mode": "explode"}))
    assert status == 400 and "unknown mode" in json.loads(body)["error"]
    status, _, body, _ = run(call(app, "/_chaos", None, method="GET"))
    assert json.loads(body)["count"] == 3


def test_the_cassette_records_then_replays(tmp_path: Path) -> None:
    path = tmp_path / "cassette.jsonl"
    calls: list[dict[str, Any]] = []
    recorder = chaos.ChaosProxy("http://up/v1", transport=upstream(calls),
                                cassette=chaos.Cassette(path, record=True))  # fmt: skip
    run(call(recorder))
    run(call(recorder))  # recorded once, then answered from the cassette
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1 and len(calls) == 1
    player = chaos.ChaosProxy("http://up/v1", transport=upstream(calls), replay_only=True,
                              cassette=chaos.Cassette(path, record=False))  # fmt: skip
    status, _, body, _ = run(call(player))
    assert status == 200 and len(calls) == 1  # answered from the cassette, no upstream call
    other = {**BODY, "messages": [{"role": "user", "content": "something else"}]}
    status, _, body, _ = run(call(player, body=other))
    assert status == 599 and "cassette miss" in json.loads(body)["error"]["message"]
    counters = player.chaos.view()["counters"]
    assert (counters["cassette_hits"], counters["cassette_misses"]) == (1, 1)


@pytest.mark.parametrize("mode", ["explode", "delay", "http:abc", "filter:loud", "flaky"])
def test_bad_modes(mode: str) -> None:
    with pytest.raises(ValueError):
        chaos.check_mode(mode)
