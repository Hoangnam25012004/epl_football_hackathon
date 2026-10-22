"""Day 3 time budgets (docs/day3-design.md section 16.5). Skipped under coverage."""

import statistics
import sys
import time

import pytest
from helpers import generated, mock_service, run
from studio_helpers import SAMPLES, triggers, view_at

from pundit.agents.contracts import StudioJob
from pundit.agents.graphics import wants_graphic
from pundit.agents.talking_points import wants_talking_point
from pundit.api.app import default_crew
from pundit.api.sessions import MatchSession
from pundit.engine.replay import RealClock
from pundit.llm.settings import Settings
from pundit.orchestration.recap_service import RecapService
from pundit.orchestration.studio_eval import run_offline
from pundit.orchestration.studio_service import StudioService

pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif("coverage" in sys.modules, reason="timings are meaningless under coverage"),
]


def p95(samples: list[float]) -> float:
    return statistics.quantiles(samples, n=20, method="inclusive")[-1]


def test_job_latency_p95() -> None:
    """Three branches p95 < 150 ms; a commentary-only job p95 < 60 ms (mock, in process)."""
    service = StudioService(Settings())
    full, commentary_only = [], []
    for scenario, seed in SAMPLES:
        for trigger in triggers(scenario, seed):
            view = view_at(scenario, seed, trigger.seq)
            started = time.perf_counter()
            run(service.run(StudioJob(trigger=trigger), view))
            elapsed = (time.perf_counter() - started) * 1000
            if wants_talking_point(trigger) and wants_graphic(trigger):
                full.append(elapsed)
            elif not wants_talking_point(trigger) and not wants_graphic(trigger):
                commentary_only.append(elapsed)
    assert p95(full) < 150, p95(full)
    assert p95(commentary_only) < 60, p95(commentary_only)


def test_a_whole_match_offline_under_15_s() -> None:
    settings = Settings()
    started = time.perf_counter()
    run(
        run_offline(
            generated("red_card_swing", 7),  # the busiest sample: 99 triggers
            StudioService(settings, offline=True),
            RecapService(settings),
        )
    )
    assert time.perf_counter() - started < 15


def _replay_seconds(studio: bool, speed: float = 1000.0) -> tuple[float, MatchSession]:
    settings = Settings(studio_enabled=studio)
    session = MatchSession(
        generated("dominant_loser", 42),
        service=mock_service(),
        speed=speed,
        clock=RealClock(),
        studio=default_crew(settings) if studio else None,
    )

    async def play() -> float:
        started = time.perf_counter()
        session.start()
        await session.wait()
        elapsed = time.perf_counter() - started
        if session.director is not None:
            await session.director.idle()
            await session.director.stop()
        return elapsed

    return run(play()), session


def test_the_studio_never_slows_the_replay_much() -> None:
    """Replay at 1000x with and without the studio: at most 20% slower; no job of importance
    2 or more is dropped."""
    without, _ = _replay_seconds(False)
    with_studio, session = _replay_seconds(True)
    assert with_studio <= without * 1.2, (without, with_studio)
    director = session.director
    assert director is not None and director.errors == 0
    expected = sum(1 for t in triggers("dominant_loser", 42) if t.importance >= 2)
    kept = sum(1 for item in director.commentary.values() if item.importance >= 2)
    assert kept == expected


def test_event_to_item_latency_at_200x() -> None:
    """Event -> item p95 < 300 ms on a session paced like a fast live replay."""
    _, session = _replay_seconds(True, speed=200.0)
    director = session.director
    assert director is not None
    lag = director.lag_p95()
    assert lag is not None and lag < 300, lag
