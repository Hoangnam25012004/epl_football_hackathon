"""REST endpoints (design section 11.2; Day 3 section 15.1). Errors: 404 unknown match / claim /
item, 422 bad input or profile, 409 a recap that is not ready yet. An LLM problem is never a
5xx: the claim (or the studio item) is answered by the deterministic path and flagged."""

from typing import Annotated, Any, Literal, get_args

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from pundit.agents.contracts import (
    ClaimRequest,
    ClaimResult,
    CommentaryView,
    Lang,
    LowerThirdView,
    Moment,
    Profile,
    RecapScope,
    RecapView,
    Register,
    TalkingPoint,
    TalkingPointStatus,
    TraceSpan,
    VoiceGender,
)
from pundit.agents.personalize import ProfileError, parse_profile, render_lower_third
from pundit.agents.voices import voice_list
from pundit.api.onair import OnAirCommand, OnAirError, OnAirState
from pundit.api.panels import MAX_IDS, MAX_LAST, EventsPage, Period, StatsPanel, TimelineView
from pundit.api.sessions import (
    MatchSession,
    SessionManager,
    SessionsFull,
    TooManySessions,
    public_claim,
    public_talking_point,
)
from pundit.api.studio import StudioDirector, StudioKind
from pundit.domain.clients import client_ip
from pundit.domain.ids import TOKEN_PATTERN
from pundit.domain.percentiles import p95
from pundit.engine.rosters import roster_team
from pundit.engine.scenarios import EXAMPLE_CLAIMS, SCENARIOS
from pundit.llm.settings import Settings
from pundit.speech.narrator import Narrator
from pundit.state.match_state import MatchSnapshot

router = APIRouter()

ScenarioKey = Literal["dominant_loser", "comeback_var", "red_card_swing", "balanced"]
assert set(get_args(ScenarioKey)) == set(SCENARIOS), "keep ScenarioKey in sync with SCENARIOS"


class CreateMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: ScenarioKey
    seed: int = Field(default=42, ge=0, le=1_000_000)
    speed: float = Field(default=20.0, gt=0, le=1000)
    from_minute: float = Field(default=0.0, ge=0, le=120)
    autostart: bool = True
    restart: bool = False
    # Day 6 (docs/day6-design.md section 6.3): a session of one's own, named by its token
    private: bool = False
    session: str | None = Field(default=None, pattern=TOKEN_PATTERN)


class TeamBrief(BaseModel):
    id: str
    name: str
    short_name: str


class StudioStats(BaseModel):
    """The session's studio crew (Day 3 section 14.4)."""

    queued: int
    processed: int
    dropped: int
    errors: int
    lag_ms_p95: float | None
    degraded: dict[str, int]
    commentary: int
    talking_points: int
    lower_thirds: int
    recaps: list[str]


class MatchInfo(BaseModel):
    match_id: str  # the session id: "dominant_loser-42", or "dominant_loser-42~k3v9x2ma"
    private: bool = False  # only present for a private session (Day 6)
    scenario: str
    seed: int
    status: Literal["created", "running", "paused", "finished", "stopped", "error"]
    error: str | None
    speed: float
    from_minute: float
    events_total: int
    events_applied: int
    home: TeamBrief
    away: TeamBrief
    moments: int
    claims: int
    synthetic: bool
    disclaimer: str
    studio: StudioStats | None = None  # absent with STUDIO_ENABLED=false (the Day 2 shape)


class MatchView(BaseModel):
    info: MatchInfo
    snapshot: MatchSnapshot


class StudioHealth(BaseModel):
    enabled: bool
    queued: int
    dropped: int
    lag_ms_p95: float | None
    degraded: list[str]


class McpHealth(BaseModel):
    """MCP on /mcp (docs/day6-design.md section 3.7)."""

    enabled: bool
    status: Literal["ok", "unavailable"]
    transport: str
    path: str
    calls: int = 0
    errors: int = 0
    refused: int = 0  # arguments the client can correct, rate limits included
    rate_limited: int = 0
    by_tool: dict[str, int] = Field(default_factory=dict)


