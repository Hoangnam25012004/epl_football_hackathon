"""MCP on the API's live sessions (docs/day6-design.md sections 3.2, 10.2 and 13): a session is
read as far as it has been played, pinned for the whole call, never published to."""

import asyncio

import pytest
from helpers import generated, mock_service, run
from mcp import Client

from pundit.api.mcp_mount import LiveSessions, live_entry
from pundit.api.sessions import MatchSession, SessionManager
from pundit.domain.matchtime import format_minute
from pundit.engine.replay import SteppingClock
from pundit.mcp_server import tools
from pundit.mcp_server.errors import ToolInputError
from pundit.mcp_server.server import build_server
from pundit.stats import MetricParams, compare, compute
from pundit.stats.result import display

DL = "dominant_loser-42"
PLAYED = 1500  # events applied: early in the second half


def manager() -> SessionManager:
    return SessionManager(mock_service(), clock_factory=SteppingClock)


async def partly_played(sessions: SessionManager, played: int = PLAYED) -> MatchSession:
    """A session that has applied `played` events and is not replaying (deterministic)."""
    session = await sessions.create("dominant_loser", 42, autostart=False)
    for event in generated("dominant_loser", 42).events[:played]:
        session.apply(event, publish=False)
    return session


async def finished(
    sessions: SessionManager, scenario: str = "comeback_var", seed: int = 7
) -> MatchSession:
    session = await sessions.create(scenario, seed, speed=1000.0)
    await session.wait()
    assert session.status == "finished"
    return session


def test_a_live_session_is_read_as_far_as_it_has_been_played() -> None:
    async def scenario() -> None:
        sessions = manager()
        session = await partly_played(sessions)
        source = LiveSessions(sessions)
        out = await tools.get_stat(source, match_id=DL, metric="possession", team="HAR")
        view = session.view_at(PLAYED - 1)
        expected = compute(view, "possession", MetricParams(team="harbor_lions"))
        assert (out.source, out.as_of.seq) == ("live", PLAYED - 1)
        assert out.stat.display == display(expected)
        last = generated("dominant_loser", 42).events[PLAYED - 1]
        assert out.as_of.minute == format_minute(last.period, last.clock_ms)
        pair = await tools.compare_stat(source, match_id=DL, metric="shots")
        both = compare(view, "shots")
        assert (pair.home.display, pair.away.display) == (display(both.home), display(both.away))

    run(scenario())


def test_a_live_session_never_answers_about_the_future() -> None:
    async def scenario() -> None:
        sessions = manager()
        await partly_played(sessions)
        source = LiveSessions(sessions)
        last = generated("dominant_loser", 42).events[PLAYED - 1]
        now = format_minute(last.period, last.clock_ms)
        ahead = format_minute(last.period, last.clock_ms + 10 * 60_000)
        with pytest.raises(ToolInputError, match=f"has not been played yet: the match is at {now}"):
            await tools.get_scoreboard(source, match_id=DL, as_of=ahead)
        with pytest.raises(ToolInputError, match="the match is not over"):
            await tools.get_scoreboard(source, match_id=DL, as_of="FT")
        at_ht = await tools.get_scoreboard(source, match_id=DL, as_of="HT")
        assert at_ht.period == 1 and at_ht.source == "live"

    run(scenario())


def test_moments_stop_at_the_minute_asked() -> None:
    async def scenario() -> None:
        sessions = manager()
        session = await partly_played(sessions)
        out = await tools.get_moments(
            LiveSessions(sessions), match_id=DL, min_importance=1, limit=50
        )
        assert [m.id for m in out.moments] == [m.id for m in session.moments][-50:]
        ht = await tools.get_moments(
            LiveSessions(sessions), match_id=DL, min_importance=1, as_of="HT", limit=50
        )
        expected = [m.id for m in session.moments if m.seq <= ht.as_of.seq]
        assert [m.id for m in ht.moments] == expected[-50:]
        assert ht.as_of.seq < out.as_of.seq

    run(scenario())


