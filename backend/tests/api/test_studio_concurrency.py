"""Concurrency with the studio (docs/day3-design.md section 16.5): a slow model never delays
the items, many clients of several profiles share one numbering, and claims verified while the
studio works give the very same results as without it."""

import asyncio
import time
from typing import Any

from helpers import generated, mock_service, run, source
from studio_helpers import FAST, first_trigger, run_job

from pundit.agents.contracts import ClaimRequest, ClaimResult, Profile
from pundit.api.app import default_crew
from pundit.api.hub import Client, Hub
from pundit.api.sessions import MatchSession
from pundit.engine.replay import SteppingClock
from pundit.llm.settings import Settings
from pundit.orchestration.studio_service import StudioService

DL = ("dominant_loser", 42)


def test_a_slow_model_never_delays_the_items() -> None:
    """The mock answers in 5 s with budgets of 0.5 s: templates on time, then the studio
    breaker opens and the model is not called any more."""
    slow = StudioService(Settings(mock_llm_latency_ms=5000), budgets=FAST)
    goal = first_trigger(*DL, "goal")
    timings, outputs = [], []
    for _ in range(3):
        started = time.perf_counter()
        outputs.append(run_job(slow, *DL, goal))
        timings.append(time.perf_counter() - started)
    assert all(t <= FAST.job_s + 0.2 for t in timings), timings
    first, last = outputs[0], outputs[-1]
    assert {t.source for t in first.commentary.texts} == {"template"}
    assert "writer_fallback" in first.degraded and slow.breaker.state == "open"
    assert {"llm_skipped", "circuit_open"} <= set(last.degraded)
    assert last.talking_point is not None and last.talking_point.source_vi == "template"


def test_three_profiles_five_clients_one_numbering() -> None:
    async def scenario() -> None:
        hub = Hub("m-1", lambda: {"state": "now"}, state_for=lambda c: {"key": c.profile.key()})
        profiles = [Profile(), Profile(mode="casual"), Profile(language="en", beginner=True)]
        clients = [hub.subscribe(100, profile) for profile in profiles for _ in range(5)]
        slow = hub.subscribe(2, Profile(mode="casual"))  # never reads
        renders = 0
        for _ in range(10):
            cache: dict[str, dict[str, Any]] = {}

            def payload_for(client: Client, cache: dict[str, dict[str, Any]] = cache) -> Any:
                nonlocal renders
                key = client.profile.key()
                if key not in cache:
                    renders += 1
                    cache[key] = {"for": key}
                return cache[key]

            hub.publish_each("commentary", payload_for)
        assert renders == 30  # once per profile and message, not once per client
        for client in clients:
            messages = [client.queue.get_nowait() for _ in range(client.queue.qsize())]
            assert [m.n for m in messages] == list(range(1, 11))
            assert all(m.payload == {"for": client.profile.key()} for m in messages)
        queued = [slow.queue.get_nowait() for _ in range(slow.queue.qsize())]
        assert slow.resyncs >= 1 and queued[0].type == "resync"
        assert queued[0].payload == {"key": Profile(mode="casual").key()}  # per viewer

    run(scenario())


def test_claims_during_the_studio_equal_claims_without_it() -> None:
    """C5 of Day 2 with the studio on: 20 claims while a full replay feeds the studio."""
    settings = Settings(studio_queue_size=4096, studio_drop_at=4096)
    claims = mock_service()
    session = MatchSession(
        generated(*DL),
        service=claims,
        speed=1000.0,
        clock=SteppingClock(),
        studio=default_crew(settings),
    )
    texts = (
        "Đội nhà áp đảo hiệp 2",
        "Kestrel won despite lower xG",
        "HAR có nhiều cú sút hơn KES",
        "KES chưa có thẻ đỏ nào",
    )
    seqs = (400, 800, 1200, 1600, 2000)

    async def scenario() -> list[ClaimResult]:
        session.start()
        while len(session.state) < max(seqs) + 1:
            await asyncio.sleep(0)
        results = await asyncio.gather(
            *(
                claims.verify(
                    ClaimRequest(match_id=session.match_id, text=text, as_of_seq=seq),
                    session,
                    claim_id=f"c-{seq}-{index}",
                )
                for seq in seqs
                for index, text in enumerate(texts)
            )
        )
        await session.wait()
        assert session.director is not None and session.director.processed > 0
        await session.director.idle()
        await session.director.stop()
        return list(results)

    with_studio = run(scenario())
    assert len(with_studio) == 20
    alone = mock_service()
    for result in with_studio:
        request = ClaimRequest(
            match_id=result.match_id, text=result.text, as_of_seq=result.as_of_seq
        )
        expected = run(alone.verify(request, source(*DL), claim_id=result.claim_id))
        assert result.verdict == expected.verdict and result.headline == expected.headline
        assert result.explanation == expected.explanation
