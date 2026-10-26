"""MatchSession and SessionManager (design section 11.1): the shared state of the agents.

One replay task per session is the only writer: it applies each event to the MatchState, feeds
the MomentDetector and broadcasts through the Hub, in that order (an event is never broadcast
before it is applied). Claims read immutable prefix views (`view_at`), so a replay running at
20x never moves the numbers of a claim being verified.

Day 3 (docs/day3-design.md sections 11 and 14.4): the replay also detects cues and, after it has
broadcast an event and its moments, offers their triggers to the session's StudioDirector. The
studio items are published per viewer profile: one message number for everybody, the payload
rendered once per profile.

Day 4 (docs/day4-design.md section 3): pause / resume / speed stop only the replay task, never
the studio jobs; the on-air decisions of the editors; read-only panels (timeline, stats, events)
on the current view; a restart tells the clients of the old session to reconnect once the new
session is registered.

Day 5 (docs/day5-design.md section 9.1): a viewer who turned the voice on gets a SpeechCue with
each live commentary line and recap; the Narrator starts making the clip at once. `hello`, resync
and REST never make a clip (nobody listens to the past).
"""

import asyncio
import builtins
import contextlib
import logging
import math
import time
from collections import OrderedDict
from collections.abc import Callable
from functools import cached_property
from typing import Any, Literal

from pundit.agents.context import build_match_context
from pundit.agents.contracts import (
    AgentBreaker,
    ChaosCommand,
    ChaosStatus,
    ClaimRequest,
    ClaimResult,
    CommentaryItem,
    CommentaryView,
    FactView,
    LowerThird,
    MatchContext,
    Moment,
    Profile,
    Recap,
    RecapView,
    RuleId,
    TalkingPoint,
    TalkingPointStatus,
)
from pundit.agents.narration import commentary_script, recap_moment_lines
from pundit.agents.personalize import (
    metric_facts,
    render_commentary,
    render_lower_third,
    render_recap,
    tracker_facts,
)
from pundit.agents.spoken import SpokenLexicon
from pundit.agents.triggers import TriggerFeed
from pundit.api.agent_ops import AgentOps
from pundit.api.chaos import SessionChaos
from pundit.api.hub import DEFAULT_QUEUE_SIZE, Client, Hub
from pundit.api.onair import OnAirCommand, OnAirState, apply_command
from pundit.api.panels import (
    EventsPage,
    PanelCache,
    Period,
    StatsPanel,
    TimelineView,
    events_by_id,
    last_events,
    stats_panel,
    timeline,
)
from pundit.api.studio import StudioCrew, StudioDirector, StudioItem, StudioKind
from pundit.domain.errors import PunditError
from pundit.domain.events import EVENT_ADAPTER, Event
from pundit.domain.ids import new_token, session_id
from pundit.domain.log import LogView
from pundit.domain.matchtime import format_minute
from pundit.domain.meta import MatchMeta
from pundit.engine.generator import generate
from pundit.engine.match import GeneratedMatch
from pundit.engine.replay import Clock, RealClock, replay
from pundit.orchestration.progressive import ModelPassQueue
from pundit.orchestration.service import ClaimService, Listen
from pundit.orchestration.studio_service import StudioService
from pundit.speech.narrator import Narrator
from pundit.state.match_state import MatchSnapshot, MatchState

log = logging.getLogger("pundit.sessions")

SessionStatus = Literal["created", "running", "paused", "finished", "stopped", "error"]
MAX_CLAIMS_PER_SESSION = 200
HELLO_MOMENTS = 50
HELLO_CLAIMS = 20
SNAPSHOT_EVERY_MS = 30_000  # of match time, so the throttle does not depend on the speed
SNAPSHOT_AFTER = frozenset({"goal", "card", "period_end", "var_check", "substitution"})
SILENT_EVENTS = frozenset({"player_physical"})
HELLO_TALKING_POINTS = 20
HELLO_LOWER_THIRDS = 5
HELLO_COMMENTARY = 30
# Day 6 (docs/day6-design.md section 6.4)
PRIVATE_WINDOW_S = 600.0  # new private sessions are counted per address over ten minutes
MAX_TRACKED_ADDRESSES = 4096
JANITOR_EVERY_S = 30.0
FULL_RETRY_S = 60


