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
import contextlib
import logging
from collections import OrderedDict
from collections.abc import Callable
from functools import cached_property
from typing import Any, Literal

from pundit.agents.context import build_match_context
from pundit.agents.contracts import (
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
from pundit.domain.events import EVENT_ADAPTER, Event
from pundit.domain.log import LogView
from pundit.domain.meta import MatchMeta
from pundit.engine.generator import generate
from pundit.engine.match import GeneratedMatch
from pundit.engine.replay import Clock, RealClock, replay
from pundit.orchestration.service import ClaimService
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
    ) -> None:
        self.match = match
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
            match.meta.match_id,
            self.state_payload,
            state_for=self.client_state if studio is not None else None,
        )
        self.status: SessionStatus = "created"
        self.error: str | None = None
        self.on_air = OnAirState()
        self.panels = PanelCache()
        self._task: asyncio.Task[None] | None = None
        self._swapping = False  # the replay task is being replaced (new speed): not a stop
        self._claim_count = 0
        self._last_snapshot_bucket = -1

    # ------------------------------------------------------------------ MatchSource

    @property
    def meta(self) -> MatchMeta:
        return self.match.meta

    @property
    def match_id(self) -> str:
        return self.match.meta.match_id

    def latest_seq(self) -> int:
        return len(self.state) - 1

    def view_at(self, seq: int) -> LogView:
        """Immutable prefix: the first `seq + 1` applied events."""
        return self.state.view(seq + 1)

    # ------------------------------------------------------------------ state for clients

    def snapshot(self) -> MatchSnapshot:
        return self.state.snapshot()

    def info(self) -> dict[str, Any]:
        meta = self.meta
        return {
            "match_id": self.match_id,
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
        return view if cue is None else view.model_copy(update={"speech": cue})

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
            log.exception("replay of %s failed", self.match_id)
            self.error = f"{type(exc).__name__}: {exc}"[:300]
            self._set_status("error")

    def start(self) -> None:
        if self.status in ("running", "finished") or (
            self._task is not None and not self._task.done()
        ):
            return
        self.error = None
        self._set_status("running")
        self._task = asyncio.create_task(self._run(), name=f"replay:{self.match_id}")

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
            self._task = asyncio.create_task(self._run(), name=f"replay:{self.match_id}")
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
        return f"{self.match_id}-c{self._claim_count:04d}"

    def add_claim(self, result: ClaimResult) -> None:
        self.claims[result.claim_id] = result
        while len(self.claims) > MAX_CLAIMS_PER_SESSION:
            self.claims.popitem(last=False)
        self.hub.publish("claim", public_claim(result).model_dump(mode="json"))

    async def verify(self, request: ClaimRequest) -> ClaimResult:
        result = await self.service.verify(request, self, claim_id=self.next_claim_id())
        self.add_claim(result)
        return result


def public_claim(result: ClaimResult) -> ClaimResult:
    """The claim as broadcast: without the trace."""
    return result.model_copy(update={"trace": None})


def public_talking_point(item: TalkingPoint) -> TalkingPoint:
    """The talking point as broadcast: without the trace."""
    return item.model_copy(update={"trace": None})


class SessionManager:
    """Sessions by match id; generated matches are cached (generation takes ~0.3 s)."""

    def __init__(
        self,
        service: ClaimService,
        *,
        clock_factory: Callable[[], Clock] = RealClock,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        max_sessions: int = 16,
        studio: StudioCrew | None = None,
        narrator: Narrator | None = None,
    ) -> None:
        self.service = service
        self.studio = studio
        self.narrator = narrator  # one for the app: clips are shared by every session
        self.clock_factory = clock_factory
        self.queue_size = queue_size
        self.max_sessions = max_sessions
        self.sessions: dict[str, MatchSession] = {}
        self._matches: dict[tuple[str, int], GeneratedMatch] = {}
        self._lock = asyncio.Lock()

    async def _generate(self, scenario: str, seed: int) -> GeneratedMatch:
        key = (scenario, seed)
        if key not in self._matches:
            self._matches[key] = await asyncio.to_thread(generate, scenario, seed)
        return self._matches[key]

    async def create(
        self,
        scenario: str,
        seed: int,
        *,
        speed: float = 20.0,
        from_minute: float = 0.0,
        autostart: bool = True,
        restart: bool = False,
    ) -> MatchSession:
        """Idempotent: the session of a running match is returned unless `restart=True`."""
        async with self._lock:
            match_id = f"{scenario}-{seed}"
            existing = self.sessions.get(match_id)
            if existing is not None and not restart:
                return existing
            if existing is not None:
                await existing.stop()
                del self.sessions[match_id]
            if len(self.sessions) >= self.max_sessions:
                oldest = next(iter(self.sessions))
                await self.sessions.pop(oldest).stop()
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
            )
            self.sessions[match_id] = session
        if existing is not None:
            # only now: a client that reconnects at once lands on the new session
            existing.announce_restart()
        if autostart:
            session.start()
        return session

    def get(self, match_id: str) -> MatchSession | None:
        return self.sessions.get(match_id)

    def list(self) -> list[MatchSession]:
        return list(self.sessions.values())

    def find_claim(self, claim_id: str) -> ClaimResult | None:
        for session in self.sessions.values():
            if claim_id in session.claims:
                return session.claims[claim_id]
        return None

    async def shutdown(self) -> None:
        """Cancel every replay task and wait for it (no task left behind)."""
        for session in list(self.sessions.values()):
            await session.stop()
