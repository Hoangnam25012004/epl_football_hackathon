"""Replay pacing with a fake clock (design section 12.1, replay tests)."""

import asyncio
from collections.abc import Sequence

import pytest
from builders import MatchBuilder
from helpers import generated

from pundit.domain.events import Event
from pundit.engine.replay import FakeClock, ReplayItem, describe_event, replay, status_line
from pundit.state.match_state import MatchState


def collect(events: Sequence[Event], clock: FakeClock, **kwargs: float) -> list[ReplayItem]:
    async def run() -> list[ReplayItem]:
        return [item async for item in replay(events, clock=clock, **kwargs)]  # type: ignore[arg-type]

    return asyncio.run(run())


def small_match() -> list[Event]:
    b = MatchBuilder()
    b.at("0:00").kick_off("H9", "H8")
    b.at("0:01").pass_("H8", "H6")
    b.at("0:05").pass_("H6", "H4")
    return b.events


def test_sleeps_follow_match_time_divided_by_speed() -> None:
    clock = FakeClock()
    items = collect(small_match(), clock, speed=10)
    assert [i.event.seq for i in items] == list(range(5))
    assert clock.sleeps == pytest.approx([0.1, 0.4])


def test_a_slow_consumer_does_not_drift() -> None:
    clock = FakeClock()

    async def run() -> None:
        async for item in replay(small_match(), speed=10, clock=clock):
            if item.event.seq == 3:
                clock.t += 0.3  # the consumer is slow on this event

    asyncio.run(run())
    assert clock.t == pytest.approx(0.5)  # absolute schedule: ends at 5 s / 10
    assert clock.sleeps == pytest.approx([0.1, 0.1])


def test_from_minute_fast_forwards_but_yields_everything() -> None:
    match = generated("dominant_loser", 0)
    clock = FakeClock()
    items = collect(match.events, clock, speed=1000, from_minute=60)
    assert len(items) == len(match.events)
    flags = [item.catching_up for item in items]
    first_live = flags.index(False)
    assert all(flags[:first_live]) and not any(flags[first_live:])  # the switch happens once
    assert items[first_live].event.minute >= 60
    state = MatchState(match.meta)
    for item in items:
        state.apply(item.event)
    assert state.phase == "post"


def test_half_time_pause_is_added_once() -> None:
    match = generated("balanced", 1)
    clock = FakeClock()
    collect(match.events, clock, speed=1_000, halftime_pause_s=5.0)
    last = match.events[-1]
    assert clock.t == pytest.approx(last.match_ms / 1000 / 1_000 + 5.0, abs=1e-6)


def test_bad_speed_is_rejected() -> None:
    with pytest.raises(ValueError):
        collect(small_match(), FakeClock(), speed=0)


def test_closing_the_replay_stops_it() -> None:
    clock = FakeClock()

    async def run() -> int:
        stream = replay(small_match(), speed=10, clock=clock)
        seen = 0
        async for _ in stream:
            seen += 1
            if seen == 2:
                await stream.aclose()  # type: ignore[attr-defined]
                break
        return seen

    assert asyncio.run(run()) == 2


def test_every_event_type_can_be_described() -> None:
    match = generated("comeback_var", 0)
    state = MatchState(match.meta)
    lines = 0
    snapshot = state.snapshot()
    for event in match.events:
        state.apply(event)
        if event.type in ("goal", "period_end", "match_end"):  # only these lines show the score
            snapshot = state.snapshot()
        line = describe_event(event, match.meta, snapshot)
        if event.type == "player_physical":
            assert line is None
        else:
            assert line is not None and line.startswith("[")
            lines += 1
    assert lines > 1000
    status = status_line(match.meta, state.snapshot())
    assert "POSSESSION IRO" in status and "BALL IN PLAY" in status
