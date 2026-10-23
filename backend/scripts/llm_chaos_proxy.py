"""A fault-injecting proxy in front of an OpenAI-compatible LLM (docs/day6-design.md 5.2).

The app talks to it as it talks to Foundry (LLM_PROVIDER=openai, LLM_BASE_URL=http://127.0.0.1:
11435/v1, the same AsyncOpenAI client): the proxy forwards /v1/* to Ollama, or answers from a
cassette, or fails the way it is told to.

    POST /_chaos {"mode": "hang", "count": 3}   the next 3 requests hang, then "pass" again
    POST /_chaos {"mode": "http:500"}           every request fails until the mode changes
    GET  /_chaos                                the mode and the counters

Modes: pass, hang, delay:S, http:CODE (429 carries Retry-After), filter (Azure's 400
content_filter error), filter:finish (200 with finish_reason content_filter), reset (the
connection is cut in the middle of the answer), garbage (a completion whose content is not the
asked JSON), empty (200 without choices), flaky:P (a 500 with probability P, from a fixed seed).

A cassette answers at once (Ollama on a CPU takes 20 s a call, more than the budgets under
test): --record F stores every answer under sha256(model, messages, response_format), --replay F
answers from F only and a miss is a 599 that names the cause.

    cd backend
    uv run python scripts/llm_chaos_proxy.py --port 11435 [--record F | --replay F]
"""

import argparse
import asyncio
import hashlib
import json
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import uvicorn

UPSTREAM = "http://127.0.0.1:11434/v1"
MODES = ("pass", "hang", "delay", "http", "filter", "reset", "garbage", "empty", "flaky")
FILTERED = {
    "error": {
        "message": "The response was filtered due to the prompt triggering Azure OpenAI's "
        "content management policy.",
        "type": None,
        "param": "prompt",
        "code": "content_filter",
        "status": 400,
        "innererror": {"code": "ResponsibleAIPolicyViolation"},
    }
}
Scope = dict[str, Any]
Message = dict[str, Any]


def check_mode(mode: str) -> str:
    name, _, arg = mode.partition(":")
    if name not in MODES:
        raise ValueError(f"unknown mode {mode!r}; modes: {', '.join(MODES)}")
    if name in ("delay", "flaky") and not arg:
        raise ValueError(f"{name} needs a value, e.g. {name}:{'4' if name == 'delay' else '0.3'}")
    if name == "http" and not arg.isdigit():
        raise ValueError("http needs a status code, e.g. http:500")
    if name == "filter" and arg not in ("", "finish"):
        raise ValueError("filter or filter:finish")
    return mode


@dataclass
class Chaos:
    mode: str = "pass"
    count: int | None = None  # requests left in this mode; None until it is changed
    counters: Counter[str] = field(default_factory=Counter)

    def set(self, mode: str, count: int | None) -> None:
        self.mode = check_mode(mode)
        self.count = count if count is None or count > 0 else None

    def take(self) -> str:
        """The mode of the next request (a counted mode goes back to pass when used up)."""
        mode = self.mode
        if self.count is not None:
            self.count -= 1
            if self.count <= 0:
                self.mode, self.count = "pass", None
        self.counters["requests"] += 1
        self.counters[f"mode:{mode.partition(':')[0]}"] += 1
        return mode

    def view(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "count": self.count,
            "counters": dict(sorted(self.counters.items())),
        }


class Cassette:
    """Answers by request: one JSON line per answer, appended when recording."""

    def __init__(self, path: Path, *, record: bool) -> None:
        self.path = path
        self.record = record
        self.answers: dict[str, Message] = {}
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    self.answers[entry["key"]] = entry["answer"]

    @staticmethod
    def key(body: Message) -> str:
        what = {k: body.get(k) for k in ("model", "messages", "response_format")}
        return hashlib.sha256(
            json.dumps(what, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    def get(self, body: Message) -> Message | None:
        return self.answers.get(self.key(body))

    def put(self, body: Message, answer: Message) -> None:
        key = self.key(body)
        if key in self.answers:
            return
        self.answers[key] = answer
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"key": key, "answer": answer}, ensure_ascii=False) + "\n")


