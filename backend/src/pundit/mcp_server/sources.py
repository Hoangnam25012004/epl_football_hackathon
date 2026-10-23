"""Where the MCP tools read a match (docs/day6-design.md section 3.2).

A view is pinned at one seq for the whole call, so a replay that moves on meanwhile never mixes
two states in one answer. `StaticMatches` generates a scenario and seed (deterministic, any
minute); the API puts its live sessions in front of it (api/mcp_mount.py).
"""

import asyncio
import re
import threading
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from pundit.agents.contracts import Moment
from pundit.agents.moments import detect_all
from pundit.domain.events import Event
from pundit.domain.log import LogView
from pundit.domain.matchtime import MS_PER_MINUTE, format_minute, parse_display_minute
from pundit.engine.generator import generate
from pundit.engine.rosters import roster_team
from pundit.engine.scenarios import SCENARIOS
from pundit.mcp_server.errors import ToolInputError
from pundit.orchestration.service import MatchSource as ClaimSource
from pundit.orchestration.service import StaticSource

DEFAULT_SEED = 42  # the API's default seed (api/routes.py DEFAULT_SEED)
MAX_SEED = 1_000_000  # as POST /matches
STATIC_CACHE = 8
MATCH_ID_RE = re.compile(r"^([a-z0-9_]+)-(\d{1,7})$")
SESSION_ID_RE = re.compile(r"^[a-z0-9_]+-\d{1,7}(~[a-z0-9]{8})?$")

Origin = Literal["live", "replay"]


@dataclass(frozen=True)
class ResolvedView:
    """One match at one moment, as every tool of a call sees it."""

    view: LogView  # pinned: its events never change during the call
    source: Origin
    match_id: str  # the id that was asked (a session id for a live session)
    moments: tuple[Moment, ...]  # detected up to the view's last event
    claims: ClaimSource  # what the claim verifier reads (a session or a static match)
    complete: bool  # no event will follow (a static match, or a finished live one)

    @property
    def as_of_seq(self) -> int:
        return self.view.last_seq

    @property
    def as_of_minute(self) -> str:
        last = self.view.last
        return format_minute(last.period, last.clock_ms) if last is not None else "0'"


class ScenarioEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    title_en: str
    title_vi: str
    match_id: str  # the default seed: ready to use in every tool
    home: str
    away: str


class LiveEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    match_id: str
    scenario: str
    seed: int
    status: str
    minute: str
    score: str  # "1-2", home first


class MatchSource(Protocol):
    async def resolve(self, match_id: str, as_of: str | None) -> ResolvedView: ...

    def live(self) -> list[LiveEntry]: ...


def check_match_id(match_id: str) -> str:
    text = match_id.strip()
    if not SESSION_ID_RE.match(text):
        raise ToolInputError(
            f"match_id {match_id!r} is not a match id such as 'dominant_loser-42' (call "
            "list_matches for the matches you can use)"
        )
    return text


def seq_for(events: Sequence[Event], as_of: str | None, *, complete: bool) -> int:
    """The seq of the last event at `as_of` ("63'" = up to 62:59 on the clock, "45+2'", "HT",
    "FT"; nothing = the latest event). A minute a live match has not finished playing is refused:
    the tools never answer about the future."""
    if not events:
        raise ToolInputError("the match has not started yet")
    last = events[-1]
    now = format_minute(last.period, last.clock_ms)
    text = (as_of or "").strip()
    key = text.upper()
    if key in ("", "NOW", "LIVE"):
        return last.seq
    if key == "FT":
        if not complete:
            raise ToolInputError(f"the match is not over: it is at {now}; leave as_of empty")
        return last.seq
    if key == "HT":
        for event in events:
            if event.type == "period_end" and event.period == 1:
                return event.seq
        raise ToolInputError(f"half-time has not been reached: the match is at {now}")
    try:
        period, clock_ms = parse_display_minute(text)
    except ValueError as exc:
        raise ToolInputError(
            f'as_of {as_of!r}: {exc}; use a minute such as "63\'", "45+2\'", "HT" or "FT"'
        ) from None
    end = clock_ms + MS_PER_MINUTE
    if not complete and (last.period, last.clock_ms) < (period, end):
        raise ToolInputError(f"{text} has not been played yet: the match is at {now}")
    found: int | None = None
    for event in events:
        if event.period < period or (event.period == period and event.clock_ms < end):
            found = event.seq
        else:
            break
    if found is None:
        raise ToolInputError(f"no event before {text}")
    return found


