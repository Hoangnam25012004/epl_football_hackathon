"""Live smoke test of the voice over HTTP and WebSocket (docs/day5-design.md section 13, V9).

Two runs of dominant_loser-42 on a local server with the mock voice (300 ms per clip):

1. clean: three viewers (casual · vi · HAR · beginner · female; analyst · vi · male; casual · en
   without a voice) download every clip the way the fan player does, and ask the recaps H1 and
   FULL in their voice. Checks: each clip made once (a line said twice is one clip), every
   clip answers 200, the
   recaps are in the durable store, nothing spoken for the English viewer, the right voice for
   each viewer, message -> clip ready p95 under 1.5 s, no error in the log, no task left.
2. faults (MOCK_TTS_FAULTS=error:6, breaker cooldown 10 s): the first syntheses fail (503,
   reason unavailable), the breaker opens (lines come with the note circuit_open: the browser
   reads them), then closes again; every other clip answers 200 and the recaps have audio.
   Fewer faults than the calls in flight would close the breaker at once: a success closes it.

The design restarts the server mid-match with the faults; sessions live in memory, so the
faults run is a second match instead.

    cd backend
    uv run python scripts/smoke_voice.py [--speed 60]
"""

import argparse
import asyncio
import io
import json
import logging
import socket
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from websockets.asyncio.client import connect

from pundit.api.app import create_app
from pundit.engine.generator import generate
from pundit.llm.settings import Settings
from pundit.orchestration.resilience import CircuitBreaker
from pundit.speech.clips import ClipCache
from pundit.speech.durable import build_durable
from pundit.speech.narrator import Narrator, build_synthesizer

MATCH = "dominant_loser-42"
VIEWERS = {
    "casual-vi-HAR-beginner-female": "mode=casual&lang=vi&club=HAR&beginner=1&voice=female",
    "analyst-vi-male": "lang=vi&voice=male",
    "casual-en": "mode=casual&lang=en",
}
VOICES = {
    "casual-vi-HAR-beginner-female": "vi-VN-HoaiMyNeural",
    "analyst-vi-male": "vi-VN-NamMinhNeural",
}
RECAPS = {
    "casual-vi-HAR-beginner-female": "mode=casual&lang=vi&voice=female",
    "analyst-vi-male": "mode=analyst&lang=vi&voice=male",
}
Message = dict[str, Any]


class Errors(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(f"{record.name}: {record.getMessage()}")


@dataclass
class Clip:
    viewer: str
    url: str
    status: int = 0
    reason: str | None = None
    wait_ms: float = 0.0


@dataclass
class Viewer:
    name: str
    messages: list[Message] = field(default_factory=list)
    clips: list[Clip] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)


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


async def fetch(http: httpx.AsyncClient, clip: Clip, arrived: float) -> None:
    """GET the clip like the fan player (it waits while the clip is being made)."""
    response = await http.get(clip.url)
    clip.status = response.status_code
    clip.wait_ms = (time.monotonic() - arrived) * 1000
    if response.status_code == 503:
        clip.reason = response.json().get("reason")


async def watch(
    url: str, viewer: Viewer, http: httpx.AsyncClient, downloads: list[asyncio.Task[None]]
) -> None:
    async with connect(url, max_size=None) as ws:
        async for raw in ws:
            message = json.loads(raw)
            viewer.messages.append(message)
            speech = message["payload"].get("speech") if message["type"] == "commentary" else None
            if speech is not None:
                if speech.get("audio"):
                    clip = Clip(viewer.name, speech["audio"]["url"])
                    viewer.clips.append(clip)
                    downloads.append(asyncio.create_task(fetch(http, clip, time.monotonic())))
                else:
                    viewer.notes.append(speech.get("note") or "?")
            if is_last(message):
                viewer.done.set()
                return


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}{': ' + detail if detail else ''}")
    return ok


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))] if ordered else 0.0


