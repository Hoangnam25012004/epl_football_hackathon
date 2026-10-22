"""Studio properties (docs/day3-design.md section 16.3): the offline run equals a live session
(S-2), items carry the numbers of their pinned view (S-4); S-1, S-3, S-5..S-10 are covered by
the workflow, template, personalization and evaluation tests."""

from helpers import generated, mock_service, run
from studio_helpers import offline, view_at

from pundit.api.app import default_crew
from pundit.api.sessions import MatchSession
from pundit.engine.replay import SteppingClock
from pundit.llm.settings import Settings
from pundit.orchestration.studio_eval import pack_of

DL = ("dominant_loser", 42)


def live_session() -> MatchSession:
    """A whole live session at full speed, with a queue that never drops a job."""
    settings = Settings(studio_queue_size=4096, studio_drop_at=4096)
    session = MatchSession(
        generated(*DL),
        service=mock_service(),
        speed=1000.0,
        clock=SteppingClock(),
        studio=default_crew(settings),
    )

    async def play() -> None:
        session.start()
        await session.wait()
        assert session.director is not None
        await session.director.idle()
        await session.director.stop()

    run(play())
    return session


def test_offline_run_equals_the_live_session() -> None:
    """S-2: `pundit studio` and a live session make the very same items."""
    session = live_session()
    director = session.director
    assert director is not None and director.dropped == 0 and director.errors == 0
    batch = offline(*DL)
    assert list(director.commentary.values()) == [o.commentary for o in batch.outputs]
    talking = [o.talking_point for o in batch.outputs if o.talking_point is not None]
    assert list(director.talking_points.values()) == talking
    graphics = [o.lower_third for o in batch.outputs if o.lower_third is not None]
    assert list(director.lower_thirds.values()) == graphics
    for scope, revisions in batch.recaps.items():
        assert director.recaps[scope] == revisions[-1]  # type: ignore[index]


def test_items_carry_the_numbers_of_their_pinned_view() -> None:
    """S-4: the facts of every item are Stats Tools results at its trigger's seq, although the
    replay was far ahead when the job ran."""
    batch = offline(*DL)
    for trigger, output in zip(batch.triggers, batch.outputs, strict=True):
        built = pack_of(trigger, view_at(*DL, trigger.seq))
        facts = {fact.key: fact for fact in built.facts}
        for fact in output.commentary.facts:
            assert facts[fact.key] == fact, (trigger.id, fact.key)
        assert output.commentary.as_of_seq == trigger.seq
