"""The fault matrix C1-C13 of Day 6 (docs/day6-design.md 5.3): the whole app, real time, a real
OpenAI-compatible client (the Foundry code path), every LLM failure injected by a proxy.

The proxy (scripts/llm_chaos_proxy.py) answers from a cassette recorded once with Ollama, so a
"pass" is instant; the app runs with the shortened budgets of the matrix (LLM_TIMEOUT_S=6,
CLAIM_BUDGET_S=12, LLM_BREAKER_COOLDOWN_S=10) and the mock voice. Each case prints PASS / FAIL
with its numbers; at the end no task may be left and the log may hold no unexpected ERROR.

    cd backend
    uv run python scripts/chaos_run.py --record    # once, with Ollama running (about 5 min)
    uv run python scripts/chaos_run.py             # about 4 min, no Ollama needed
"""

import argparse
import asyncio
import io
import json
import logging
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from llm_chaos_proxy import Cassette, ChaosProxy
from mcp import Client
from pydantic import SecretStr
from websockets.asyncio.client import connect

from pundit.agents.contracts import ClaimRequest
from pundit.api.app import create_app
from pundit.engine.generator import generate
from pundit.llm.settings import Settings
from pundit.orchestration.resilience import CircuitBreaker
from pundit.orchestration.service import ClaimService, StaticSource

CASSETTE = Path(__file__).resolve().parents[1] / ".chaos" / "claims.jsonl"
MATCH = "dominant_loser-42"
LIVE = "comeback_var-7"
CLAIM = "Đội nhà áp đảo hiệp 2"
RECORDED = (CLAIM, "Harbor had more corners than Kestrel", "KES chỉ có 1 cú sút trong hiệp 2")
FLAKY = (
    "Harbor dominated the second half", "Kestrel áp đảo hiệp 2", "HAR không áp đảo hiệp 2",
    "HAR có nhiều cú sút hơn KES trong hiệp 2", "KES chỉ có 1 cú sút trong hiệp 2",
    "HAR kiểm soát bóng khoảng 60% trong hiệp 2", "HAR kiểm soát bóng 70% trong hiệp 2",
    "Kestrel had more corners than Harbor in the second half",
    "KES chưa có quả phạt góc nào trong hiệp 2", "Hai đội phạm lỗi ngang nhau trong hiệp 2",
    "HAR pressing mạnh hơn KES trong hiệp 2", "HAR áp đảo hiệp 2 nhưng vẫn thua",
    "HAR áp đảo hiệp 2 và thắng trận", "HAR có 14 cú sút trong hiệp 2",
    "Calder ghi bàn trong hiệp 2", "Kestrel won despite lower xG", "Kestrel ghi hai bàn",
    "Harbor scored two goals", "Harbor sút nhiều hơn nhưng Kestrel thắng", CLAIM,
)  # fmt: skip
# the log lines a fault is expected to write (anything else at ERROR fails the run)
EXPECTED_ERRORS = ("ASGI callable returned without completing response",)