class SpeechHealth(BaseModel):
    """The voice of the app (docs/day5-design.md section 9.4)."""

    provider: str
    languages: list[str]
    voices: list[str]
    store: str
    breaker: str
    degraded: list[str]
    synthesized: int
    cache_hits: int
    durable_hits: int
    throttled: int
    guard_rejected: int
    errors: dict[str, int]
    chars_today: int
    char_budget: int
    latency_ms_p95: float | None
    queue: int
    stt: str


class KeepaliveHealth(BaseModel):
    """The app's ping of its own public URL (docs/day7-design.md section 2.2)."""

    url: str
    interval_s: float
    checks: int
    failures: int
    last_ok_at: str | None
    last_status: int | None
    last_error: str | None


class Health(BaseModel):
    status: Literal["ok"]
    llm_provider: str
    model: str
    degraded: list[str]
    sessions: int
    synthetic: bool = True
    studio: StudioHealth | None = None  # absent when STUDIO_ENABLED=false (the Day 2 shape)
    speech: SpeechHealth | None = None  # absent when TTS_PROVIDER=off (the Day 4 shape)
    mcp: McpHealth | None = None  # absent when MCP_ENABLED=false (the Day 5 shape)
    keepalive: KeepaliveHealth | None = None  # only with KEEPALIVE_URL (the public host)
    build: str | None = None  # only with PUNDIT_BUILD: the commit the image was built from


class TalkingPointStatusBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TalkingPointStatus


class StudioItemDetail(BaseModel):
    """A canonical studio item with the trace of the job that made it (Agent Trace drawer)."""

    kind: StudioKind
    item: dict[str, Any]
    trace: list[TraceSpan]


class TeamCard(TeamBrief):
    primary_color: str
    secondary_color: str


class ScenarioInfo(BaseModel):
    """A scenario as the match picker shows it (Day 4 section 3.6)."""

    key: str
    title_vi: str
    title_en: str
    home: TeamCard
    away: TeamCard
    default_seed: int
    example_claims: dict[Lang, list[str]]


class SpeedBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speed: float = Field(gt=0, le=1000)


class PlayerBrief(BaseModel):
    """A fictional player, for the player-focus picker of the fan player."""

    id: str
    team: str
    shirt: int
    name: str
    short_name: str
    position: str


DEFAULT_SEED = 42


def manager(request: Request) -> SessionManager:
    found: SessionManager = request.app.state.manager
    return found


def _session(request: Request, match_id: str) -> MatchSession:
    session = manager(request).get(match_id)
    if session is None:
        raise HTTPException(
            status_code=404, detail=f"unknown match {match_id!r}; POST /matches first"
        )
    return session


def _info(session: MatchSession) -> MatchInfo:
    return MatchInfo.model_validate(session.info())


def _studio_health(sessions: SessionManager) -> StudioHealth | None:
    crew = sessions.studio
    if crew is None:
        return None
    directors = [s.director for s in sessions.sessions.values() if s.director is not None]
    lag = p95(lag for d in directors for lag in d.lags())
    return StudioHealth(
        enabled=True,
        queued=sum(d.stats()["queued"] for d in directors),
        dropped=sum(d.dropped for d in directors),
        lag_ms_p95=None if lag is None else round(lag, 1),
        degraded=list(crew.service.degraded_now()),
    )


@router.get("/healthz", response_model=Health, response_model_exclude_unset=True)
async def healthz(request: Request) -> Health:
    sessions = manager(request)
    service = sessions.service
    # only the blocks that exist are set: STUDIO_ENABLED=false / TTS_PROVIDER=off keep the
    # older shapes (response_model_exclude_unset)
    blocks: dict[str, Any] = {}
    studio = _studio_health(sessions)
    if studio is not None:
        blocks["studio"] = studio
    speech = _speech_health(request)
    if speech is not None:
        blocks["speech"] = speech
    mcp = getattr(request.app.state, "mcp", None)
    if mcp is not None and mcp.status != "disabled":
        blocks["mcp"] = McpHealth.model_validate(mcp.health())
    keepalive = getattr(request.app.state, "keepalive", None)
    if keepalive is not None:
        blocks["keepalive"] = KeepaliveHealth.model_validate(keepalive.health())
    build = request.app.state.settings.build
    if build is not None:
        blocks["build"] = build
    return Health(
        status="ok",
        llm_provider=service.provider,
        model=service.model,
        degraded=list(service.degraded_now()),
        sessions=len(sessions.sessions),
        synthetic=True,
        **blocks,
    )


