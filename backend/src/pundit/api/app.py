"""FastAPI application factory (design section 11; Day 3 section 15; Day 6 /mcp)."""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from pundit.api import audio, routes, ws
from pundit.api.hub import DEFAULT_QUEUE_SIZE
from pundit.api.mcp_mount import mount_mcp
from pundit.api.sessions import SessionManager
from pundit.api.studio import StudioCrew
from pundit.domain.meta import DISCLAIMER
from pundit.engine.replay import Clock, RealClock
from pundit.llm.settings import Settings
from pundit.orchestration.recap_service import RecapService
from pundit.orchestration.service import ClaimService
from pundit.orchestration.studio_service import StudioService
from pundit.speech.narrator import Narrator, build_narrator
from pundit.speech.transcriber import Transcriber, build_transcriber

log = logging.getLogger("pundit.api")
IMMUTABLE = "public, max-age=31536000, immutable"


class UIFiles(StaticFiles):
    """The static export of the frontend (Day 4 section 3.7). HTML is always revalidated, so OBS
    and browsers pick up a new build; `_next/static/` files have hashed names and never change."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        static = path.replace("\\", "/").startswith("_next/static/")
        response.headers["Cache-Control"] = IMMUTABLE if static else "no-cache"
        return response


def ui_directory(settings: Settings) -> Path | None:
    """The built frontend to serve, or None (no UI_DIR, or no build in it)."""
    if not settings.ui_dir:
        return None
    path = Path(settings.ui_dir)
    if not (path / "index.html").is_file():
        log.warning("UI_DIR %s has no index.html (run `npm run build` in frontend/)", path)
        return None
    return path


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
    narrator: Narrator | None = None,
    transcriber: Transcriber | None = None,
) -> FastAPI:
    """Build the app. Tests inject services (fault plans, fake clocks) and a replay clock.
    The studio runs unless STUDIO_ENABLED=false (then the app is exactly the Day 2 one); the
    voice reads the studio unless TTS_PROVIDER=off (then the app is exactly the Day 4 one)."""
    settings = settings or Settings.from_env()
    service = service or ClaimService(settings)
    if studio is None and settings.studio_enabled:
        studio = default_crew(settings)
    if studio is None:
        narrator = None
    elif narrator is None:
        narrator = build_narrator(settings)
    if transcriber is None:
        transcriber = build_transcriber(settings)  # P2: None unless STT_PROVIDER is set
    sessions = SessionManager(
        service,
        clock_factory=clock_factory,
        queue_size=queue_size,
        studio=studio,
        narrator=narrator,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("pundit api starting: %s", settings.describe())
        # a mounted app's lifespan never runs: the MCP session manager is started here
        async with app.state.mcp.run():
            yield
            await sessions.shutdown()
        if narrator is not None:
            await narrator.aclose()
        if transcriber is not None:
            await transcriber.aclose()

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
    app.state.narrator = narrator
    app.state.transcriber = transcriber
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(routes.router)
    app.include_router(ws.router)
    if narrator is not None:  # mounted only with the voice: TTS_PROVIDER=off is the Day 4 app
        app.include_router(audio.router)
    if transcriber is not None:  # speech to text (P2), off by default
        app.include_router(audio.stt_router)
    # Day 6: /mcp and /mcp/ before the UI's catch-all; MCP_ENABLED=false is the Day 5 app
    app.state.mcp = mount_mcp(app, sessions, settings)
    ui = ui_directory(settings)
    if ui is not None:  # mounted last: every API route and /docs match first
        app.mount("/", UIFiles(directory=ui, html=True), name="ui")
    return app
