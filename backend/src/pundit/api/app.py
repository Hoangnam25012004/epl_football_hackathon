"""FastAPI application factory (design section 11; Day 3 section 15)."""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pundit.api import routes, ws
from pundit.api.hub import DEFAULT_QUEUE_SIZE
from pundit.api.sessions import SessionManager
from pundit.api.studio import StudioCrew
from pundit.domain.meta import DISCLAIMER
from pundit.engine.replay import Clock, RealClock
from pundit.llm.settings import Settings
from pundit.orchestration.recap_service import RecapService
from pundit.orchestration.service import ClaimService
from pundit.orchestration.studio_service import StudioService

log = logging.getLogger("pundit.api")


def default_crew(settings: Settings) -> StudioCrew:
    return StudioCrew(
        service=StudioService(settings),
        recaps=RecapService(settings),
        queue_size=settings.studio_queue_size,
        drop_at=settings.studio_drop_at,
    )


def create_app(
    settings: Settings | None = None,
    *,
    service: ClaimService | None = None,
    studio: StudioCrew | None = None,
    clock_factory: Callable[[], Clock] = RealClock,
    queue_size: int = DEFAULT_QUEUE_SIZE,
) -> FastAPI:
    """Build the app. Tests inject services (fault plans, fake clocks) and a replay clock.
    The studio runs unless STUDIO_ENABLED=false (then the app is exactly the Day 2 one)."""
    settings = settings or Settings.from_env()
    service = service or ClaimService(settings)
    if studio is None and settings.studio_enabled:
        studio = default_crew(settings)
    sessions = SessionManager(
        service, clock_factory=clock_factory, queue_size=queue_size, studio=studio
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("pundit api starting: %s", settings.describe())
        yield
        await sessions.shutdown()

    app = FastAPI(
        title="Pundit Copilot API",
        version="0.3.0",
        description=(
            "Claim verification, match moments and the studio: commentary, talking points, "
            f"lower-thirds and recaps, personalized per viewer. {DISCLAIMER}"
        ),
        lifespan=lifespan,
    )
    app.state.manager = sessions
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(routes.router)
    app.include_router(ws.router)
    return app