def _speech_health(request: Request) -> SpeechHealth | None:
    narrator = sessions_narrator(request)
    if narrator is None:
        return None
    stats = narrator.stats()
    voices = [v.voice for v in voice_list(narrator.settings.tts_languages, narrator.server_audio)]
    keep = set(SpeechHealth.model_fields) - {"voices", "stt"}
    return SpeechHealth(
        **{k: v for k, v in stats.items() if k in keep},
        voices=voices,
        stt=narrator.settings.stt_provider,
    )


def sessions_narrator(request: Request) -> Narrator | None:
    narrator: Narrator | None = getattr(request.app.state, "narrator", None)
    return narrator


def _refused(status: int, detail: str, retry_after_s: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"detail": detail, "retry_after_s": retry_after_s},
        headers={"Retry-After": str(retry_after_s)},
    )


@router.post(
    "/matches",
    response_model=MatchInfo,
    response_model_exclude_unset=True,
    responses={429: {"description": "too_many_sessions"}, 503: {"description": "demo_full"}},
)
async def create_match(body: CreateMatch, request: Request) -> Any:
    settings: Settings = request.app.state.settings
    peer = request.client.host if request.client is not None else None
    client = client_ip(
        peer, request.headers.get("x-forwarded-for"), trust_proxy=settings.trust_proxy
    )
    try:
        session = await manager(request).create(
            body.scenario,
            body.seed,
            speed=body.speed,
            from_minute=body.from_minute,
            autostart=body.autostart,
            restart=body.restart,
            private=body.private,
            token=body.session,
            client=client,
        )
    except SessionsFull as exc:
        return _refused(503, "demo_full", exc.retry_after_s)
    except TooManySessions as exc:
        return _refused(429, "too_many_sessions", exc.retry_after_s)
    return _info(session)


@router.post(
    "/matches/{match_id}/start", response_model=MatchInfo, response_model_exclude_unset=True
)
async def start_match(match_id: str, request: Request) -> MatchInfo:
    session = _session(request, match_id)
    session.start()
    return _info(session)


@router.post(
    "/matches/{match_id}/stop", response_model=MatchInfo, response_model_exclude_unset=True
)
async def stop_match(match_id: str, request: Request) -> MatchInfo:
    session = _session(request, match_id)
    await session.stop()
    return _info(session)


@router.get("/matches/{match_id}", response_model=MatchView, response_model_exclude_unset=True)
async def get_match(match_id: str, request: Request) -> MatchView:
    session = _session(request, match_id)
    return MatchView(info=_info(session), snapshot=session.snapshot())


# --------------------------------------------------------------------------- Day 4: UI support


def _team_card(team_id: str) -> TeamCard:
    team = roster_team(team_id)
    return TeamCard(
        id=team.id,
        name=team.name,
        short_name=team.short_name,
        primary_color=team.primary_color,
        secondary_color=team.secondary_color,
    )


@router.get("/scenarios", response_model=list[ScenarioInfo])
async def list_scenarios() -> list[ScenarioInfo]:
    return [
        ScenarioInfo(
            key=scenario.key,
            title_vi=scenario.title_vi,
            title_en=scenario.title_en,
            home=_team_card(scenario.home),
            away=_team_card(scenario.away),
            default_seed=DEFAULT_SEED,
            example_claims={
                "vi": list(EXAMPLE_CLAIMS[scenario.key]["vi"]),
                "en": list(EXAMPLE_CLAIMS[scenario.key]["en"]),
            },
        )
        for scenario in SCENARIOS.values()
    ]


@router.get("/matches", response_model=list[MatchInfo], response_model_exclude_unset=True)
async def list_matches(request: Request) -> list[MatchInfo]:
    return [_info(session) for session in manager(request).list()]


@router.get("/matches/{match_id}/players", response_model=list[PlayerBrief])
async def get_players(match_id: str, request: Request) -> list[PlayerBrief]:
    """Both squads, home team first, by shirt number."""
    meta = _session(request, match_id).meta
    order = {team: index for index, team in enumerate(meta.team_ids)}
    players = sorted(meta.players.values(), key=lambda p: (order.get(p.team_id, 9), p.shirt))
    return [
        PlayerBrief(
            id=player.id,
            team=player.team_id,
            shirt=player.shirt,
            name=player.name,
            short_name=player.short_name,
            position=player.position,
        )
        for player in players
    ]


