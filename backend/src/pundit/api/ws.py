"""WebSocket `/ws/match/{id}` (design section 11.3; Day 3 sections 11.1 and 15.2): `hello`, then
the session's messages.

A sender loop forwards the client's queue; a receiver loop notices a disconnect even when the
match is quiet. Either one ending closes the connection and unsubscribes the client.

With the studio on, the query string is the viewer's profile (`mode`, `lang`, `club`, `player`,
`beginner`, `metric`). A bad profile is accepted and then closed with 4422 and the reason; a
session already serving 64 clients or 32 different profiles closes the new one with 4429.
"""

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from pundit.agents.contracts import Profile
from pundit.agents.personalize import ProfileError, parse_profile
from pundit.api.hub import Client
from pundit.api.sessions import MatchSession, SessionManager

router = APIRouter()
UNKNOWN_MATCH = 4404
BAD_PROFILE = 4422
TOO_MANY = 4429
MAX_CLIENTS = 64
MAX_PROFILES = 32
MAX_REASON_BYTES = 120  # a close reason is at most 123 bytes


def close_reason(text: str) -> str:
    return text.encode("utf-8")[:MAX_REASON_BYTES].decode("utf-8", "ignore")


async def _send(websocket: WebSocket, client: Client) -> None:
    while True:
        message = await client.queue.get()
        await websocket.send_text(message.model_dump_json())


async def _receive(websocket: WebSocket) -> None:
    while True:
        await websocket.receive_text()  # clients do not send commands yet; this detects closing


def _refusal(session: MatchSession, profile: Profile) -> str | None:
    hub = session.hub
    if hub.clients >= MAX_CLIENTS:
        return f"this match already has {MAX_CLIENTS} viewers"
    keys = hub.profile_keys()
    if profile.key() not in keys and len(keys) >= MAX_PROFILES:
        return f"this match already serves {MAX_PROFILES} different profiles"
    return None


@router.websocket("/ws/match/{match_id}")
async def match_stream(websocket: WebSocket, match_id: str) -> None:
    sessions: SessionManager = websocket.app.state.manager
    session = sessions.get(match_id)
    if session is None:
        await websocket.close(code=UNKNOWN_MATCH, reason=f"unknown match {match_id}")
        return
    profile = Profile()
    if session.director is not None:  # Day 2 clients (no query) get the default profile
        try:
            profile = parse_profile(websocket.query_params, session.context)
        except ProfileError as exc:
            await websocket.accept()
            await websocket.close(code=BAD_PROFILE, reason=close_reason(str(exc)))
            return
        refusal = _refusal(session, profile)
        if refusal is not None:
            await websocket.accept()
            await websocket.close(code=TOO_MANY, reason=close_reason(refusal))
            return
    await websocket.accept()
    client = session.hub.subscribe(session.queue_size, profile)
    try:
        await websocket.send_text(session.hub.state_message("hello", client).model_dump_json())
        tasks = [
            asyncio.create_task(_send(websocket, client)),
            asyncio.create_task(_receive(websocket)),
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect):
                await task
        for task in done:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                task.result()
    except WebSocketDisconnect:
        pass
    finally:
        session.hub.unsubscribe(client)
