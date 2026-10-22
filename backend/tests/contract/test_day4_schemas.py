"""Day 4 schemas (docs/day4-design.md section 3.8): one payload model per WebSocket message
type, and every message of a real session validates against it and against the exported JSON
Schema the frontend generates its types from."""

import asyncio
import json
from typing import Any, get_args

from helpers import generated, mock_service, run
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter

from pundit.agents.contracts import WsMessage, WsType
from pundit.api.app import default_crew
from pundit.api.onair import OnAirCommand
from pundit.api.payloads import WsPayloads
from pundit.api.sessions import MatchSession
from pundit.cli import _schemas
from pundit.engine.replay import SteppingClock
from pundit.llm.settings import Settings

DAY4_FILES = {
    "create_match.schema.json",
    "events_page.schema.json",
    "health.schema.json",
    "match_info.schema.json",
    "match_view.schema.json",
    "on_air.schema.json",
    "on_air_command.schema.json",
    "player_brief.schema.json",
    "scenario_info.schema.json",
    "stats_panel.schema.json",
    "studio_item_detail.schema.json",
    "timeline.schema.json",
    "ws_payloads.schema.json",
}


def test_every_message_type_has_a_payload_model() -> None:
    assert set(WsPayloads.model_fields) == set(get_args(WsType))
    assert set(_schemas()) >= DAY4_FILES


def recorded_session() -> tuple[list[WsMessage], list[WsMessage]]:
    """Every message of dominant_loser-42 with the studio on, and two hellos (Day 2 client and
    studio client)."""

    async def scenario() -> tuple[list[WsMessage], list[WsMessage]]:
        live = MatchSession(
            generated("dominant_loser", 42),
            service=mock_service(),
            speed=1000.0,
            clock=SteppingClock(),
            studio=default_crew(Settings()),
        )
        client = live.hub.subscribe(maxsize=1_000_000)
        live.start()
        while len(live.state) < 1500:
            await asyncio.sleep(0)
        assert live.director is not None
        item = next(iter(live.director.lower_thirds), None)
        if item is not None:
            live.command_on_air(OnAirCommand(action="take", item_id=item))
        await live.wait()
        await live.director.idle()
        hellos = [live.hub.state_message("hello", client), live.hub.state_message("resync")]
        messages: list[WsMessage] = []
        while not client.queue.empty():
            messages.append(client.queue.get_nowait())
        await live.stop()
        return messages, hellos

    return run(scenario())


def test_real_messages_validate_against_the_payload_models_and_schema() -> None:
    messages, hellos = recorded_session()
    schema = json.loads(_schemas()["ws_payloads.schema.json"])
    defs = schema["$defs"]
    validators = {
        kind: Draft202012Validator({"$defs": defs, **schema["properties"][kind]})
        for kind in schema["properties"]
    }
    adapters: dict[str, TypeAdapter[Any]] = {
        kind: TypeAdapter(field.annotation) for kind, field in WsPayloads.model_fields.items()
    }
    seen = set()
    for message in [*hellos, *messages]:
        payload = json.loads(json.dumps(message.payload))  # exactly what goes on the wire
        adapters[message.type].validate_python(payload)
        errors = list(validators[message.type].iter_errors(payload))
        assert errors == [], (message.type, errors[0].message)
        seen.add(message.type)
    expected = {"hello", "resync", "event", "snapshot", "moment", "status", "talking_point",
                "lower_third", "commentary", "recap", "on_air"}  # fmt: skip
    assert expected <= seen
