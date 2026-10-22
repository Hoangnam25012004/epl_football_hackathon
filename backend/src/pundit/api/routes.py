"""REST endpoints (design section 11.2; Day 3 section 15.1). Errors: 404 unknown match / claim /
item, 422 bad input or profile, 409 a recap that is not ready yet. An LLM problem is never a
5xx: the claim (or the studio item) is answered by the deterministic path and flagged."""

import statistics
from typing import Annotated, Any, Literal, get_args

from fastapi import APIRouter, HTTPException, Query, Request
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
)
from pundit.agents.personalize import ProfileError, parse_profile, render_lower_third, render_recap
from pundit.api.sessions import MatchSession, SessionManager, public_claim, public_talking_point
from pundit.api.studio import StudioDirector, StudioKind
from pundit.engine.scenarios import SCENARIOS
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


class TeamBrief(BaseModel):
    id: str
    name: str
    short_name: str


class MatchInfo(BaseModel):
    match_id: str
    scenario: str
    seed: int
    status: Literal["created", "running", "finished", "stopped", "error"]
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


class MatchView(BaseModel):
    info: MatchInfo
    snapshot: MatchSnapshot


class StudioHealth(BaseModel):
    enabled: bool
    queued: int
    dropped: int
    lag_ms_p95: float | None
    degraded: list[str]


class Health(BaseModel):
    status: Literal["ok"]
    llm_provider: str
    model: str
    degraded: list[str]
    sessions: int
    synthetic: bool = True
    studio: StudioHealth | None = None  # absent when STUDIO_ENABLED=false (the Day 2 shape)


class TalkingPointStatusBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TalkingPointStatus


class StudioItemDetail(BaseModel):
    """A canonical studio item with the trace of the job that made it (Agent Trace drawer)."""

    kind: StudioKind
    item: dict[str, Any]
    trace: list[TraceSpan]


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
    lags = [lag for d in directors for lag in d.lags()]
    p95 = None
    if len(lags) == 1:
        p95 = round(lags[0], 1)
    elif lags:
        p95 = round(statistics.quantiles(lags, n=20)[18], 1)
    return StudioHealth(
        enabled=True,
        queued=sum(d.stats()["queued"] for d in directors),
        dropped=sum(d.dropped for d in directors),
        lag_ms_p95=p95,
        degraded=list(crew.service.degraded_now()),
    )


@router.get("/healthz", response_model=Health, response_model_exclude_unset=True)
async def healthz(request: Request) -> Health:
    sessions = manager(request)
    service = sessions.service
    studio = _studio_health(sessions)
    return Health(
        status="ok",
        llm_provider=service.provider,
        model=service.model,
        degraded=list(service.degraded_now()),
        sessions=len(sessions.sessions),
        synthetic=True,
        **({"studio": studio} if studio is not None else {}),
    )


@router.post("/matches", response_model=MatchInfo)
async def create_match(body: CreateMatch, request: Request) -> MatchInfo:
    session = await manager(request).create(
        body.scenario,
        body.seed,
        speed=body.speed,
        from_minute=body.from_minute,
        autostart=body.autostart,
        restart=body.restart,
    )
    return _info(session)


@router.post("/matches/{match_id}/start", response_model=MatchInfo)
async def start_match(match_id: str, request: Request) -> MatchInfo:
    session = _session(request, match_id)
    session.start()
    return _info(session)


@router.post("/matches/{match_id}/stop", response_model=MatchInfo)
async def stop_match(match_id: str, request: Request) -> MatchInfo:
    session = _session(request, match_id)
    await session.stop()
    return _info(session)


@router.get("/matches/{match_id}", response_model=MatchView)
async def get_match(match_id: str, request: Request) -> MatchView:
    session = _session(request, match_id)
    return MatchView(info=_info(session), snapshot=session.snapshot())


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
) -> RecapView:
    _, director = _director(request, match_id)
    recap = director.recaps.get(scope)
    if recap is None:
        raise HTTPException(
            status_code=409,
            detail=f"recap_not_ready: the {scope} recap comes after "
            f"{'half-time' if scope == 'H1' else 'full time'}",
        )
    return render_recap(recap, Profile(language=lang, mode=mode))


@router.get("/matches/{match_id}/studio/{item_id}", response_model=StudioItemDetail)
async def get_studio_item(match_id: str, item_id: str, request: Request) -> StudioItemDetail:
    _, director = _director(request, match_id)
    found = director.find(item_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"unknown studio item {item_id!r}")
    kind, item = found
    payload = item.model_dump(mode="json")
    return StudioItemDetail(kind=kind, item=payload, trace=list(director.trace_of(item_id)))


@router.post("/matches/{match_id}/talking-points/{item_id}/status", response_model=TalkingPoint)
async def set_talking_point_status(
    match_id: str, item_id: str, body: TalkingPointStatusBody, request: Request
) -> TalkingPoint:
    session, _ = _director(request, match_id)
    updated = session.set_talking_point_status(item_id, body.status)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"unknown talking point {item_id!r}")
    return public_talking_point(updated)
