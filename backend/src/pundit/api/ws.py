"""WebSocket `/ws/match/{id}` (design section 11.3): `hello`, then the session's messages.

A sender loop forwards the client's queue; a receiver loop notices a disconnect even when the
match is quiet. Either one ending closes the connection and unsubscribes the client.
"""

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from pundit.api.hub import Client
from pundit.api.sessions import SessionManager

router = APIRouter()
UNKNOWN_MATCH = 4404


async def _send(websocket: WebSocket, client: Client) -> None:
    while True:
        message = await client.queue.get()
        await websocket.send_text(message.model_dump_json())


async def _receive(websocket: WebSocket) -> None:
    while True:
        await websocket.receive_text()  # clients do not send commands yet; this detects closing


@router.websocket("/ws/match/{match_id}")
async def match_stream(websocket: WebSocket, match_id: str) -> None:
    sessions: SessionManager = websocket.app.state.manager
    session = sessions.get(match_id)
    if session is None:
        await websocket.close(code=UNKNOWN_MATCH, reason=f"unknown match {match_id}")
        return
    await websocket.accept()
    client = session.hub.subscribe(session.queue_size)
    try:
        await websocket.send_text(session.hub.state_message("hello").model_dump_json())
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