@dataclass(frozen=True)
class _Generated:
    source: StaticSource
    events: tuple[Event, ...]
    moments: tuple[Moment, ...]


def _generate(scenario: str, seed: int) -> _Generated:
    match = generate(scenario, seed)
    return _Generated(
        source=StaticSource(match.meta, match.events),
        events=tuple(match.events),
        moments=tuple(detect_all(match.meta, match.events)),
    )


class StaticMatches:
    """Generated matches by `scenario-seed`, cached (a match takes about a second to build).

    Matches are built in a worker thread, one at a time: generating is CPU work that holds the
    GIL, so a burst of new seeds queues up instead of slowing a live replay down.
    """

    def __init__(self, size: int = STATIC_CACHE) -> None:
        self._cache: OrderedDict[str, _Generated] = OrderedDict()
        self._size = size
        self._cache_lock = threading.Lock()  # short: the event loop takes it too
        self._build_lock = threading.Lock()  # long: held while a match is generated

    @staticmethod
    def parse(match_id: str) -> tuple[str, int]:
        found = MATCH_ID_RE.match(match_id)
        if found is None or found.group(1) not in SCENARIOS:
            known = ", ".join(f"{key}-{DEFAULT_SEED}" for key in SCENARIOS)
            raise ToolInputError(
                f"unknown match {match_id!r}: use a scenario and a seed, e.g. {known} "
                "(list_matches lists them)"
            )
        seed = int(found.group(2))
        if seed > MAX_SEED:
            raise ToolInputError(f"seed {seed} is too large (at most {MAX_SEED})")
        return found.group(1), seed

    def _cached(self, match_id: str) -> _Generated | None:
        with self._cache_lock:
            hit = self._cache.get(match_id)
            if hit is not None:
                self._cache.move_to_end(match_id)
            return hit

    def _build(self, match_id: str, scenario: str, seed: int) -> _Generated:
        with self._build_lock:
            hit = self._cached(match_id)  # built while this call waited for the lock
            if hit is None:
                hit = _generate(scenario, seed)
                with self._cache_lock:
                    self._cache[match_id] = hit
                    while len(self._cache) > self._size:
                        self._cache.popitem(last=False)
            return hit

    async def _get(self, match_id: str) -> _Generated:
        scenario, seed = self.parse(match_id)
        hit = self._cached(match_id)
        if hit is not None:
            return hit
        # off the event loop: a live replay keeps its pace while a match is generated
        return await asyncio.to_thread(self._build, match_id, scenario, seed)

    async def resolve(self, match_id: str, as_of: str | None) -> ResolvedView:
        generated = await self._get(check_match_id(match_id))
        seq = seq_for(generated.events, as_of, complete=True)
        return ResolvedView(
            view=generated.source.view_at(seq),
            source="replay",
            match_id=match_id,
            moments=tuple(m for m in generated.moments if m.seq <= seq),
            claims=generated.source,
            complete=True,
        )

    def live(self) -> list[LiveEntry]:
        return []

    @staticmethod
    def scenarios() -> list[ScenarioEntry]:
        return [
            ScenarioEntry(
                key=scenario.key,
                title_en=scenario.title_en,
                title_vi=scenario.title_vi,
                match_id=f"{scenario.key}-{DEFAULT_SEED}",
                home=roster_team(scenario.home).name,
                away=roster_team(scenario.away).name,
            )
            for scenario in SCENARIOS.values()
        ]