class MatchSession:
    def __init__(
        self,
        match: GeneratedMatch,
        *,
        service: ClaimService,
        speed: float = 20.0,
        from_minute: float = 0.0,
        clock: Clock | None = None,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        studio: StudioCrew | None = None,
        narrator: Narrator | None = None,
        key: str | None = None,
        private: bool = False,
        now: Callable[[], float] = time.monotonic,
        model_passes: ModelPassQueue | None = None,
    ) -> None:
        self.match = match
        self.model_passes = model_passes  # Day 7: progressive claims (CLAIM_PROGRESSIVE)
        # what clients call the session: the match id, or the match id and a token (Day 6)
        self.key = key or match.meta.match_id
        self.private = private
        self.service = service
        self.narrator = narrator if studio is not None else None  # the voice reads the studio
        self.speed = speed
        self.from_minute = from_minute
        self.clock = clock or RealClock()
        self.queue_size = queue_size
        self.state = MatchState(match.meta)
        self.feed = TriggerFeed(match.meta)
        self.detector = self.feed.moments
        self.moments: list[Moment] = []
        self.claims: OrderedDict[str, ClaimResult] = OrderedDict()
        self.director = StudioDirector(studio, self) if studio is not None else None
        # names and squads for profiles and personalization (the same at every seq)
        self.context: MatchContext = build_match_context(
            LogView.from_events(match.meta, match.events[:1])
        )
        self.hub = Hub(
            self.key,
            self.state_payload,
            state_for=self.client_state if studio is not None else None,
            clock=now,
        )
        self.status: SessionStatus = "created"
        self.error: str | None = None
        self.on_air = OnAirState()
        self.panels = PanelCache()
        self._task: asyncio.Task[None] | None = None
        self._swapping = False  # the replay task is being replaced (new speed): not a stop
        self._claim_count = 0
        self._last_snapshot_bucket = -1
        # Day 7: the agents of this session, live (docs/day7-design.md section 2.5)
        self.chaos: SessionChaos | None = None  # Day 7: a private session's chaos
        breakers = {"claim": lambda: self.claim_service.breaker.state}
        if studio is not None:
            breakers["studio"] = lambda: self.studio_breaker_state()
        self.agent_ops = AgentOps(
            self.key, display_of=self.display_at, breakers=breakers, chaos=self.chaos_status
        )

    # ------------------------------------------------------------------ MatchSource

    @property
    def meta(self) -> MatchMeta:
        return self.match.meta

    @property
    def match_id(self) -> str:
        return self.match.meta.match_id

    def latest_seq(self) -> int:
        return len(self.state) - 1

    @property
    def watched(self) -> bool:
        """At least one viewer is connected: such a session is never stopped to make room."""
        return self.hub.clients > 0

    def display_at(self, seq: int) -> str:
        """The scoreboard minute of the event `seq` ("63'")."""
        events = self.match.events
        if not events:
            return "0'"
        event = events[max(0, min(seq, len(events) - 1))]
        return format_minute(event.period, event.clock_ms)

    def view_at(self, seq: int) -> LogView:
        """Immutable prefix: the first `seq + 1` applied events."""
        return self.state.view(seq + 1)

    # ------------------------------------------------------------------ state for clients

    def snapshot(self) -> MatchSnapshot:
        return self.state.snapshot()

    def info(self) -> dict[str, Any]:
        meta = self.meta
        return {
            "match_id": self.key,
            **({"private": True} if self.private else {}),  # absent: the Day 5 shape
            "scenario": meta.scenario,
            "seed": meta.seed,
            "status": self.status,
            "error": self.error,
            "speed": self.speed,
            "from_minute": self.from_minute,
            "events_total": len(self.match.events),
            "events_applied": len(self.state),
            "home": {
                "id": meta.home.team.id,
                "name": meta.home.team.name,
                "short_name": meta.home.team.short_name,
            },
            "away": {
                "id": meta.away.team.id,
                "name": meta.away.team.name,
                "short_name": meta.away.team.short_name,
            },
            "moments": len(self.moments),
            "claims": len(self.claims),
            "synthetic": True,
            "disclaimer": meta.disclaimer,
            **({"studio": self.director.stats()} if self.director is not None else {}),
        }

    def state_payload(self) -> dict[str, Any]:
        """Payload of `hello` and `resync`: enough for a late client to catch up."""
        return {
            "match": self.info(),
            "snapshot": self.snapshot().model_dump(mode="json"),
            "moments": [m.model_dump(mode="json") for m in self.moments[-HELLO_MOMENTS:]],
            "claims": [
                public_claim(c).model_dump(mode="json")
                for c in list(self.claims.values())[-HELLO_CLAIMS:]
            ],
            "degraded": self.service.degraded_now(),
        }

    def client_state(self, client: Client) -> dict[str, Any]:
        """`hello` / `resync` for one viewer: the Day 2 state plus the latest studio items in
        the viewer's language and register. Rules explained here count as seen."""
        payload = self.state_payload()
        director = self.director
        if director is None:
            return payload
        profile = client.profile
        commentary = []
        for item in list(director.commentary.values())[-HELLO_COMMENTARY:]:
            view, shown = self.render_commentary(item, profile, client.seen_rules)
            if shown is not None:
                client.seen_rules.add(shown)
            commentary.append(view)
        payload.update(
            {
                "on_air": self.on_air.model_dump(mode="json"),
                "profile": profile.model_dump(mode="json"),
                "talking_points": [
                    public_talking_point(tp).model_dump(mode="json")
                    for tp in list(director.talking_points.values())[-HELLO_TALKING_POINTS:]
                ],
                "lower_thirds": [
                    render_lower_third(lt, profile).model_dump(mode="json")
                    for lt in list(director.lower_thirds.values())[-HELLO_LOWER_THIRDS:]
                ],
                "commentary": commentary,
                "recaps": [
                    render_recap(director.recaps[scope], profile).model_dump(mode="json")
                    for scope in ("H1", "FULL")
                    if scope in director.recaps
                ],
            }
        )
        return payload

    # ------------------------------------------------------------------ studio (Day 3)

    @cached_property
    def lexicon(self) -> SpokenLexicon:
        """How the names of this match are read aloud (the same at every seq)."""
        return SpokenLexicon.from_match(self.context)

    def _job_end_ms(self, item_id: str) -> float:
        """Where the studio job of an item ended on its trace (the Narrator's spans follow)."""
        if self.director is None:
            return 0.0
        spans = self.director.trace_of(item_id)
        return max((s.start_ms + s.duration_ms for s in spans), default=0.0)

    def _commentary_speech(
        self, view: CommentaryView, item: CommentaryItem, profile: Profile
    ) -> CommentaryView:
        """A live line for a viewer who listens: the cue (and the clip, made in background)."""
        narrator, voice = self.narrator, profile.voice
        if narrator is None or voice is None or not view.speak:
            return view
        script = commentary_script(view)
        if script is None:
            return view
        cue = narrator.cue(
            script, self.lexicon, gender=voice, item_id=item.id, priority=item.excitement,
            session_speed=self.speed, match_id=self.match_id,
            trace_offset_ms=self._job_end_ms(item.id),
        )  # fmt: skip
        if cue is None:
            return view
        self.agent_ops.cued(item.id, as_of_seq=item.as_of_seq, voice=cue.voice)
        return view.model_copy(update={"speech": cue})

    def recap_view(self, recap: Recap, profile: Profile, *, live: bool) -> RecapView:
        """A recap for one viewer; read aloud (with its key moments) when `live` and the viewer
        listens: the recap REST endpoint and live messages, never hello."""
        view = render_recap(recap, profile)
        narrator, director = self.narrator, self.director
        if not live or narrator is None or director is None or profile.voice is None:
            return view
        lines = recap_moment_lines(
            recap, self.moments, director.commentary, profile.language, profile.mode
        )
        cue = narrator.recap_cue(
            view, lines, recap.as_of_seq, self.lexicon, gender=profile.voice,
            match_id=self.match_id,
        )  # fmt: skip
        return view if cue is None else view.model_copy(update={"speech": cue})

    def render_commentary(
        self, item: CommentaryItem, profile: Profile, seen: set[RuleId], *, live: bool = False
    ) -> tuple[dict[str, Any], RuleId | None]:
        """One commentary item for one viewer (JSON), and the rule it explained in full. A live
        message of a viewer who listens also carries its speech cue."""

        def tracker() -> tuple[FactView, ...] | None:
            if profile.player is None:
                return None
            return tracker_facts(self.view_at(item.as_of_seq), profile.player, profile.language)

        def metric() -> tuple[FactView, ...] | None:
            if profile.metric is None:
                return None
            return metric_facts(self.view_at(item.as_of_seq), profile.metric, profile.language)

        view, shown = render_commentary(
            item, profile, self.context, seen=seen, tracker=tracker, metric_facts=metric
        )
        if live:
            view = self._commentary_speech(view, item, profile)
        return view.model_dump(mode="json"), shown

    def publish_studio(self, kind: StudioKind, item: StudioItem) -> None:
        """Broadcast a studio item, rendered once per profile (the director calls this)."""
        if self.chaos is not None:
            self.chaos.check_published(item.model_dump(mode="json"))
        if isinstance(item, TalkingPoint):
            self.hub.publish("talking_point", public_talking_point(item).model_dump(mode="json"))
        elif isinstance(item, LowerThird):
            lower_third = item
            cache: dict[str, dict[str, Any]] = {}

            def lower_third_for(client: Client) -> dict[str, Any]:
                key = client.profile.language
                if key not in cache:
                    view = render_lower_third(lower_third, client.profile)
                    cache[key] = view.model_dump(mode="json")
                return cache[key]

            self.hub.publish_each("lower_third", lower_third_for)
        elif isinstance(item, Recap):
            recap = item
            recaps: dict[tuple[str, str, str | None], dict[str, Any]] = {}

            def recap_for(client: Client) -> dict[str, Any]:
                profile = client.profile
                key = (profile.language, profile.mode, profile.voice)
                if key not in recaps:
                    view = self.recap_view(recap, profile, live=True)
                    recaps[key] = view.model_dump(mode="json")
                return recaps[key]

            self.hub.publish_each("recap", recap_for)
        else:
            self._publish_commentary(item)
        del kind

    def _publish_commentary(self, item: CommentaryItem) -> None:
        rendered: dict[tuple[str, frozenset[str]], tuple[dict[str, Any], RuleId | None]] = {}

        def commentary_for(client: Client) -> dict[str, Any]:
            profile = client.profile
            seen = (
                frozenset(rule for rule in item.rules if rule in client.seen_rules)
                if profile.beginner
                else frozenset()
            )
            key = (profile.key(), seen)
            if key not in rendered:
                rendered[key] = self.render_commentary(item, profile, client.seen_rules, live=True)
            payload, shown = rendered[key]
            if shown is not None:
                client.seen_rules.add(shown)
            return payload

        self.hub.publish_each("commentary", commentary_for)

    def set_talking_point_status(
        self, item_id: str, status: TalkingPointStatus
    ) -> TalkingPoint | None:
        if self.director is None:
            return None
        updated = self.director.set_status(item_id, status)
        if updated is not None:
            self.hub.publish("talking_point_status", {"id": updated.id, "status": updated.status})
        return updated

    def command_on_air(self, command: OnAirCommand) -> OnAirState:
        """Apply an editor decision and broadcast it. LookupError: the taken lower-third does
        not exist (or the studio is off); OnAirError: a take without an item."""
        if command.action == "take" and (
            self.director is None
            or command.item_id is None
            or command.item_id not in self.director.lower_thirds
        ):
            raise LookupError(command.item_id)
        last = self.state.view().last
        self.on_air = apply_command(self.on_air, command, last.match_ms if last else 0)
        self.hub.publish("on_air", self.on_air.model_dump(mode="json"))
        return self.on_air

    # ------------------------------------------------------------------ panels (Day 4)

    def timeline(self, window_minutes: float = 10.0) -> TimelineView:
        view = self.state.view()
        return self.panels.get(
            ("timeline", window_minutes, view.last_seq),
            lambda: timeline(view, self.moments, window_minutes),
        )

    def stats(self, period: Period) -> StatsPanel:
        view = self.state.view()
        return self.panels.get(("stats", period, view.last_seq), lambda: stats_panel(view, period))

    def events(self, ids: list[str]) -> EventsPage:
        return events_by_id(self.state.view(), ids)

    def last_events(self, count: int) -> EventsPage:
        return last_events(self.state.view(), count)

    # ------------------------------------------------------------------ replay (single writer)

    def _publish_status(self, *, restarted: bool = False) -> None:
        self.hub.publish(
            "status",
            {
                "session": self.status,
                "error": self.error,
                "degraded": self.service.degraded_now(),
                "speed": self.speed,
                "restarted": restarted,
            },
        )

    def _set_status(self, status: SessionStatus) -> None:
        self.status = status
        self._publish_status()

    def _snapshot_due(self, event: Event) -> bool:
        bucket = event.match_ms // SNAPSHOT_EVERY_MS
        due = bucket != self._last_snapshot_bucket or event.type in SNAPSHOT_AFTER
        self._last_snapshot_bucket = bucket
        return due

    def apply(self, event: Event, *, publish: bool) -> list[Moment]:
        """Apply one event, detect moments and cues and (unless catching up) broadcast them,
        then offer the triggers to the studio: an item never comes before its event."""
        self.state.apply(event)
        found, triggers = self.feed.feed(event, self.state.view())
        self.moments.extend(found)
        if publish:
            if event.type not in SILENT_EVENTS:
                self.hub.publish("event", EVENT_ADAPTER.dump_python(event, mode="json"))
            for moment in found:
                self.hub.publish("moment", moment.model_dump(mode="json"))
            if self._snapshot_due(event):
                self.hub.publish("snapshot", self.snapshot().model_dump(mode="json"))
            if self.director is not None:
                for trigger in triggers:
                    self.director.offer(trigger)
        return found

    async def _run(self) -> None:
        start = len(self.state)
        first_run = start == 0
        try:
            caught_up = False
            async for item in replay(
                self.match.events[start:],
                speed=self.speed,
                clock=self.clock,
                from_minute=self.from_minute if first_run else 0.0,
            ):
                if not item.catching_up and not caught_up and first_run and self.from_minute > 0:
                    caught_up = True
                    self.hub.publish("snapshot", self.snapshot().model_dump(mode="json"))
                self.apply(item.event, publish=not item.catching_up)
            self._set_status("finished")
        except asyncio.CancelledError:
            if self.status == "running" and not self._swapping:
                self._set_status("stopped")
            raise
        except Exception as exc:  # a broken replay must not take the server down
            log.exception("replay of %s failed", self.key)
            self.error = f"{type(exc).__name__}: {exc}"[:300]
            self._set_status("error")

    def start(self) -> None:
        if self.status in ("running", "finished") or (
            self._task is not None and not self._task.done()
        ):
            return
        self.error = None
        self._set_status("running")
        self._task = asyncio.create_task(self._run(), name=f"replay:{self.key}")

    async def _cancel_replay(self) -> None:
        """Cancel the replay task at its next sleep: `apply()` is synchronous, so an event is
        never half applied, and the event it was waiting for is replayed by the next task."""
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def pause(self) -> None:
        """Stop the replay where it is. Unlike `stop`, queued studio jobs keep running and are
        still published."""
        if self.status != "running":
            return
        self._set_status("paused")  # first: the task's cancel handler only rewrites "running"
        await self._cancel_replay()

    def resume(self) -> None:
        """Continue (or begin) the replay from the next event."""
        if self.status in ("created", "paused", "stopped"):
            self.start()

    async def set_speed(self, speed: float) -> None:
        """Change the replay speed; a running replay continues at once from the next event."""
        if speed <= 0:
            raise ValueError("speed must be > 0")
        running = self.status == "running" and self._task is not None and not self._task.done()
        if running:
            self._swapping = True
            try:
                await self._cancel_replay()
            finally:
                self._swapping = False
            self.speed = speed
            self._task = asyncio.create_task(self._run(), name=f"replay:{self.key}")
        else:
            self.speed = speed
        self._publish_status()

    def announce_restart(self) -> None:
        """Tell the clients of this (replaced) session to reconnect: the match id now names a
        new session (called once that session is registered)."""
        self._publish_status(restarted=True)

    async def stop(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self.director is not None:
            await self.director.stop()
        await self.agent_ops.aclose()
        # also when the task was cancelled before it ever ran, or the replay was paused
        if self.status in ("running", "paused"):
            self._set_status("stopped")

    async def wait(self) -> None:
        """Until the replay task ends (tests and CLI)."""
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    # ------------------------------------------------------------------ claims

    def next_claim_id(self) -> str:
        self._claim_count += 1
        return f"{self.key}-c{self._claim_count:04d}"  # unique across private sessions

    def add_claim(self, result: ClaimResult) -> None:
        """Store and broadcast a claim; a progressive claim comes again as it moves on (queued,
        running, rev.2), always under the same id. An older revision never replaces a newer."""
        known = self.claims.get(result.claim_id)
        if known is not None and known.revision > result.revision:
            return
        self.claims[result.claim_id] = result
        while len(self.claims) > MAX_CLAIMS_PER_SESSION:
            self.claims.popitem(last=False)
        payload = public_claim(result).model_dump(mode="json")
        if self.chaos is not None:
            self.chaos.check_published(payload)
        self.hub.publish("claim", payload)

    # ------------------------------------------------------------------ chaos (Day 7)

    @property
    def claim_service(self) -> ClaimService:
        """The session's own service once its chaos was switched on, else the app's."""
        return self.chaos.claims if self.chaos is not None else self.service

    @property
    def studio_service_override(self) -> StudioService | None:
        return self.chaos.studio if self.chaos is not None else None

    def studio_breaker_state(self) -> AgentBreaker:
        own = self.studio_service_override
        if own is not None:
            return own.breaker.state
        assert self.director is not None
        return self.director.crew.service.breaker.state

    def chaos_status(self) -> ChaosStatus:
        if not self.private:
            return ChaosStatus(available=False)
        if self.chaos is None:
            return ChaosStatus(available=True)
        return self.chaos.status()

    def switch_chaos(self, command: ChaosCommand, now: float) -> ChaosStatus:
        """Raises ChaosRefused on a shared session or too many switches."""
        if not self.private:
            raise ChaosRefused("only your own demo match can be broken: start one first")
        if self.chaos is None:
            crew = self.director.crew if self.director is not None else None
            self.chaos = SessionChaos(self.service, crew.service if crew else None)
        if not self.chaos.may_switch(now):
            raise ChaosRefused("too many chaos switches in a minute: wait a little")
        self.chaos.switch(command.fault, command.duration_s, now)
        log.info("chaos of %s: %s", self.key, command.fault or "reset")
        return self.chaos.status()

    def _claim_listen(self, run_id: str) -> Listen:
        ops = self.agent_ops
        return lambda seq: ops.listener("claim", run_id, seq)

    async def verify(self, request: ClaimRequest) -> ClaimResult:
        claim_id = self.next_claim_id()
        service = self.claim_service
        if self.model_passes is None:  # one pass (the Day 2 claim)
            result = await service.verify(
                request, self, claim_id=claim_id, listen=self._claim_listen(claim_id)
            )
        else:  # Day 7: rev.1 from the rules now, the model pass on the queue
            rev1 = await service.verify_rules(
                request, self, claim_id=claim_id, listen=self._claim_listen(f"{claim_id}#rules")
            )
            result = self.model_passes.submit(
                rev1,
                request,
                self,
                self.add_claim,
                self._claim_listen(f"{claim_id}#model"),
                service=service,
            )
        self.add_claim(result)
        return result


def public_claim(result: ClaimResult) -> ClaimResult:
    """The claim as broadcast: without the trace."""
    return result.model_copy(update={"trace": None})


def public_talking_point(item: TalkingPoint) -> TalkingPoint:
    """The talking point as broadcast: without the trace."""
    return item.model_copy(update={"trace": None})


class ChaosRefused(PunditError):
    """Chaos on a shared session, or switched too often (HTTP 403 / 429)."""


class SessionsFull(PunditError):
    """Every session is being watched: no room for another one (HTTP 503 demo_full)."""

    retry_after_s = FULL_RETRY_S


class TooManySessions(PunditError):
    """This address made its new private sessions of the last ten minutes (HTTP 429)."""

    def __init__(self, retry_after_s: int) -> None:
        super().__init__(f"too many new private sessions: retry in {retry_after_s} s")
        self.retry_after_s = retry_after_s


class SessionManager:
    """Sessions by session id (a match id, or a match id and a private token); generated
    matches are cached (generation takes ~0.3 s). Day 6 (docs/day6-design.md section 6.4): a
    session somebody watches is never stopped; at the cap a session nobody watches makes room
    (a finished one first, then the one left alone longest), else 503; the janitor removes the
    sessions nobody has watched for a while."""

    def __init__(
        self,
        service: ClaimService,
        *,
        clock_factory: Callable[[], Clock] = RealClock,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        max_sessions: int = 16,
        studio: StudioCrew | None = None,
        narrator: Narrator | None = None,
        private_idle_s: float = 900.0,
        shared_idle_s: float = 3600.0,
        private_per_ip: int = 3,
        now: Callable[[], float] = time.monotonic,
        model_passes: ModelPassQueue | None = None,
    ) -> None:
        self.service = service
        self.model_passes = model_passes  # one queue for every session: one model on the CPU
        self.studio = studio
        self.narrator = narrator  # one for the app: clips are shared by every session
        self.clock_factory = clock_factory
        self.queue_size = queue_size
        self.max_sessions = max_sessions
        self.private_idle_s = private_idle_s
        self.shared_idle_s = shared_idle_s
        self.private_per_ip = private_per_ip
        self.sessions: dict[str, MatchSession] = {}
        self._matches: dict[tuple[str, int], GeneratedMatch] = {}
        self._lock = asyncio.Lock()
        self._now = now
        self._private_by_ip: OrderedDict[str, list[float]] = OrderedDict()

    async def _generate(self, scenario: str, seed: int) -> GeneratedMatch:
        key = (scenario, seed)
        if key not in self._matches:
            self._matches[key] = await asyncio.to_thread(generate, scenario, seed)
        return self._matches[key]

    def _has_room(self) -> bool:
        return len(self.sessions) < self.max_sessions or any(
            not session.watched for session in self.sessions.values()
        )

    async def _make_room(self) -> None:
        """At the cap, stop a session nobody watches: a finished one first, then the one left
        alone longest. Never a watched one: with none to stop, the demo is full."""
        while len(self.sessions) >= self.max_sessions:
            idle = [(key, s) for key, s in self.sessions.items() if not s.watched]
            if not idle:
                raise SessionsFull("every session of the demo is being watched")
            key, victim = min(
                idle, key=lambda item: (item[1].status != "finished", item[1].hub.last_change)
            )
            await victim.stop()
            del self.sessions[key]
            log.info("session %s stopped to make room", key)

    def _take_private_slot(self, client: str) -> None:
        """Count a new private session of `client` (its address); refuse past the limit."""
        now = self._now()
        recent = [t for t in self._private_by_ip.get(client, ()) if now - t < PRIVATE_WINDOW_S]
        if len(recent) >= self.private_per_ip:
            self._private_by_ip[client] = recent
            raise TooManySessions(max(1, math.ceil(PRIVATE_WINDOW_S - (now - recent[0]))))
        recent.append(now)
        self._private_by_ip[client] = recent
        self._private_by_ip.move_to_end(client)
        while len(self._private_by_ip) > MAX_TRACKED_ADDRESSES:
            self._private_by_ip.popitem(last=False)

    def _fresh_token(self, match_id: str) -> str:
        while True:
            token = new_token()
            if session_id(match_id, token) not in self.sessions:
                return token

    async def create(
        self,
        scenario: str,
        seed: int,
        *,
        speed: float = 20.0,
        from_minute: float = 0.0,
        autostart: bool = True,
        restart: bool = False,
        private: bool = False,
        token: str | None = None,
        client: str = "local",
    ) -> MatchSession:
        """Idempotent: the session of a running match is returned unless `restart=True`.
        `private` makes a session of its own (a new token); a `token` names one, which is
        restarted with the same id, or made again with it after the janitor removed it."""
        async with self._lock:
            match_id = f"{scenario}-{seed}"
            if private and token is None:
                token = self._fresh_token(match_id)
            key = session_id(match_id, token)
            existing = self.sessions.get(key)
            if existing is not None and not restart:
                return existing
            if existing is not None:
                await existing.stop()
                del self.sessions[key]
            else:
                if not self._has_room():
                    raise SessionsFull("every session of the demo is being watched")
                if token is not None:  # every new private session counts, a made-up token too
                    self._take_private_slot(client)
                await self._make_room()
            match = await self._generate(scenario, seed)
            session = MatchSession(
                match,
                service=self.service,
                speed=speed,
                from_minute=from_minute,
                clock=self.clock_factory(),
                queue_size=self.queue_size,
                studio=self.studio,
                narrator=self.narrator,
                key=key,
                private=token is not None,
                now=self._now,
                model_passes=self.model_passes,
            )
            self.sessions[key] = session
        if existing is not None:
            # only now: a client that reconnects at once lands on the new session
            existing.announce_restart()
        if autostart:
            session.start()
        return session

    def get(self, match_id: str) -> MatchSession | None:
        return self.sessions.get(match_id)

    def list(self) -> list[MatchSession]:
        """The shared sessions: a private one is only reached by its link (section 6.3)."""
        return [session for session in self.sessions.values() if not session.private]

    async def sweep(self) -> builtins.list[str]:  # (`list` is a method here)
        """Stop and remove the sessions nobody has watched for a while (15 minutes for a
        private one, an hour for a shared one): a short disconnection loses nothing."""
        removed: builtins.list[str] = []
        async with self._lock:
            now = self._now()
            for key, session in list(self.sessions.items()):
                if session.watched:
                    continue
                limit = self.private_idle_s if session.private else self.shared_idle_s
                if now - session.hub.last_change >= limit:
                    await session.stop()
                    del self.sessions[key]
                    removed.append(key)
        if removed:
            log.info("sessions nobody watched were removed: %s", ", ".join(removed))
        return removed

    async def run_janitor(self, every_s: float = JANITOR_EVERY_S) -> None:
        """The janitor of the app's lifespan (cancelled at shutdown)."""
        while True:
            await asyncio.sleep(every_s)
            try:
                await self.sweep()
            except Exception:  # the janitor must never die: the next sweep tries again
                log.exception("session janitor failed")

    def find_claim(self, claim_id: str) -> ClaimResult | None:
        for session in self.sessions.values():
            if claim_id in session.claims:
                return session.claims[claim_id]
        return None

    async def shutdown(self) -> None:
        """Cancel every replay task and wait for it (no task left behind)."""
        for session in list(self.sessions.values()):
            await session.stop()
        if self.model_passes is not None:
            await self.model_passes.aclose()