def completion(content: str | None, finish: str = "stop", model: str = "chaos") -> Message:
    return {
        "id": "chatcmpl-chaos",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish,
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


async def _send(send: Any, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
    head = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    head += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": head})
    await send({"type": "http.response.body", "body": body})


async def _json(send: Any, status: int, data: Any, headers: dict[str, str] | None = None) -> None:
    await _send(send, status, json.dumps(data, ensure_ascii=False).encode(), headers)


class ChaosProxy:
    """The ASGI app (tests and chaos_run.py run it in process)."""

    def __init__(
        self,
        upstream: str = UPSTREAM,
        *,
        cassette: Cassette | None = None,
        replay_only: bool = False,
        seed: int = 7,
        transport: httpx.AsyncBaseTransport | None = None,
        upstream_timeout_s: float = 900.0,
    ) -> None:
        self.upstream = upstream.rstrip("/")
        self.cassette = cassette
        self.replay_only = replay_only
        self.chaos = Chaos()
        self._rng = random.Random(seed)
        self._transport = transport
        self._timeout = upstream_timeout_s

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        path = scope["path"]
        if path == "/_chaos":
            await self._control(scope, body, send)
        elif path.startswith("/v1/"):
            await self._proxy(scope, body, receive, send)
        else:
            await _json(send, 404, {"error": {"message": f"no route {path}"}})

    async def _control(self, scope: Scope, body: bytes, send: Any) -> None:
        if scope["method"] == "POST":
            try:
                order = json.loads(body or b"{}")
                self.chaos.set(str(order.get("mode", "pass")), order.get("count"))
            except (ValueError, TypeError, AttributeError) as exc:
                await _json(send, 400, {"error": str(exc)})
                return
        await _json(send, 200, self.chaos.view())

    async def _proxy(self, scope: Scope, body: bytes, receive: Any, send: Any) -> None:
        mode = self.chaos.take()
        name, _, arg = mode.partition(":")
        counters = self.chaos.counters
        if name == "flaky":
            name = "http" if self._rng.random() < float(arg) else "pass"
            arg = "500"
        if name == "hang":  # until the client gives up
            counters["injected"] += 1
            while (await receive())["type"] != "http.disconnect":
                pass
            return
        if name == "delay":
            await asyncio.sleep(float(arg))
            name = "pass"
        if name == "http":
            counters["injected"] += 1
            code = int(arg)
            headers = {"retry-after": "5"} if code == 429 else None
            await _json(send, code, {"error": {"message": f"chaos: HTTP {code}", "code": code}},
                        headers)  # fmt: skip
            return
        if name == "filter":
            counters["injected"] += 1
            if arg == "finish":
                await _json(send, 200, completion(None, "content_filter"))
            else:
                await _json(send, 400, FILTERED)
            return
        if name == "reset":  # headers and half an answer, then the connection is cut
            counters["injected"] += 1
            head = [(b"content-type", b"application/json"), (b"content-length", b"1000")]
            await send({"type": "http.response.start", "status": 200, "headers": head})
            await send({"type": "http.response.body", "body": b'{"id": "chatcmpl-cut", ',
                        "more_body": True})  # fmt: skip
            return
        if name == "garbage":  # a valid completion whose content is not the asked JSON
            counters["injected"] += 1
            await _json(send, 200, completion("<html>502 bad gateway</html> {not json"))
            return
        if name == "empty":
            counters["injected"] += 1
            await _json(send, 200, {"id": "chatcmpl-empty", "object": "chat.completion",
                                    "choices": []})  # fmt: skip
            return
        await self._pass(scope, body, send)

    async def _pass(self, scope: Scope, body: bytes, send: Any) -> None:
        counters = self.chaos.counters
        request: Message = json.loads(body) if body else {}
        if self.cassette is not None:
            stored = self.cassette.get(request)
            if stored is not None:
                counters["cassette_hits"] += 1
                await _json(send, 200, stored)
                return
            counters["cassette_misses"] += 1
            if self.replay_only:
                miss = "chaos: cassette miss (record it again: the prompt has changed)"
                await _json(send, 599, {"error": {"message": miss}})
                return
        counters["forwarded"] += 1
        url = self.upstream + scope["path"].removeprefix("/v1")
        headers = {"content-type": "application/json"}
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
            try:
                answer = await client.post(url, content=body, headers=headers)
            except httpx.HTTPError as exc:
                counters["upstream_errors"] += 1
                await _json(send, 502, {"error": {"message": f"chaos: upstream {exc!r}"}})
                return
        if answer.status_code == 200 and self.cassette is not None and self.cassette.record:
            self.cassette.put(request, answer.json())
        await _send(send, answer.status_code, answer.content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=11435)
    parser.add_argument("--upstream", default=UPSTREAM)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--record", type=Path, help="forward and store every answer")
    group.add_argument("--replay", type=Path, help="answer from the cassette only")
    args = parser.parse_args()
    cassette = None
    if args.record or args.replay:
        cassette = Cassette(args.record or args.replay, record=bool(args.record))
    proxy = ChaosProxy(args.upstream, cassette=cassette, replay_only=bool(args.replay))
    uvicorn.run(proxy, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
