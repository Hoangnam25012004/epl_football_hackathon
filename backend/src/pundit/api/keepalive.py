"""Keepalive, layer 1 (docs/day7-design.md section 2.2): the app pings its own public URL.

A free Space sleeps when nobody visits it. With KEEPALIVE_URL set, a background task asks for
that URL every KEEPALIVE_INTERVAL_S, with some jitter, through the host's proxy, which counts as
a visit. It starts with the server and needs no viewer. A failed ping is only logged and counted
in /healthz: the app never depends on it. Without the variable there is no task (dev, CI).
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("pundit.keepalive")

Fetch = Callable[[str], Awaitable[int]]  # GET the URL, return the HTTP status
Sleep = Callable[[float], Awaitable[None]]
JITTER = 0.1  # each wait is the interval +/- 10 %
FIRST_PING_S = 60.0  # the host's proxy reaches a new container only once it reports running
TIMEOUT_S = 30.0


async def http_get(url: str) -> int:
    import httpx

    async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "pundit-keepalive"})
        return response.status_code


class Keepalive:
    def __init__(
        self,
        url: str,
        interval_s: float,
        *,
        fetch: Fetch = http_get,
        sleep: Sleep = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),  # noqa: TID251 - shown in /healthz
        rng: random.Random | None = None,
    ) -> None:
        self.url = url
        self.interval_s = interval_s
        self._fetch = fetch
        self._sleep = sleep
        self._now = now
        self._rng = rng or random.Random()
        self.checks = 0
        self.failures = 0
        self.last_ok_at: datetime | None = None
        self.last_status: int | None = None
        self.last_error: str | None = None

    def next_wait(self) -> float:
        return self.interval_s * self._rng.uniform(1 - JITTER, 1 + JITTER)

    async def ping(self) -> bool:
        """One ping; never raises (except on cancellation)."""
        self.checks += 1
        try:
            status = await self._fetch(self.url)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the network, the proxy, a sleeping host: only counted
            self.failures += 1
            self.last_status, self.last_error = None, f"{type(exc).__name__}: {exc}"[:200]
            log.warning("keepalive ping to %s failed: %s", self.url, self.last_error)
            return False
        self.last_status = status
        if status >= 400:
            self.failures += 1
            self.last_error = f"HTTP {status}"
            log.warning("keepalive ping to %s answered %d", self.url, status)
            return False
        self.last_ok_at, self.last_error = self._now(), None
        return True

    async def run(self) -> None:
        log.info("keepalive: GET %s every %.0f s", self.url, self.interval_s)
        await self._sleep(min(FIRST_PING_S, self.interval_s))
        while True:
            await self.ping()
            await self._sleep(self.next_wait())

    def health(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "interval_s": self.interval_s,
            "checks": self.checks,
            "failures": self.failures,
            "last_ok_at": self.last_ok_at.isoformat() if self.last_ok_at else None,
            "last_status": self.last_status,
            "last_error": self.last_error,
        }
