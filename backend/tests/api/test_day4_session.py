"""Day 4 session features without HTTP (docs/day4-design.md sections 3.1-3.5): pause, resume
and speed keep every event once and in order, studio jobs survive a pause, a restart tells the old
clients to reconnect once the new session exists, editors' on-air decisions, read-only panels."""

import asyncio

import pytest
from helpers import generated, mock_service, run
from hypothesis import given, settings
from hypothesis import strategies as st

from pundit.agents.contracts import WsMessage
from pundit.api.app import default_crew
from pundit.api.hub import Client
from pundit.api.onair import OnAirCommand, OnAirError, OnAirState, apply_command
from pundit.api.panels import (
    MARKER_KINDS,
    PERIOD_WINDOWS,
    STATS_PANEL,
    events_by_id,
    last_events,
    momentum_display,
    stats_panel,
    timeline,
)
from pundit.api.sessions import MatchSession, SessionManager
from pundit.domain.log import EventLog
from pundit.engine.replay import SteppingClock
from pundit.llm.settings import Settings
from pundit.stats import MetricParams, compute, display
from pundit.stats.tactical import momentum_series

MATCH = ("dominant_loser", 42)


def studio_session(scenario: str = "dominant_loser", seed: int = 42) -> MatchSession:
    return MatchSession(
        generated(scenario, seed),
        service=mock_service(),
        speed=1000.0,
        clock=SteppingClock(),
        studio=default_crew(Settings()),
    )


def drain(client: Client) -> list[WsMessage]:
    messages: list[WsMessage] = []
    while not client.queue.empty():
        messages.append(client.queue.get_nowait())
    return messages


async def until(condition: object, limit: int = 200_000) -> None:
    for _ in range(limit):
        if callable(condition) and condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true")


def event_seqs(messages: list[WsMessage]) -> list[int]:
    return [int(m.payload["seq"]) for m in messages if m.type == "event"]


# --------------------------------------------------------------------------- replay control


def test_pause_and_resume_lose_or_repeat_no_event() -> None:
    async def scenario() -> tuple[MatchSession, list[WsMessage], int, int]:
        live = studio_session()
        client = live.hub.subscribe(maxsize=1_000_000)
        live.start()
        await until(lambda: len(live.state) >= 400)
        await live.pause()
        paused_at = len(live.state)
        for _ in range(500):
            await asyncio.sleep(0)
        still = len(live.state)
        live.resume()
        await live.wait()
        assert live.director is not None
        await live.director.idle()
        return live, drain(client), paused_at, still

    live, messages, paused_at, still = run(scenario())
    match = generated(*MATCH)
    assert still == paused_at  # nothing moves while paused
    assert live.status == "finished" and len(live.state) == len(match.events)
    numbers = [m.n for m in messages]
    assert numbers == list(range(1, len(numbers) + 1))  # one n per message, no gap
    seqs = event_seqs(messages)
    assert seqs == sorted(set(seqs))  # every broadcast event once, in order
    statuses = [m.payload["session"] for m in messages if m.type == "status"]
    assert statuses == ["running", "paused", "running", "finished"]
    assert all("speed" in m.payload and m.payload["restarted"] is False for m in messages
               if m.type == "status")  # fmt: skip


def test_a_pause_keeps_the_studio_jobs_that_stop_would_drop() -> None:
    async def scenario() -> tuple[int, int, bool]:
        live = studio_session()
        live.start()
        await until(lambda: any(m.kind == "goal" for m in live.moments))
        await live.pause()  # the goal's job is queued or running, not finished
        director = live.director
        assert director is not None
        goal = next(m for m in live.moments if m.kind == "goal")
        await director.idle()
        made = any(tp.trigger_id == goal.id for tp in director.talking_points.values())
        stats = director.stats()
        await live.stop()
        return stats["dropped"], stats["processed"], made

    dropped, processed, made = run(scenario())
    assert dropped == 0 and processed > 0 and made


