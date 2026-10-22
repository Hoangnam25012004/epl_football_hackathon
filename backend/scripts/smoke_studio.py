"""Live smoke test of the studio over HTTP and WebSocket (docs/day3-design.md section 17, P5).

Starts the API (mock LLM, studio on) on a free port. Three viewers (analyst-vi, a casual
Vietnamese beginner who supports HAR, casual-en) watch dominant_loser-42 at 20x; claims are
verified while the match plays; the replay is stopped mid-match and resumed; then the server
shuts down. Checks: one numbering for every viewer, items after their event (S-8), payloads
that follow each profile, both recaps in two revisions, claims answered, at most the job in
flight lost at the stop, no error in the log, no task left.

    cd backend
    uv run python scripts/smoke_studio.py [--speed 20]
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
from typing import Any

import httpx
import uvicorn
from websockets.asyncio.client import connect

from pundit.agents.triggers import detect_triggers
from pundit.api.app import create_app
from pundit.engine.generator import generate
from pundit.llm.settings import Settings

MATCH = "dominant_loser-42"
PROFILES = {
    "analyst-vi": "",
    "casual-vi-HAR-beginner": "mode=casual&lang=vi&club=HAR&beginner=1",
    "casual-en": "mode=casual&lang=en",
}
CLAIMS = (
    "Đội nhà áp đảo hiệp 2",
    "Kestrel won despite lower xG",
    "HAR có nhiều cú sút hơn KES",
    "Harbor Lions FC dominated possession",
    "KES chưa có thẻ đỏ nào",
)
Message = dict[str, Any]


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


def is_last(message: Message) -> bool:
    payload = message["payload"]
    return bool(
        message["type"] == "recap"
        and payload.get("scope") == "FULL"
        and payload.get("revision") == 2
    )


async def watch(url: str, messages: list[Message], done: asyncio.Event) -> None:
    async with connect(url, max_size=None) as ws:
        async for raw in ws:
            message = json.loads(raw)
            messages.append(message)
            if is_last(message):
                done.set()
                return


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}{': ' + detail if detail else ''}")
    return ok


async def main(speed: float) -> int:
    errors = Errors()
    logging.getLogger().addHandler(errors)
    app = create_app(Settings())  # mock LLM, studio on
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        await asyncio.sleep(0.05)
    match = generate("dominant_loser", 42)
    triggers = detect_triggers(match.meta, match.events)
    seq_of = {t.id: t.seq for t in triggers}
    minutes = (match.events[-1].match_ms / 60_000) / speed
    print(f"server on port {port}; {MATCH} at {speed:g}x takes about {minutes:.1f} min")
    streams: dict[str, list[Message]] = {name: [] for name in PROFILES}
    done = {name: asyncio.Event() for name in PROFILES}
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=60) as http:
        created = await http.post(
            "/matches",
            json={"scenario": "dominant_loser", "seed": 42, "speed": speed, "autostart": False},
        )
        created.raise_for_status()
        watchers = [
            asyncio.create_task(
                watch(f"ws://127.0.0.1:{port}/ws/match/{MATCH}?{query}", streams[name], done[name])
            )
            for name, query in PROFILES.items()
        ]
        await asyncio.sleep(0.5)  # every viewer has its hello
        await http.post(f"/matches/{MATCH}/start")
        await asyncio.sleep(minutes * 60 * 0.25)
        answers = await asyncio.gather(
            *(http.post("/claims/verify", json={"match_id": MATCH, "text": c}) for c in CLAIMS)
        )
        await asyncio.sleep(minutes * 60 * 0.15)
        await http.post(f"/matches/{MATCH}/stop")
        await asyncio.sleep(1.5)
        await http.post(f"/matches/{MATCH}/start")
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in done.values())),
            timeout=minutes * 60 + 120,
        )
        health = (await http.get("/healthz")).json()
        info = (await http.get(f"/matches/{MATCH}")).json()["info"]
        for task in watchers:
            task.cancel()
    server.should_exit = True
    thread.join(timeout=15)
    leftover = [
        s.match_id
        for s in app.state.manager.sessions.values()
        if (s._task is not None and not s._task.done())
        or (s.director is not None and s.director.active())
    ]

    ok = True
    reference = streams["analyst-vi"]
    numbers = [m["n"] for m in reference]
    for name, stream in streams.items():
        ok &= check(f"{name}: same numbering", [m["n"] for m in stream] == numbers)
    ok &= check(
        "numbering has no gap", numbers == list(range(numbers[0], numbers[0] + len(numbers)))
    )
    event_n = {m["payload"]["seq"]: m["n"] for m in reference if m["type"] == "event"}
    late = [
        m["payload"]["trigger_id"]
        for m in reference
        if m["type"] in ("commentary", "talking_point", "lower_third")
        and seq_of[m["payload"]["trigger_id"]] in event_n
        and m["n"] <= event_n[seq_of[m["payload"]["trigger_id"]]]
    ]
    ok &= check("S-8 items after their event", not late, ", ".join(late[:5]))
    languages = {
        name: {m["payload"]["language"] for m in stream if m["type"] == "commentary"}
        for name, stream in streams.items()
    }
    ok &= check(
        "payloads follow the profile",
        languages == {"analyst-vi": {"vi"}, "casual-vi-HAR-beginner": {"vi"}, "casual-en": {"en"}},
        str(languages),
    )
    beginner = [
        m["payload"]["explanation"]["rule"]
        for m in streams["casual-vi-HAR-beginner"]
        if m["type"] == "commentary" and m["payload"]["explanation"]
    ]
    framed = [
        m
        for m in streams["casual-vi-HAR-beginner"]
        if m["type"] == "commentary" and m["payload"]["framing"]
    ]
    ok &= check("beginner explanations and club framing", "offside" in beginner and bool(framed))
    recaps = [
        (m["payload"]["scope"], m["payload"]["revision"]) for m in reference if m["type"] == "recap"
    ]
    ok &= check(
        "recaps H1 and FULL, two revisions each",
        recaps == [("H1", 1), ("H1", 2), ("FULL", 1), ("FULL", 2)],
        str(recaps),
    )
    verdicts = [r.json().get("verdict") for r in answers]
    ok &= check(
        "claims answered while the match plays",
        all(r.status_code == 200 for r in answers),
        ", ".join(str(v) for v in verdicts),
    )
    statuses = [m["payload"]["session"] for m in reference if m["type"] == "status"]
    ok &= check(
        "stopped and resumed", "stopped" in statuses and statuses[-1] == "finished", str(statuses)
    )
    studio = info.get("studio", {})
    ok &= check(
        "no job lost except the one in flight at the stop",
        studio.get("dropped", 99) <= 1 and studio.get("errors", 1) == 0,
        json.dumps(studio, ensure_ascii=False),
    )
    ok &= check("health has the studio block", health.get("studio", {}).get("enabled") is True)
    ok &= check("no error in the log", not errors.records, "; ".join(errors.records[:3]))
    ok &= check("no task left after shutdown", not leftover, str(leftover))
    print(
        f"\n{len(reference)} messages per viewer · {studio.get('commentary')} commentary · "
        f"{studio.get('talking_points')} talking points · {studio.get('lower_thirds')} "
        f"lower-thirds · studio lag p95 {studio.get('lag_ms_p95')} ms"
    )
    print("PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--speed", type=float, default=20.0)
    started = time.monotonic()
    code = asyncio.run(main(parser.parse_args().speed))
    print(f"({time.monotonic() - started:.0f} s)")
    raise SystemExit(code)
