"""Keepalive, layer 1 (docs/day7-design.md section 2.2): the app pings its own public URL on a
fake clock; it is off without KEEPALIVE_URL and a failed ping never stops the app."""

import asyncio
import random
import re
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from helpers import api_settings, run

from pundit.api.app import create_app
from pundit.api.keepalive import FIRST_PING_S, JITTER, Keepalive
from pundit.engine.replay import SteppingClock
from pundit.llm.settings import ConfigError, Settings

URL = "https://space.example/healthz"
NOW = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)


class Stop(Exception):
    """Ends the loop after the number of waits a test wants to see."""


def keepalive(answers: list[int | Exception], waits: list[float], *, limit: int) -> Keepalive:
    async def fetch(url: str) -> int:
        assert url == URL
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) > limit:
            raise Stop

    return Keepalive(URL, 1200, fetch=fetch, sleep=sleep, now=lambda: NOW, rng=random.Random(7))


def test_pings_every_interval_with_jitter_after_a_first_short_wait() -> None:
    waits: list[float] = []
    alive = keepalive([200, 200, 200], waits, limit=3)
    with pytest.raises(Stop):
        run(alive.run())
    assert waits[0] == FIRST_PING_S
    assert all(1200 * (1 - JITTER) <= w <= 1200 * (1 + JITTER) for w in waits[1:])
    assert len(set(waits[1:])) == len(waits[1:])  # jitter: not the same wait every time
    health = alive.health()
    assert (health["checks"], health["failures"], health["last_status"]) == (3, 0, 200)
    assert health["last_ok_at"] == NOW.isoformat() and health["last_error"] is None


def test_a_network_error_or_an_error_status_is_counted_and_the_loop_goes_on() -> None:
    waits: list[float] = []
    alive = keepalive([httpx.ConnectError("no route"), 503, 200, 502], waits, limit=4)
    with pytest.raises(Stop):
        run(alive.run())
    health = alive.health()
    assert (health["checks"], health["failures"]) == (4, 3)
    assert health["last_ok_at"] == NOW.isoformat()  # the 200 in between
    assert (health["last_status"], health["last_error"]) == (502, "HTTP 502")


def test_a_failure_leaves_the_last_success_and_names_the_error() -> None:
    alive = keepalive([200, httpx.ReadTimeout("slow")], [], limit=9)
    assert run(alive.ping()) and not run(alive.ping())
    health = alive.health()
    assert health["last_ok_at"] == NOW.isoformat() and health["last_status"] is None
    assert health["last_error"] == "ReadTimeout: slow"


def test_cancellation_is_not_swallowed() -> None:
    async def hang(url: str) -> int:
        await asyncio.sleep(3600)
        return 200

    async def scenario() -> None:
        alive = Keepalive(URL, 1200, fetch=hang, sleep=lambda s: asyncio.sleep(0))
        task = asyncio.create_task(alive.run())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert alive.failures == 0

    run(scenario())


def test_off_without_the_variable_and_on_the_health_page_with_it() -> None:
    with TestClient(create_app(api_settings(), clock_factory=SteppingClock)) as client:
        assert "keepalive" not in client.get("/healthz").json()

    async def never(seconds: float) -> None:  # the first wait lasts the whole test: no ping
        await asyncio.Event().wait()

    alive = Keepalive(URL, 1200, sleep=never)
    app = create_app(api_settings(), clock_factory=SteppingClock, keepalive=alive)
    with TestClient(app) as client:
        body = client.get("/healthz").json()["keepalive"]
    assert body == {"url": URL, "interval_s": 1200, "checks": 0, "failures": 0,
                    "last_ok_at": None, "last_status": None, "last_error": None}  # fmt: skip


def test_the_health_page_names_the_build_only_when_the_image_has_one() -> None:
    assert Settings.from_env({"PUNDIT_BUILD": "d8e1488"}).build == "d8e1488"
    with TestClient(create_app(api_settings(build="d8e1488"))) as client:
        assert client.get("/healthz").json()["build"] == "d8e1488"
    with TestClient(create_app(api_settings())) as client:
        assert "build" not in client.get("/healthz").json()


def test_settings_build_the_task_from_the_environment() -> None:
    settings = Settings.from_env({"KEEPALIVE_URL": URL, "KEEPALIVE_INTERVAL_S": "900"})
    assert (settings.keepalive_url, settings.keepalive_interval_s) == (URL, 900)
    app = create_app(api_settings(keepalive_url=URL, keepalive_interval_s=900))
    assert app.state.keepalive.url == URL and app.state.keepalive.interval_s == 900
    assert Settings.from_env({}).keepalive_url is None


@pytest.mark.parametrize(
    ("env", "problem"),
    [
        ({"KEEPALIVE_URL": "space.example/healthz"}, "KEEPALIVE_URL must be an http(s) URL"),
        ({"KEEPALIVE_INTERVAL_S": "30"}, "KEEPALIVE_INTERVAL_S must be at least 60"),
        ({"KEEPALIVE_INTERVAL_S": "soon"}, "KEEPALIVE_INTERVAL_S must be a number"),
    ],
)
def test_settings_refuse_a_wrong_value(env: dict[str, str], problem: str) -> None:
    with pytest.raises(ConfigError, match=re.escape(problem)):
        Settings.from_env(env)