@router.post(
    "/matches/{match_id}/pause", response_model=MatchInfo, response_model_exclude_unset=True
)
async def pause_match(match_id: str, request: Request) -> MatchInfo:
    """Pause the replay; studio jobs already queued still finish (unlike /stop)."""
    session = _session(request, match_id)
    await session.pause()
    return _info(session)


@router.post(
    "/matches/{match_id}/resume", response_model=MatchInfo, response_model_exclude_unset=True
)
async def resume_match(match_id: str, request: Request) -> MatchInfo:
    session = _session(request, match_id)
    session.resume()
    return _info(session)


@router.post(
    "/matches/{match_id}/speed", response_model=MatchInfo, response_model_exclude_unset=True
)
async def set_match_speed(match_id: str, body: SpeedBody, request: Request) -> MatchInfo:
    session = _session(request, match_id)
    await session.set_speed(body.speed)
    return _info(session)


@router.get("/matches/{match_id}/timeline", response_model=TimelineView)
async def get_timeline(
    match_id: str,
    request: Request,
    window: Annotated[float, Query(gt=0, le=45, description="rolling window, minutes")] = 10.0,
) -> TimelineView:
    """Momentum of the home team per played minute, with markers (goals, VAR, red cards)."""
    return _session(request, match_id).timeline(window)


@router.get("/matches/{match_id}/stats", response_model=StatsPanel)
async def get_stats(match_id: str, request: Request, period: Period = "FULL") -> StatsPanel:
    return _session(request, match_id).stats(period)


@router.get("/matches/{match_id}/events", response_model=EventsPage)
async def get_events(
    match_id: str,
    request: Request,
    ids: Annotated[str | None, Query(description="comma-separated event ids")] = None,
    last: Annotated[int | None, Query(ge=1, le=MAX_LAST, description="the last N events")] = None,
) -> EventsPage:
    """Applied events only: an id the replay has not reached yet is listed as missing."""
    session = _session(request, match_id)
    if (ids is None) == (last is None):
        raise HTTPException(status_code=422, detail="give either ids or last")
    if ids is not None:
        wanted = [part.strip() for part in ids.split(",") if part.strip()]
        if not wanted or len(wanted) > MAX_IDS:
            raise HTTPException(status_code=422, detail=f"give 1 to {MAX_IDS} event ids")
        return session.events(wanted)
    assert last is not None
    return session.last_events(last)


@router.get("/matches/{match_id}/moments", response_model=list[Moment])
async def get_moments(
    match_id: str,
    request: Request,
    since: Annotated[int | None, Query(description="only moments after this event seq")] = None,
) -> list[Moment]:
    session = _session(request, match_id)
    return [m for m in session.moments if since is None or m.seq > since]


@router.post("/claims/verify", response_model=ClaimResult)
async def verify_claim(body: ClaimRequest, request: Request) -> Any:
    session = _session(request, body.match_id)
    result = await session.verify(body)
    return result if body.include_trace else public_claim(result)


@router.get("/claims/{claim_id}", response_model=ClaimResult)
async def get_claim(claim_id: str, request: Request) -> ClaimResult:
    result = manager(request).find_claim(claim_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"unknown claim {claim_id!r}")
    return result


# --------------------------------------------------------------------------- studio (Day 3)


def _director(request: Request, match_id: str) -> tuple[MatchSession, StudioDirector]:
    session = _session(request, match_id)
    if session.director is None:
        raise HTTPException(status_code=404, detail="the studio is off (STUDIO_ENABLED=false)")
    return session, session.director


def _profile(request: Request, session: MatchSession) -> Profile:
    try:
        return parse_profile(request.query_params, session.context)
    except ProfileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/matches/{match_id}/commentary", response_model=list[CommentaryView])