def test_a_finished_session_answers_at_full_time() -> None:
    async def scenario() -> None:
        sessions = manager()
        session = await finished(sessions)
        out = await tools.get_scoreboard(
            LiveSessions(sessions), match_id="comeback_var-7", as_of="FT"
        )
        assert (out.source, out.finished) == ("live", True)
        assert (out.home.goals, out.away.goals) == (3, 2)
        assert out.as_of.seq == session.latest_seq()

    run(scenario())


def test_a_match_without_a_session_is_generated() -> None:
    async def scenario() -> None:
        out = await tools.get_scoreboard(LiveSessions(manager()), match_id="red_card_swing-7")
        assert out.source == "replay" and out.finished

    run(scenario())


def test_a_private_session_that_is_gone_says_so() -> None:
    async def scenario() -> None:
        with pytest.raises(ToolInputError, match="is not running"):
            await tools.get_scoreboard(
                LiveSessions(manager()), match_id="dominant_loser-42~ab12cd34"
            )

    run(scenario())


def test_the_listing_shows_shared_sessions_only() -> None:
    async def scenario() -> None:
        sessions = manager()
        session = await partly_played(sessions)
        sessions.sessions["dominant_loser-42~ab12cd34"] = session  # a private one (section 6)
        listing = LiveSessions(sessions).live()
        assert [entry.match_id for entry in listing] == [DL]
        entry = listing[0]
        snapshot = session.snapshot()
        assert (entry.scenario, entry.seed, entry.status) == ("dominant_loser", 42, "created")
        assert entry.minute == snapshot.clock_display
        home, away = session.meta.team_ids
        assert entry.score == f"{snapshot.score[home]}-{snapshot.score[away]}"
        assert live_entry(DL, session) == entry
        out = await tools.list_matches(LiveSessions(sessions))
        assert [e.match_id for e in out.live] == [DL]

    run(scenario())


def test_verify_claim_on_a_live_session_publishes_nothing() -> None:
    async def scenario() -> None:
        sessions = manager()
        session = await partly_played(sessions)
        before = session.hub.last_n
        out = await tools.verify_claim(
            LiveSessions(sessions), tools.rules_only(), match_id=DL, text="Harbor dominated"
        )
        assert out.source == "live" and out.as_of.seq == PLAYED - 1
        assert session.claims == {} and session.hub.last_n == before
        assert session.info()["claims"] == 0

    run(scenario())


def test_calls_during_a_fast_replay_are_each_consistent_with_their_minute() -> None:
    """20 calls while a session replays as fast as it can: no error, and every answer is the
    registry's value at its own as_of seq (the view was pinned for the whole call)."""

    async def scenario() -> list[str]:
        sessions = manager()
        session = await sessions.create("dominant_loser", 42, speed=1000.0)
        server, stats = build_server(LiveSessions(sessions))
        wrong: list[str] = []
        async with Client(server) as client:

            async def one(n: int) -> None:
                await asyncio.sleep(n * 0.003)
                tool = "compare_stat" if n % 2 else "get_stat"
                arguments = {"match_id": DL, "metric": "shots"}
                if tool == "get_stat":
                    arguments = {**arguments, "metric": "possession", "team": "home"}
                result = await client.call_tool(tool, arguments)
                content = result.structured_content
                if result.is_error or content is None:
                    if "has not started" not in str(result.content):
                        wrong.append(f"{n}: {result.content}")
                    return
                view = session.view_at(content["as_of"]["seq"])
                if tool == "get_stat":
                    want = compute(view, "possession", MetricParams(team="harbor_lions"))
                    if content["stat"]["display"] != display(want):
                        wrong.append(f"{n}: possession")
                else:
                    both = compare(view, "shots")
                    if (content["home"]["display"], content["away"]["display"]) != (
                        display(both.home),
                        display(both.away),
                    ):
                        wrong.append(f"{n}: shots")

            await asyncio.gather(*(one(n) for n in range(20)))
        await session.wait()
        assert stats.errors == 0
        return wrong

    assert run(scenario()) == []