def test_a_new_speed_continues_without_a_stop() -> None:
    async def scenario() -> tuple[MatchSession, list[WsMessage]]:
        live = studio_session()
        client = live.hub.subscribe(maxsize=1_000_000)
        live.start()
        await until(lambda: len(live.state) >= 300)
        await live.set_speed(5.0)
        assert live.status == "running" and live.speed == 5.0
        await live.wait()
        return live, drain(client)

    live, messages = run(scenario())
    statuses = [(m.payload["session"], m.payload["speed"]) for m in messages if m.type == "status"]
    assert statuses == [("running", 1000.0), ("running", 5.0), ("finished", 5.0)]
    seqs = event_seqs(messages)
    assert seqs == sorted(set(seqs)) and live.status == "finished"


def test_speed_while_paused_is_kept_for_the_resume() -> None:
    async def scenario() -> MatchSession:
        live = studio_session()
        live.start()
        await until(lambda: len(live.state) >= 100)
        await live.pause()
        await live.set_speed(20.0)
        assert live.status == "paused"
        with pytest.raises(ValueError):
            await live.set_speed(0)
        live.resume()
        await live.wait()
        return live

    live = run(scenario())
    assert live.status == "finished" and live.speed == 20.0


def test_stop_after_pause_ends_stopped() -> None:
    async def scenario() -> str:
        live = studio_session()
        live.start()
        await until(lambda: len(live.state) >= 50)
        await live.pause()
        await live.stop()
        return live.status

    assert run(scenario()) == "stopped"


def test_restart_tells_old_clients_once_the_new_session_exists() -> None:
    async def scenario() -> tuple[MatchSession, MatchSession, list[object], list[WsMessage]]:
        manager = SessionManager(mock_service(), clock_factory=SteppingClock)
        old = await manager.create(*MATCH, speed=1000.0, autostart=False)
        client = old.hub.subscribe()
        registered: list[object] = []
        announce = old.announce_restart

        def spy() -> None:
            registered.append(manager.get(old.match_id))
            announce()

        old.announce_restart = spy  # type: ignore[method-assign]
        new = await manager.create(*MATCH, speed=1000.0, restart=True, autostart=False)
        return old, new, registered, drain(client)

    old, new, registered, messages = run(scenario())
    assert new is not old and registered == [new]
    assert messages[-1].type == "status" and messages[-1].payload["restarted"] is True


# --------------------------------------------------------------------------- on air


def test_on_air_transitions() -> None:
    state = OnAirState()
    take = apply_command(state, OnAirCommand(action="take", item_id="lt-1"), 1000)
    assert (take.mode, take.item_id, take.hold, take.revision) == ("manual", "lt-1", False, 1)
    held = apply_command(take, OnAirCommand(action="hold"), 2000)
    assert held.hold and held.item_id == "lt-1" and held.revision == 2
    released = apply_command(held, OnAirCommand(action="release"), 2500)
    assert not released.hold
    cleared = apply_command(released, OnAirCommand(action="clear"), 3000)
    assert (cleared.mode, cleared.item_id, cleared.last_action) == ("manual", None, "clear")
    auto = apply_command(cleared, OnAirCommand(action="auto"), 4000)
    assert (auto.mode, auto.item_id, auto.hold, auto.revision) == ("auto", None, False, 5)
    skip = apply_command(apply_command(auto, OnAirCommand(action="hold"), 0), OnAirCommand(
        action="clear"), 0)  # fmt: skip
    assert skip.mode == "auto" and not skip.hold and skip.last_action == "clear"
    assert skip.at_match_ms == 0 and auto.at_match_ms == 4000
    with pytest.raises(OnAirError):
        apply_command(state, OnAirCommand(action="take"), 0)


def test_on_air_commands_are_broadcast_and_in_the_hello() -> None:
    async def scenario() -> tuple[MatchSession, list[WsMessage], str]:
        live = studio_session()
        client = live.hub.subscribe(maxsize=1_000_000)
        live.start()
        director = live.director
        assert director is not None
        await until(lambda: len(director.lower_thirds) > 0)
        with pytest.raises(LookupError):
            live.command_on_air(OnAirCommand(action="take", item_id="nope"))
        item = next(iter(director.lower_thirds))
        live.command_on_air(OnAirCommand(action="take", item_id=item))
        live.command_on_air(OnAirCommand(action="hold"))
        hello = live.hub.state_message("hello", client)
        await live.stop()
        return live, drain(client), str(hello.payload["on_air"]["item_id"])

    live, messages, hello_item = run(scenario())
    on_air = [m.payload for m in messages if m.type == "on_air"]
    assert [p["revision"] for p in on_air] == [1, 2]
    assert on_air[-1]["mode"] == "manual" and on_air[-1]["hold"] is True
    assert hello_item == on_air[-1]["item_id"] == live.on_air.item_id


