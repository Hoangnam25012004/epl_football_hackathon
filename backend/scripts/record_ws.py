"""Record WebSocket streams as JSONL fixtures for the frontend tests (docs/day4-design.md
section 11.2).

Fixture A (default): a whole match through the real session code (mock LLM, SteppingClock: as
fast as possible and deterministic), one file per viewer profile, plus the `hello` of a client
that joins in the second half. Fixture B (`--offline PATH`): the studio items of an offline run
(`pundit studio --provider ollama --json PATH`), rendered for each profile, so the layout is
tested with the long texts of a real model.

    cd backend
    uv run python scripts/record_ws.py --out ../frontend/tests/fixtures
    uv run python scripts/record_ws.py --offline out.jsonl --out ../frontend/tests/fixtures

Every line is a WsMessage exactly as the server sends it. Day 5: the profile with a voice
(`...-female`) is read by the mock narrator (temporary stores, no speed limit), so its
commentary and recaps carry `speech` (docs/day5-design.md section 12.5).
"""

import argparse
import asyncio
import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from pundit.agents.context import build_match_context
from pundit.agents.contracts import (
    CommentaryItem,
    LowerThird,
    Profile,
    Recap,
    RuleId,
    TalkingPoint,
    WsMessage,
)
from pundit.agents.personalize import render_commentary, render_lower_third, render_recap
from pundit.api.app import default_crew
from pundit.api.hub import Client
from pundit.api.onair import OnAirCommand
from pundit.api.sessions import MatchSession, public_talking_point
from pundit.domain.log import LogView
from pundit.engine.generator import generate
from pundit.engine.replay import SteppingClock
from pundit.llm.settings import Settings
from pundit.orchestration.service import ClaimService
from pundit.speech.narrator import Narrator, build_narrator

PROFILES: dict[str, Profile] = {
    "analyst-en": Profile(mode="analyst", language="en"),
    "casual-vi-beginner-har": Profile(
        mode="casual", language="vi", team="harbor_lions", beginner=True
    ),
    "casual-vi-beginner-har-female": Profile(
        mode="casual", language="vi", team="harbor_lions", beginner=True, voice="female"
    ),
}
JOIN_AT = 0.6  # a client that joins at 60 % of the events (its hello), in the second half


def _write(path: Path, messages: list[WsMessage]) -> None:
    text = "".join(message.model_dump_json() + "\n" for message in messages)
    path.write_bytes(text.encode("utf-8"))
    print(f"wrote {path} ({len(messages)} messages)")


def _drain(client: Client) -> list[WsMessage]:
    found: list[WsMessage] = []
    while not client.queue.empty():
        found.append(client.queue.get_nowait())
    return found


async def _record_live(scenario: str, seed: int, out: Path) -> None:
    with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as store:
        settings = Settings(
            tts_provider="mock",
            tts_max_speed=1_000_000.0,  # the replay is as fast as possible: still read
            audio_cache_dir=cache,
            audio_store_dir=store,
        )
        narrator = build_narrator(settings)
        try:
            await _record_session(settings, narrator, scenario, seed, out)
        finally:
            if narrator is not None:
                await narrator.aclose()


async def _record_session(
    settings: Settings, narrator: Narrator | None, scenario: str, seed: int, out: Path
) -> None:
    live = MatchSession(
        generate(scenario, seed),
        service=ClaimService(settings),
        speed=1000.0,
        clock=SteppingClock(),
        studio=default_crew(settings),
        narrator=narrator,
    )
    clients = {name: live.hub.subscribe(1_000_000, profile) for name, profile in PROFILES.items()}
    hellos = {name: live.hub.state_message("hello", c) for name, c in clients.items()}
    live.start()
    join_at = int(JOIN_AT * len(live.match.events))
    while len(live.state) < join_at and live.status == "running":
        await asyncio.sleep(0)
    late = live.hub.subscribe(1_000_000, PROFILES["analyst-en"])
    late_hello = live.hub.state_message("hello", late)
    director = live.director
    assert director is not None
    taken = next(reversed(director.lower_thirds), None)
    if taken is not None:  # an editor's decision, so the stream has an on_air message
        live.command_on_air(OnAirCommand(action="take", item_id=taken))
        live.command_on_air(OnAirCommand(action="auto"))
    await live.wait()
    await director.idle()
    match_id = live.match_id
    for name, client in clients.items():
        _write(out / f"{match_id}.{name}.jsonl", [hellos[name], *_drain(client)])
    _write(out / f"{match_id}.late-hello.analyst-en.jsonl", [late_hello])
    await live.stop()


def _record_offline(path: Path, scenario: str, seed: int, out: Path) -> None:
    match = generate(scenario, seed)
    context = build_match_context(LogView.from_events(match.meta, match.events[:1]))
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for name, profile in PROFILES.items():
        if profile.voice is not None:
            continue  # speech is the live session's: fixture B is about long texts
        seen: set[RuleId] = set()
        payloads: list[tuple[str, dict[str, Any]]] = []
        for row in rows:
            kind = row["type"]
            if kind == "commentary":
                item = CommentaryItem.model_validate(row["item"])
                view, shown = render_commentary(item, profile, context, seen=seen)
                if shown is not None:
                    seen.add(shown)
                payloads.append(("commentary", view.model_dump(mode="json")))
            elif kind == "talking_point":
                point = public_talking_point(TalkingPoint.model_validate(row["item"]))
                payloads.append(("talking_point", point.model_dump(mode="json")))
            elif kind == "lower_third":
                lower = render_lower_third(LowerThird.model_validate(row["item"]), profile)
                payloads.append(("lower_third", lower.model_dump(mode="json")))
            elif kind == "recap":
                recap = render_recap(Recap.model_validate(row["item"]), profile)
                payloads.append(("recap", recap.model_dump(mode="json")))
        messages = [
            WsMessage(type=kind, n=n, match_id=match.meta.match_id, payload=payload)  # type: ignore[arg-type]
            for n, (kind, payload) in enumerate(payloads, start=1)
        ]
        _write(out / f"{match.meta.match_id}.llm-long.{name}.jsonl", messages)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scenario", default="dominant_loser")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="../frontend/tests/fixtures")
    parser.add_argument("--offline", help="JSONL of `pundit studio --json` (fixture B)")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.offline:
        _record_offline(Path(args.offline), args.scenario, args.seed, out)
    else:
        asyncio.run(_record_live(args.scenario, args.seed, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
