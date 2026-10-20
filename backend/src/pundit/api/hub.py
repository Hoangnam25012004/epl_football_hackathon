"""Broadcast of one session's messages to its WebSocket clients (design section 11.3).

Every message gets the next `n` of the session, so a client can detect a gap or a duplicate.
Each client has a bounded queue: when it is full the queue is cleared and a single `resync`
message (the current state) takes its place. A slow client therefore never blocks the replay
or the other clients. `hello` and `resync` carry the `n` of the last message already sent:
the next message the client receives is `n + 1`.
"""

import asyncio
from collections.abc import Callable
from typing import Any

from pundit.agents.contracts import WsMessage, WsType

DEFAULT_QUEUE_SIZE = 1000


class Client:
    def __init__(self, hub: "Hub", maxsize: int) -> None:
        self.hub = hub
        self.queue: asyncio.Queue[WsMessage] = asyncio.Queue(maxsize=maxsize)
        self.resyncs = 0

    def offer(self, message: WsMessage) -> None:
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            while not self.queue.empty():
                self.queue.get_nowait()
            self.resyncs += 1
            self.queue.put_nowait(self.hub.state_message("resync"))


class Hub:
    def __init__(self, match_id: str, state: Callable[[], dict[str, Any]]) -> None:
        """`state()` returns the payload of `hello` / `resync` (snapshot, moments, claims)."""
        self.match_id = match_id
        self._state = state
        self._n = 0
        self._clients: set[Client] = set()

    @property
    def last_n(self) -> int:
        return self._n

    @property
    def clients(self) -> int:
        return len(self._clients)

    def subscribe(self, maxsize: int = DEFAULT_QUEUE_SIZE) -> Client:
        client = Client(self, maxsize)
        self._clients.add(client)
        return client

    def unsubscribe(self, client: Client) -> None:
        self._clients.discard(client)

    def state_message(self, kind: WsType) -> WsMessage:
        return WsMessage(type=kind, n=self._n, match_id=self.match_id, payload=self._state())

    def publish(self, kind: WsType, payload: dict[str, Any]) -> WsMessage:
        self._n += 1
        message = WsMessage(type=kind, n=self._n, match_id=self.match_id, payload=payload)
        for client in list(self._clients):
            client.offer(message)
        return message