class Errors(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = f"{record.name}: {record.getMessage()}"
        if not any(expected in text for expected in EXPECTED_ERRORS):
            self.records.append(text[:300])


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def serve(app: Any, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, thread


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)
    return ok


class Run:
    def __init__(self, app: Any, api: str, proxy: ChaosProxy) -> None:
        self.app = app
        self.api = api
        self.proxy = proxy
        self.http = httpx.AsyncClient(base_url=api, timeout=120)
        self.rules = ClaimService(Settings())  # the mock: what the rules alone answer
        match = generate("dominant_loser", 42)
        self.static = StaticSource(match.meta, match.events)

    async def mode(self, mode: str, count: int | None = None) -> None:
        self.proxy.chaos.set(mode, count)

    def requests(self) -> int:
        return int(self.proxy.chaos.counters["requests"])

    def reset_breakers(self) -> None:
        """A fresh claim breaker between cases (C12 checks the cooldown itself)."""
        service = self.app.state.manager.service
        service.breaker = CircuitBreaker(cooldown_s=service.settings.llm_breaker_cooldown_s)

    async def claim(self, text: str, match_id: str = MATCH) -> tuple[dict[str, Any], float]:
        started = time.perf_counter()
        answer = await self.http.post(
            "/claims/verify", json={"match_id": match_id, "text": text, "include_trace": True}
        )
        answer.raise_for_status()
        return answer.json(), time.perf_counter() - started

    async def rule_verdict(self, text: str) -> str:
        """What the rules alone answer on the finished match (the mock claim service)."""
        request = ClaimRequest(match_id=MATCH, text=text)
        result = await self.rules.verify(request, self.static, claim_id="chaos-rules")
        return result.verdict

    async def session(self, match_id: str, **body: Any) -> dict[str, Any]:
        scenario, seed = match_id.rsplit("-", 1)
        answer = await self.http.post(
            "/matches", json={"scenario": scenario, "seed": int(seed), **body}
        )
        answer.raise_for_status()
        info: dict[str, Any] = answer.json()
        return info

    async def info(self, match_id: str) -> dict[str, Any]:
        data: dict[str, Any] = (await self.http.get(f"/matches/{match_id}")).json()["info"]
        return data

    async def finished(self, match_id: str, timeout_s: float = 120) -> None:
        deadline = time.monotonic() + timeout_s
        while (await self.info(match_id))["status"] != "finished":
            if time.monotonic() > deadline:
                raise AssertionError(f"{match_id} did not finish")
            await asyncio.sleep(0.2)


def failures(result: dict[str, Any]) -> set[str]:
    return {
        span["output"]["failure"]
        for span in result.get("trace") or ()
        if span["agent"] in ("ClaimAnalyst", "Explainer")
        and isinstance(span.get("output", {}).get("failure"), str)
    }


async def claims_matrix(run: Run) -> bool:
    ok = True
    await run.session(MATCH, speed=1000)
    await run.finished(MATCH)
    expected = await run.rule_verdict(CLAIM)
    budget = run.app.state.settings.claim_budget_s

    await run.mode("hang")
    result, took = await run.claim(CLAIM)
    degraded = set(result["degraded"])
    ok &= check(
        "C1 hang: the rules and the template answer within the budget",
        took <= budget + 1 and {"analyst_fallback", "explainer_fallback"} <= degraded
        and result["verdict"] == expected,
        f"{took:.1f} s, {sorted(degraded)}, {result['verdict']}",
    )  # fmt: skip
    run.reset_breakers()

    await run.mode("http:500")
    for _ in range(2):
        await run.claim(CLAIM)
    before = run.requests()
    result, took = await run.claim(CLAIM)
    ok &= check(
        "C2 500 x3: the breaker opens, the next claim skips the LLM at once",
        "circuit_open" in result["degraded"] and run.requests() == before and took < 0.5,
        f"{took * 1000:.0f} ms, {result['degraded']}",
    )
    run.reset_breakers()

    await run.mode("http:429")
    before = run.requests()
    result, _ = await run.claim(CLAIM)
    calls = run.requests() - before
    ok &= check(
        "C3 429 with Retry-After: fallback and no retry storm",
        "analyst_fallback" in result["degraded"] and calls <= 2,
        f"{calls} LLM requests for one claim",
    )
    run.reset_breakers()

    await run.mode("reset")
    result, _ = await run.claim(CLAIM)
    ok &= check(
        "C4 connection cut: fallback",
        {"analyst_fallback", "explainer_fallback"} <= set(result["degraded"]),
        str(result["degraded"]),
    )
    run.reset_breakers()

    for case, mode in (("C5 garbage", "garbage"), ("C6 empty", "empty")):
        await run.mode(mode)
        before = run.requests()
        result, _ = await run.claim(CLAIM)
        seen = failures(result)
        ok &= check(
            f"{case}: an answer it cannot use, then the rules",
            "analyst_fallback" in result["degraded"] and result["verdict"] == expected,
            f"{run.requests() - before} requests, failures {sorted(seen)}",
        )
        run.reset_breakers()

    for case, mode in (("C7 content filter (400)", "filter"),
                       ("C7 content filter (finish_reason)", "filter:finish")):  # fmt: skip
        await run.mode(mode)
        result, _ = await run.claim(CLAIM)
        ok &= check(
            f"{case}: never published, the template is",
            result["explanation_source"] == "template" and result["verdict"] == expected,
            f"{result['explanation_source']}, {sorted(failures(result))}",
        )
        run.reset_breakers()

    await run.mode("flaky:0.3")
    verdicts: list[tuple[str, str, str]] = []
    for text in FLAKY:
        result, _ = await run.claim(text)
        verdicts.append((text, result["verdict"], await run.rule_verdict(text)))
        if result["degraded"]:
            run.reset_breakers()
    wrong = [v for v in verdicts if v[1] != v[2] and v[1] != "INSUFFICIENT_DATA"]
    ok &= check("C9 flaky 30%: 20 claims, every verdict valid, none against the rules",
                not wrong, f"{len(verdicts)} claims, {len(wrong)} differ")  # fmt: skip
    await run.mode("pass")
    run.reset_breakers()
    return ok


async def mcp_matrix(run: Run) -> bool:
    async with Client(run.api + "/mcp") as client:
        calm = await client.call_tool("verify_claim", {"match_id": MATCH, "text": CLAIM})
        await run.mode("hang")
        started = time.perf_counter()
        stormy = await client.call_tool("verify_claim", {"match_id": MATCH, "text": CLAIM})
        took = time.perf_counter() - started
    await run.mode("pass")
    same = (calm.structured_content or {}).get("verdict") == (stormy.structured_content or {}).get(
        "verdict"
    ) and (calm.structured_content or {}).get("explanation") == (
        stormy.structured_content or {}
    ).get("explanation")
    return check("C13 MCP verify_claim under a hanging LLM: rules only, the same answer",
                 same and not stormy.is_error and took < 2, f"{took * 1000:.0f} ms")  # fmt: skip


async def watch(url: str, messages: list[dict[str, Any]], stop: asyncio.Event) -> None:
    async with connect(url, max_size=None) as ws:
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except TimeoutError:
                continue
            messages.append(json.loads(raw))


async def studio_matrix(run: Run) -> bool:
    """C8, C10, C11 are about steady degraded play: the studio breakers keep the production
    cooldown (60 s), as a trial call every 10 s would put a 3 s wait in every tenth job."""
    crew = run.app.state.manager.studio
    crew.service.breaker = CircuitBreaker()
    crew.recaps.breaker = CircuitBreaker()
    ok = True
    analyst: list[dict[str, Any]] = []
    fan: list[dict[str, Any]] = []
    stop = asyncio.Event()
    await run.session(LIVE, speed=20, from_minute=30, autostart=False)
    ws = run.api.replace("http", "ws") + f"/ws/match/{LIVE}"
    watchers = [
        asyncio.create_task(watch(ws, analyst, stop)),
        asyncio.create_task(watch(ws + "?mode=casual&lang=vi&voice=female", fan, stop)),
    ]
    await asyncio.sleep(0.5)
    await run.mode("hang")
    await run.http.post(f"/matches/{LIVE}/start")
    await asyncio.sleep(15 * 60 / 20 + 4)  # 15 minutes of the match (and half-time) at 20x
    hung = await run.info(LIVE)
    await run.mode("delay:4")
    await asyncio.sleep(5 * 60 / 20)  # five more minutes, the LLM slower than the budgets
    slow = await run.info(LIVE)
    await run.http.post(f"/matches/{LIVE}/stop")
    await asyncio.sleep(1.0)
    stop.set()
    await asyncio.gather(*watchers)
    await run.mode("pass")

    kinds = {m["type"] for m in analyst}
    numbers = [m["n"] for m in analyst]
    contiguous = numbers == list(range(numbers[0], numbers[0] + len(numbers))) if numbers else False
    studio = hung.get("studio", {})
    ok &= check(
        "C10 a hanging LLM for 15 minutes: the studio goes on with its templates",
        {"talking_point", "commentary", "lower_third"} <= kinds and contiguous
        and (studio.get("lag_ms_p95") or 0) < 3000,
        f"{len(analyst)} messages, kinds {sorted(kinds)}, n contiguous {contiguous}, "
        f"lag p95 {studio.get('lag_ms_p95')} ms",
    )  # fmt: skip
    recaps = [m for m in analyst if m["type"] == "recap" and m["payload"].get("scope") == "H1"]
    fan_lines = [m for m in fan if m["type"] == "commentary"]
    ok &= check(
        "C11 a hanging LLM at half-time: the H1 recap comes from its template",
        bool(recaps) and bool(fan_lines),
        f"{len(recaps)} recap messages, {len(fan_lines)} Vietnamese lines for the fan",
    )
    delayed = slow.get("studio", {})
    ok &= check(
        "C8 an LLM slower than the budgets: templates on time, no job left hanging",
        (delayed.get("lag_ms_p95") or 0) < 3000 and delayed.get("errors", 0) == 0,
        f"lag p95 {delayed.get('lag_ms_p95')} ms, degraded {delayed.get('degraded')}",
    )
    return ok


async def recovery(run: Run) -> bool:
    await run.mode("http:500")
    for _ in range(3):
        await run.claim(CLAIM)
    await run.mode("pass")
    opened = (await run.http.get("/healthz")).json()["degraded"]
    await asyncio.sleep(run.app.state.settings.llm_breaker_cooldown_s + 0.5)
    result, _ = await run.claim(CLAIM)
    health = (await run.http.get("/healthz")).json()
    hits = run.proxy.chaos.counters["cassette_hits"]
    return check(
        "C12 the LLM is back: half-open, then closed; the LLM spec is used again",
        result["analyst_source"] == "llm" and health["degraded"] == [] and "circuit_open" in opened,
        f"analyst {result['analyst_source']}, explanation {result['explanation_source']}, "
        f"degraded before {opened} after {health['degraded']}, cassette hits {hits}",
    )


async def record(run: Run) -> bool:
    await run.session(MATCH, speed=1000)
    await run.finished(MATCH)
    for text in RECORDED:
        started = time.perf_counter()
        result, _ = await run.claim(text)
        print(f"  recorded {text!r}: analyst {result['analyst_source']} in "
              f"{time.perf_counter() - started:.0f} s")  # fmt: skip
    return check("cassette recorded", CASSETTE.is_file(), str(CASSETTE))


def settings(proxy_port: int, *, recording: bool) -> Settings:
    timeout, budget = (600.0, 1200.0) if recording else (6.0, 12.0)
    return Settings(
        use_mock_llm=False,
        llm_provider="openai",
        llm_base_url=f"http://127.0.0.1:{proxy_port}/v1",
        llm_model="qwen2.5:3b",
        llm_api_key=SecretStr("x"),
        llm_timeout_s=timeout,
        claim_budget_s=budget,
        llm_breaker_cooldown_s=10.0,
        tts_provider="mock",
    )


async def main(recording: bool, forward: bool) -> int:
    errors = Errors()
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger().addHandler(errors)
    if not recording and not forward and not CASSETTE.is_file():
        print(f"no cassette at {CASSETTE}: run once with --record (Ollama running)")
        return 2
    cassette = Cassette(CASSETTE, record=recording) if not forward else None
    proxy = ChaosProxy(cassette=cassette, replay_only=not (recording or forward))
    proxy_port, api_port = free_port(), free_port()
    proxy_server, proxy_thread = serve(proxy, proxy_port)
    app = create_app(settings(proxy_port, recording=recording))
    api_server, api_thread = serve(app, api_port)
    while not (proxy_server.started and api_server.started):
        await asyncio.sleep(0.05)
    run = Run(app, f"http://127.0.0.1:{api_port}", proxy)
    try:
        if recording:
            ok = await record(run)
        else:
            ok = await claims_matrix(run)
            ok &= await mcp_matrix(run)
            ok &= await studio_matrix(run)
            ok &= await recovery(run)
    finally:
        await run.http.aclose()
        api_server.should_exit = True
        api_thread.join(timeout=20)
        proxy_server.should_exit = True
        proxy_thread.join(timeout=10)
    leftover = [
        s.match_id
        for s in app.state.manager.sessions.values()
        if (s._task is not None and not s._task.done())
        or (s.director is not None and s.director.active())
    ]
    ok &= check("no task left after shutdown", not leftover, str(leftover))
    ok &= check("no unexpected ERROR in the log", not errors.records, "; ".join(errors.records[:3]))
    print("CHAOS RUN " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--record", action="store_true", help="record the cassette (Ollama)")
    parser.add_argument("--forward", action="store_true", help="no cassette: Ollama answers")
    options = parser.parse_args()
    raise SystemExit(asyncio.run(main(options.record, options.forward)))