async def run(speed: float, faults: str) -> bool:
    label = "faults" if faults else "clean"
    print(f"\n=== {label} run{f' (MOCK_TTS_FAULTS={faults})' if faults else ''} ===")
    errors = Errors()
    logging.getLogger().addHandler(errors)
    with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as store:
        settings = Settings(
            tts_provider="mock",
            mock_tts_latency_ms=300.0,
            mock_tts_faults=faults,
            audio_cache_dir=cache,
            audio_store_dir=store,
            tts_max_speed=max(20.0, speed),
        )
        narrator = Narrator(
            settings,
            synthesizer=build_synthesizer(settings),
            cache=ClipCache(settings.cache_dir, settings.audio_cache_max_mb * 1024 * 1024),
            durable=build_durable(settings),
            breaker=CircuitBreaker(threshold=3, cooldown_s=10.0),
        )
        app = create_app(settings, narrator=narrator)
        port = free_port()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        while not server.started:
            await asyncio.sleep(0.05)
        match = generate("dominant_loser", 42)
        minutes = (match.events[-1].match_ms / 60_000) / speed
        print(f"server on port {port}; {MATCH} at {speed:g}x takes about {minutes:.1f} min")
        viewers = {name: Viewer(name) for name in VIEWERS}
        downloads: list[asyncio.Task[None]] = []
        recap_clips: list[Clip] = []
        recap_ids: list[str] = []
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=60) as http:
            created = await http.post(
                "/matches",
                json={
                    "scenario": "dominant_loser",
                    "seed": 42,
                    "speed": speed,
                    "autostart": False,
                },
            )
            created.raise_for_status()
            watchers = [
                asyncio.create_task(
                    watch(
                        f"ws://127.0.0.1:{port}/ws/match/{MATCH}?{query}",
                        viewers[name],
                        http,
                        downloads,
                    )
                )
                for name, query in VIEWERS.items()
            ]
            await asyncio.sleep(0.5)  # every viewer has its hello
            await http.post(f"/matches/{MATCH}/start")
            await asyncio.wait_for(
                asyncio.gather(*(viewer.done.wait() for viewer in viewers.values())),
                timeout=minutes * 60 + 120,
            )
            for name, query in RECAPS.items():  # "listen to the recap" in each voice
                for scope in ("H1", "FULL"):
                    recap = (await http.get(f"/matches/{MATCH}/recap?scope={scope}&{query}")).json()
                    audio = (recap.get("speech") or {}).get("audio")
                    if audio is None:
                        recap_clips.append(Clip(name, f"{scope}: no audio", status=-1))
                        continue
                    recap_ids.append(audio["clip_id"])
                    clip = Clip(name, audio["url"])
                    recap_clips.append(clip)
                    await fetch(http, clip, time.monotonic())
            await asyncio.gather(*downloads)
            await narrator.idle()
            health = (await http.get("/healthz")).json()["speech"]
            for task in watchers:
                task.cancel()
        server.should_exit = True
        thread.join(timeout=20)
        stored = {path.name for path in Path(store).rglob("*") if path.is_file()}
        leftover_sessions = [
            s.match_id
            for s in app.state.manager.sessions.values()
            if (s._task is not None and not s._task.done())
            or (s.director is not None and s.director.active())
        ]
        leftover_clips = narrator.active()
    logging.getLogger().removeHandler(errors)

    ok = True
    clips = [clip for viewer in viewers.values() for clip in viewer.clips]
    statuses: dict[int, int] = {}
    for clip in clips:
        statuses[clip.status] = statuses.get(clip.status, 0) + 1
    english = viewers["casual-en"]
    spoken_en = [
        m
        for m in english.messages
        if m["type"] in ("commentary", "recap") and m["payload"].get("speech")
    ]
    ok &= check(
        "nothing spoken for the viewer without a voice", not spoken_en and not english.clips
    )
    for name, voice in VOICES.items():
        used = {
            m["payload"]["speech"]["voice"]
            for m in viewers[name].messages
            if m["type"] == "commentary" and m["payload"].get("speech")
        }
        ok &= check(f"{name}: one voice, {voice}", used == {voice}, str(used))
    distinct = {clip.url for clip in clips} | {clip.url for clip in recap_clips if clip.status >= 0}
    made = {clip.url for clip in [*clips, *recap_clips] if clip.status == 200}
    failed = sum(health["errors"].values())
    ok &= check(
        "each clip made once (a failed one may be made by a later request)",
        health["synthesized"] == len(made) and (bool(faults) or failed == 0),
        f"{health['synthesized']} made for {len(made)} clips heard, {len(distinct)} distinct, "
        f"{failed} failed attempts",
    )
    recap_ok = all(clip.status == 200 for clip in recap_clips) and len(recap_clips) == 4
    ok &= check(
        "recaps H1 and FULL in both voices answer 200",
        recap_ok,
        ", ".join(f"{c.viewer} {c.status}" for c in recap_clips),
    )
    ok &= check(
        "recaps are in the durable store",
        all(any(clip_id in name for name in stored) for clip_id in recap_ids),
        f"{len(stored)} files",
    )
    if not faults:
        ok &= check("every clip answers 200", statuses == {200: len(clips)}, str(statuses))
        waits = [clip.wait_ms for clip in clips]
        ok &= check(
            "message -> clip ready p95 under 1.5 s (mock 300 ms)",
            p95(waits) < 1500,
            f"p95 {p95(waits):.0f} ms over {len(waits)} clips",
        )
        ok &= check("no note: every line has a clip", not any(v.notes for v in viewers.values()))
    else:
        ok &= check(
            "every clip answers 200 or 503 with a reason",
            set(statuses) <= {200, 503} and all(c.reason for c in clips if c.status == 503),
            str(statuses),
        )
        reasons = sorted({c.reason or "" for c in clips if c.status == 503})
        ok &= check(
            "the failed clips say why (503: unavailable)",
            reasons == ["unavailable"] and 0 < statuses.get(503, 0) <= failed,
            f"{statuses.get(503, 0)} x {reasons}",
        )
        notes = sorted({note for viewer in viewers.values() for note in viewer.notes})
        ok &= check(
            "the breaker opened (lines for the browser voice)", "circuit_open" in notes, str(notes)
        )
        ok &= check(
            "then closed again; later clips answer 200",
            health["breaker"] == "closed" and statuses.get(200, 0) > 0,
            f"{health['breaker']}, {statuses}",
        )
    ok &= check("no error in the log", not errors.records, "; ".join(errors.records[:3]))
    ok &= check(
        "no task left after shutdown",
        not leftover_sessions and not leftover_clips,
        f"{leftover_sessions} {leftover_clips}",
    )
    print(
        f"{len(clips)} clips fetched by the fan players · {health['synthesized']} synthesized · "
        f"{health['cache_hits']} cache hits · latency p95 {health['latency_ms_p95']} ms · "
        f"breaker {health['breaker']} · errors {health['errors']}"
    )
    return ok


async def main(speed: float) -> int:
    clean = await run(speed, "")
    faulty = await run(speed, "error:6")
    ok = clean and faulty
    print("\nPASSED" if ok else "\nFAILED")
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
