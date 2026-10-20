"""MatchSession and Hub without HTTP (design sections 11.1 and 11.3)."""

import asyncio
from dataclasses import replace

from helpers import generated, mock_service, run

from pundit.agents.contracts import ClaimRequest
from pundit.agents.moments import detect_all
from pundit.api.hub import Hub
from pundit.api.sessions import MAX_CLAIMS_PER_SESSION, MatchSession, SessionManager
from pundit.engine.replay import SteppingClock
from pundit.stats import MetricParams, compute


def session(scenario: str = "dominant_loser", seed: int = 42, **kwargs: object) -> MatchSession:
    return MatchSession(
        generated(scenario, seed),
        service=mock_service(),
        speed=1000.0,
        clock=SteppingClock(),
        **kwargs,  # type: ignore[arg-type]
    )


def test_replay_applies_every_event_and_detects_the_batch_moments() -> None:
    async def scenario() -> MatchSession:
        live = session()
        live.start()
        await live.wait()
        return live

    live = run(scenario())
    match = generated("dominant_loser", 42)
    assert live.status == "finished" and len(live.state) == len(match.events)
    assert live.moments == detect_all(match.meta, match.events)
    assert live.snapshot().score == {"harbor_lions": 1, "kestrel_athletic": 2}


def test_a_broken_stream_ends_in_error_without_raising() -> None:
    match = generated("balanced", 2)
    broken = replace(match, events=match.events[:40] + match.events[41:])  # a gap in seq

    async def scenario() -> MatchSession:
        live = MatchSession(broken, service=mock_service(), speed=1000.0, clock=SteppingClock())
        live.start()
        await live.wait()
        return live

    live = run(scenario())
    assert live.status == "error" and live.error is not None and "INV-01" in live.error
    assert len(live.state) == 40


def test_stop_and_resume() -> None:
    async def scenario() -> tuple[str, int, MatchSession]:
        live = session("comeback_var", 7)
        live.start()
        while len(live.state) < 300:
            await asyncio.sleep(0)
        await live.stop()
        stopped, applied = live.status, len(live.state)
        live.start()
        await live.wait()
        return stopped, applied, live

    stopped, applied, live = run(scenario())
    assert stopped == "stopped" and 300 <= applied < len(live.match.events)
    assert live.status == "finished" and len(live.state) == len(live.match.events)
    match = generated("comeback_var", 7)
    assert live.moments == detect_all(match.meta, match.events)  # resuming changes nothing


def test_claims_during_the_replay_are_pinned() -> None:
    """Twenty claims while the replay runs: each one matches the oracle at its own as_of."""

    async def scenario() -> tuple[MatchSession, list]:  # type: ignore[type-arg]
        live = session()
        live.start()
        results = []
        for index in range(20):
            while len(live.state) < 100 * (index + 1):
                await asyncio.sleep(0)
            results.append(
                asyncio.create_task(
                    live.verify(
                        ClaimRequest(
                            match_id=live.match_id, text="HAR có nhiều đường chuyền hơn KES"
                        )
                    )
                )
            )
        done = await asyncio.gather(*results)
        await live.wait()
        return live, list(done)

    live, results = run(scenario())
    assert len({r.claim_id for r in results}) == 20
    for result in results:
        view = live.view_at(result.as_of_seq)
        for fact in result.assertions[0].facts:
            if not fact.derived:
                params = MetricParams(team=fact.team, window=result.assertions[0].assertion.window)
                assert compute(view, "passes", params).value == fact.value
    assert [r.as_of_seq for r in results] == sorted(r.as_of_seq for r in results)


def test_claim_log_is_bounded() -> None:
    live = session()
    result = run(
        live.service.verify(
            ClaimRequest(match_id=live.match_id, text="HAR có 1 bàn"), live, claim_id="x"
        )
    )
    for index in range(MAX_CLAIMS_PER_SESSION + 5):
        live.add_claim(result.model_copy(update={"claim_id": f"c{index}"}))
    assert len(live.claims) == MAX_CLAIMS_PER_SESSION
    assert next(iter(live.claims)) == "c5"


def test_hub_numbers_messages_and_resyncs_slow_clients() -> None:
    async def scenario() -> None:
        hub = Hub("m-1", lambda: {"snapshot": "now"})
        fast, slow = hub.subscribe(100), hub.subscribe(3)
        for index in range(10):
            hub.publish("event", {"i": index})  # never blocks, even with a full queue
        fast_ns = [fast.queue.get_nowait().n for _ in range(fast.queue.qsize())]
        assert fast_ns == list(range(1, 11))
        slow_messages = [slow.queue.get_nowait() for _ in range(slow.queue.qsize())]
        assert slow.resyncs >= 1 and slow_messages[0].type == "resync"
        assert slow_messages[0].payload == {"snapshot": "now"}
        assert [m.n for m in slow_messages[1:]] == list(range(slow_messages[0].n + 1, 11))
        hub.unsubscribe(fast)
        assert hub.clients == 1

    run(scenario())


def test_manager_is_idempotent_and_restarts() -> None:
    async def scenario() -> None:
        manager = SessionManager(mock_service(), clock_factory=SteppingClock)
        first = await manager.create("dominant_loser", 42, autostart=False)
        again = await manager.create("dominant_loser", 42, autostart=False)
        assert first is again
        restarted = await manager.create(
            "dominant_loser", 42, autostart=True, speed=500, restart=True
        )
        assert restarted is not first and restarted.speed == 500
        await manager.shutdown()
        assert restarted.status in ("stopped", "finished")
        assert all(s._task is None or s._task.done() for s in manager.sessions.values())

    run(scenario())