async def get_commentary(
    match_id: str,
    request: Request,
    since_seq: Annotated[int | None, Query(description="only items after this event seq")] = None,
    mode: Annotated[Register | None, Query(description="analyst (default) or casual")] = None,
    lang: Annotated[Lang | None, Query(description="vi (default) or en")] = None,
    club: Annotated[str | None, Query(description="favourite club: team id or short name")] = None,
    player: Annotated[str | None, Query(description="player focus: player id")] = None,
    beginner: Annotated[str | None, Query(description="1 = explain the rules")] = None,
    metric: Annotated[str | None, Query(description="one metric only, e.g. xg")] = None,
) -> list[dict[str, Any]]:
    """Commentary for one profile. A rule is explained in full where it first occurs in the
    match (a REST client has no per-connection memory)."""
    del mode, lang, club, player, beginner, metric  # read by parse_profile from the query
    session, director = _director(request, match_id)
    profile = _profile(request, session)
    views = []
    for item in director.commentary.values():
        if since_seq is not None and item.as_of_seq <= since_seq:
            continue
        seen = {rule for rule in item.rules if rule not in item.first_rules}
        views.append(session.render_commentary(item, profile, seen)[0])
    return views


@router.get("/matches/{match_id}/talking-points", response_model=list[TalkingPoint])
async def get_talking_points(
    match_id: str,
    request: Request,
    since_seq: Annotated[int | None, Query(description="only items after this event seq")] = None,
) -> list[TalkingPoint]:
    _, director = _director(request, match_id)
    return [
        public_talking_point(tp)
        for tp in director.talking_points.values()
        if since_seq is None or tp.as_of_seq > since_seq
    ]


@router.get("/matches/{match_id}/lower-thirds", response_model=list[LowerThirdView])
async def get_lower_thirds(
    match_id: str, request: Request, lang: Lang = "vi"
) -> list[LowerThirdView]:
    _, director = _director(request, match_id)
    profile = Profile(language=lang)
    return [render_lower_third(lt, profile) for lt in director.lower_thirds.values()]


@router.get("/matches/{match_id}/recap", response_model=RecapView)
async def get_recap(
    match_id: str,
    request: Request,
    scope: RecapScope = "FULL",
    lang: Lang = "vi",
    mode: Register = "analyst",
    voice: Annotated[VoiceGender | None, Query(description="read it aloud (Day 5)")] = None,
) -> RecapView:
    session, director = _director(request, match_id)
    recap = director.recaps.get(scope)
    if recap is None:
        raise HTTPException(
            status_code=409,
            detail=f"recap_not_ready: the {scope} recap comes after "
            f"{'half-time' if scope == 'H1' else 'full time'}",
        )
    profile = Profile(language=lang, mode=mode, voice=voice)
    return session.recap_view(recap, profile, live=voice is not None)


@router.get("/matches/{match_id}/studio/{item_id}", response_model=StudioItemDetail)
async def get_studio_item(match_id: str, item_id: str, request: Request) -> StudioItemDetail:
    session, director = _director(request, match_id)
    found = director.find(item_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"unknown studio item {item_id!r}")
    kind, item = found
    payload = item.model_dump(mode="json")
    trace = list(director.trace_of(item_id))
    if session.narrator is not None:  # Day 5: the Narrator's spans follow the studio job
        trace += session.narrator.spans_of(item_id)
    return StudioItemDetail(kind=kind, item=payload, trace=trace)


@router.post("/matches/{match_id}/talking-points/{item_id}/status", response_model=TalkingPoint)
async def set_talking_point_status(
    match_id: str, item_id: str, body: TalkingPointStatusBody, request: Request
) -> TalkingPoint:
    session, _ = _director(request, match_id)
    updated = session.set_talking_point_status(item_id, body.status)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"unknown talking point {item_id!r}")
    return public_talking_point(updated)


@router.get("/matches/{match_id}/on-air", response_model=OnAirState)
async def get_on_air(match_id: str, request: Request) -> OnAirState:
    session, _ = _director(request, match_id)
    return session.on_air


@router.post("/matches/{match_id}/on-air", response_model=OnAirState)
async def command_on_air(match_id: str, body: OnAirCommand, request: Request) -> OnAirState:
    """An editor's decision about the overlay: take / clear / hold / release / auto."""
    session, _ = _director(request, match_id)
    try:
        return session.command_on_air(body)
    except OnAirError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except LookupError:
        raise HTTPException(
            status_code=404, detail=f"unknown lower-third {body.item_id!r}"
        ) from None
