"""How often one client may call the tools (docs/day6-design.md section 3.7).

A token bucket per client (its IP on /mcp, "local" on stdio); the table of clients is bounded
(least recently seen dropped), so the public endpoint is neither a free compute service nor a
memory leak.
"""

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from pundit.mcp_server.errors import ToolInputError

MAX_CLIENTS = 1024


@dataclass
class _Bucket:
    tokens: float
    at: float


class RateLimiter:
    def __init__(
        self,
        per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_clients: int = MAX_CLIENTS,
    ) -> None:
        if per_minute < 1:
            raise ValueError("per_minute must be >= 1")
        self.per_minute = per_minute
        self._clock = clock
        self._max_clients = max_clients
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
        self.refused = 0

    def take(self, client: str, cost: int = 1) -> None:
        """Spend `cost` tokens of `client`, or refuse with the time to wait. A cost above the
        bucket counts as a full bucket, so a small limit never forbids a tool for good."""
        cost = min(cost, self.per_minute)
        now = self._clock()
        bucket = self._buckets.get(client)
        if bucket is None:
            bucket = _Bucket(tokens=float(self.per_minute), at=now)
            self._buckets[client] = bucket
            while len(self._buckets) > self._max_clients:
                self._buckets.popitem(last=False)
        else:
            refill = (now - bucket.at) * self.per_minute / 60
            bucket.tokens = min(float(self.per_minute), bucket.tokens + refill)
            bucket.at = now
        self._buckets.move_to_end(client)
        if bucket.tokens < cost:
            self.refused += 1
            wait = (cost - bucket.tokens) * 60 / self.per_minute
            raise ToolInputError(f"rate limited: retry in {max(1, round(wait))} s")
        bucket.tokens -= cost

    def __len__(self) -> int:
        return len(self._buckets)
