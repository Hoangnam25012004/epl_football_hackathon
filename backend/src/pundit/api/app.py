"""FastAPI application factory (design section 11)."""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pundit.api import routes, ws
from pundit.api.hub import DEFAULT_QUEUE_SIZE
from pundit.api.sessions import SessionManager
from pundit.domain.meta import DISCLAIMER
from pundit.engine.replay import Clock, RealClock
from pundit.llm.settings import Settings
from pundit.orchestration.service import ClaimService

log = logging.getLogger("pundit.api")


def create_app(
    settings: Settings | None = None,
    *,
    service: ClaimService | None = None,
    clock_factory: Callable[[], Clock] = RealClock,
    queue_size: int = DEFAULT_QUEUE_SIZE,
) -> FastAPI:
    """Build the app. Tests inject a service (fault plans, fake clocks) and a replay clock."""
    settings = settings or Settings.from_env()
    service = service or ClaimService(settings)
    sessions = SessionManager(service, clock_factory=clock_factory, queue_size=queue_size)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("pundit api starting: %s", settings.describe())
        yield
        await sessions.shutdown()

    app = FastAPI(
        title="Pundit Copilot API",
        version="0.2.0",
        description=f"Claim verification and match moments. {DISCLAIMER}",
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
