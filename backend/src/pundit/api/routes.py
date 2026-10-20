"""REST endpoints (design section 11.2). Errors: 404 unknown match / claim, 422 bad input.
An LLM problem is never a 5xx: the claim is answered by the deterministic path and flagged."""

from typing import Annotated, Any, Literal, get_args

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from pundit.agents.contracts import ClaimRequest, ClaimResult, Moment
from pundit.api.sessions import MatchSession, SessionManager, public_claim
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


class Health(BaseModel):
    status: Literal["ok"]
    llm_provider: str
    model: str
    degraded: list[str]
    sessions: int
    synthetic: bool = True


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


@router.get("/healthz", response_model=Health)
async def healthz(request: Request) -> Health:
    sessions = manager(request)
    service = sessions.service
    return Health(
        status="ok",
        llm_provider=service.provider,
        model=service.model,
        degraded=list(service.degraded_now()),
        sessions=len(sessions.sessions),
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
