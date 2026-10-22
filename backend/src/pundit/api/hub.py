"""Broadcast of one session's messages to its WebSocket clients (design section 11.3; Day 3
section 11.6).

Every message gets the next `n` of the session, so a client can detect a gap or a duplicate.
Each client has a bounded queue: when it is full the queue is cleared and a single `resync`
message (the current state) takes its place. A slow client therefore never blocks the replay
or the other clients. `hello` and `resync` carry the `n` of the last message already sent:
the next message the client receives is `n + 1`.

Day 3: each client has a viewer profile. `publish_each` sends one message number to every
client with a payload per client; the caller renders once per profile and the hub builds one
WsMessage per distinct payload. No client ever misses a number because of its profile.
"""

import asyncio
from collections.abc import Callable
from typing import Any

from pundit.agents.contracts import Profile, RuleId, WsMessage, WsType

DEFAULT_QUEUE_SIZE = 1000


class Client:
    def __init__(self, hub: "Hub", maxsize: int, profile: Profile | None = None) -> None:
        self.hub = hub
        self.queue: asyncio.Queue[WsMessage] = asyncio.Queue(maxsize=maxsize)
        self.resyncs = 0
        self.profile = profile or Profile()
        self.seen_rules: set[RuleId] = set()  # rules already explained to this viewer

    def offer(self, message: WsMessage) -> None:
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            while not self.queue.empty():
                self.queue.get_nowait()
            self.resyncs += 1
            self.queue.put_nowait(self.hub.state_message("resync", self))


class Hub:
    def __init__(
        self,
        match_id: str,
        state: Callable[[], dict[str, Any]],
        *,
        state_for: Callable[[Client], dict[str, Any]] | None = None,
    ) -> None:
        """`state()` returns the payload of `hello` / `resync` (snapshot, moments, claims);
        `state_for(client)`, when given, the same rendered for the client's profile."""
        self.match_id = match_id
        self._state = state
        self._state_for = state_for
        self._n = 0
        self._clients: set[Client] = set()

    @property
    def last_n(self) -> int:
        return self._n

    @property
    def clients(self) -> int:
        return len(self._clients)

    def profile_keys(self) -> set[str]:
        return {client.profile.key() for client in self._clients}

    def subscribe(
        self, maxsize: int = DEFAULT_QUEUE_SIZE, profile: Profile | None = None
    ) -> Client:
        client = Client(self, maxsize, profile)
        self._clients.add(client)
        return client

    def unsubscribe(self, client: Client) -> None:
        self._clients.discard(client)

    def state_message(self, kind: WsType, client: Client | None = None) -> WsMessage:
        if self._state_for is not None and client is not None:
            payload = self._state_for(client)
        else:
            payload = self._state()
        return WsMessage(type=kind, n=self._n, match_id=self.match_id, payload=payload)

    def publish(self, kind: WsType, payload: dict[str, Any]) -> WsMessage:
        self._n += 1
        message = WsMessage(type=kind, n=self._n, match_id=self.match_id, payload=payload)
        for client in list(self._clients):
            client.offer(message)
        return message

    def publish_each(self, kind: WsType, payload_for: Callable[[Client], dict[str, Any]]) -> int:
        """One message number, one payload per client. Clients that get the same payload
        object (the same profile) share one WsMessage."""
        self._n += 1
        built: dict[int, tuple[dict[str, Any], WsMessage]] = {}  # keeps payloads alive
        for client in list(self._clients):
            payload = payload_for(client)
            entry = built.get(id(payload))
            if entry is None:
                message = WsMessage(type=kind, n=self._n, match_id=self.match_id, payload=payload)
                entry = built[id(payload)] = (payload, message)
            client.offer(entry[1])
        return self._n