# --------------------------------------------------------------------------- panels


def full_log(scenario: str, seed: int) -> EventLog:
    match = generated(scenario, seed)
    log = EventLog(match.meta)
    for event in match.events:
        log.append(event)
    return log


def test_timeline_is_the_momentum_series_with_markers() -> None:
    from pundit.agents.moments import detect_all

    match = generated(*MATCH)
    view = full_log(*MATCH).view()
    moments = detect_all(match.meta, match.events)
    built = timeline(view, moments)
    home = match.meta.team_ids[0]
    assert [(p.minute, p.value) for p in built.points] == [
        (m, round(v, 4)) for m, v in momentum_series(view, home)
    ]
    assert all(p.display == momentum_display(p.value) for p in built.points)
    assert momentum_display(-0.0001) == "+0.00" and momentum_display(0.4249) == "+0.42"
    goals = [m for m in built.markers if m.kind == "goal"]
    assert [g.display_minute for g in goals] == ["27'", "63'", "78'"]
    assert {m.kind for m in built.markers} <= MARKER_KINDS
    assert built.half_time_minute is not None and built.as_of_seq == view.last_seq
    early = timeline(full_log(*MATCH).view(300), moments)
    assert early.half_time_minute is None and all(m.minute < 10 for m in early.markers)


def test_stats_panel_matches_the_stats_tools() -> None:
    view = full_log(*MATCH).view()
    home, away = view.meta.team_ids
    for period, window in PERIOD_WINDOWS.items():
        panel = stats_panel(view, period)
        assert [row.metric for row in panel.rows] == list(STATS_PANEL)
        for row in panel.rows:
            for team, cell in ((home, row.home), (away, row.away)):
                result = compute(view, row.metric, MetricParams(team=team, window=window))
                expected = display(result) if result.value is not None else "—"
                assert cell.display == expected, (period, row.metric, team)
    h2 = stats_panel(view, "H2")
    possession = next(r for r in h2.rows if r.metric == "possession")
    assert (possession.home.display, possession.away.display) == ("61%", "39%")  # eval e01


@settings(max_examples=40, deadline=None)
@given(applied=st.integers(min_value=1, max_value=2500), picks=st.lists(st.integers(0, 2499),
       min_size=1, max_size=30))  # fmt: skip
def test_events_never_leak_the_future(applied: int, picks: list[int]) -> None:
    log = full_log(*MATCH)
    events = [log.view()[i] for i in range(min(len(log.view()), 2500))]
    view = log.view(min(applied, len(events)))
    ids = [events[i % len(events)].id for i in picks]
    page = events_by_id(view, ids)
    assert all(e.seq <= view.last_seq for e in page.events)
    returned = {e.id for e in page.events}
    assert returned | set(page.missing) == set(ids) and not returned & set(page.missing)
    assert all(e.type != "player_physical" for e in page.events)


def test_last_events_skip_the_silent_ones() -> None:
    view = full_log(*MATCH).view(1500)
    page = last_events(view, 100)
    assert len(page.events) == 100 and all(e.type != "player_physical" for e in page.events)
    seqs = [e.seq for e in page.events]
    assert seqs == sorted(seqs) and seqs[-1] <= view.last_seq


def test_panels_are_cached_per_seq() -> None:
    async def scenario() -> tuple[object, object, object]:
        live = studio_session()
        live.start()
        await until(lambda: len(live.state) >= 600)
        await live.pause()
        first, again = live.timeline(), live.timeline()
        live.resume()
        await until(lambda: len(live.state) >= 900)
        later = live.timeline()
        await live.stop()
        return first, again, later

    first, again, later = run(scenario())
    assert first is again and later is not first
