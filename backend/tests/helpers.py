"""Shared test helpers: cached generated matches, claim contexts and a mock claim service."""

import asyncio
import os
from collections.abc import Coroutine
from functools import cache
from typing import Any

from pundit.agents.analyst_rules import claim_language
from pundit.agents.context import build_match_context
from pundit.agents.contracts import (
    ClaimContext,
    ClaimRequest,
    ClaimResult,
    Lang,
    MatchContext,
)
from pundit.domain.log import LogView
from pundit.engine.generator import generate
from pundit.engine.match import GeneratedMatch
from pundit.llm.settings import Settings
from pundit.orchestration.evaluation import seq_at
from pundit.orchestration.service import ClaimService, StaticSource

# The Day 2 API tests run with the studio on (default) and again with STUDIO_ENABLED=false,
# which must give exactly the Day 2 behaviour (docs/day3-design.md section 16.6).
STUDIO_ON = os.environ.get("STUDIO_ENABLED", "true").strip().lower() not in {"0", "false", "no"}
# Day 5: and again with TTS_PROVIDER=off, which must give exactly the Day 4 app
# (docs/day5-design.md section 12.5); otherwise the API tests run with the mock voice
TTS_ON = os.environ.get("TTS_PROVIDER", "mock").strip().lower() != "off"
DAY2_HELLO_KEYS = {"match", "snapshot", "moments", "claims", "degraded"}
# Day 4 adds `on_air` (the editors' lower-third decision) to the studio hello
STUDIO_HELLO_KEYS = {"profile", "talking_points", "lower_thirds", "commentary", "recaps", "on_air"}
SCENARIO_KEYS = ("dominant_loser", "comeback_var", "red_card_swing", "balanced")
SCRIPTED_KEYS = ("dominant_loser", "comeback_var", "red_card_swing")
SEEDS = tuple(range(8))
DL = ("dominant_loser", 42)  # the golden match of the claim verifier (design section 13.6)


@cache
def generated(scenario: str, seed: int, physical: bool = True) -> GeneratedMatch:
    return generate(scenario, seed, physical=physical)


@cache
def source(scenario: str = DL[0], seed: int = DL[1]) -> StaticSource:
    match = generated(scenario, seed)
    return StaticSource(match.meta, match.events)


def view_at(scenario: str = DL[0], seed: int = DL[1], at: str | None = None) -> LogView:
    """Prefix view through the end of a displayed minute ("30'"), or the whole match."""
    src = source(scenario, seed)
    seq = seq_at(generated(scenario, seed).events, at)
    return src.view_at(src.latest_seq() if seq is None else seq)


@cache
def match_context(scenario: str = DL[0], seed: int = DL[1], at: str | None = None) -> MatchContext:
    return build_match_context(view_at(scenario, seed, at))


def claim_context(
    text: str,
    *,
    scenario: str = DL[0],
    seed: int = DL[1],
    at: str | None = None,
    language: Lang | None = None,
) -> ClaimContext:
    match = match_context(scenario, seed, at)
    return ClaimContext(
        claim_id="test-c0001",
        request=ClaimRequest(match_id=match.match_id, text=text),
        text=text,
        language=language or claim_language(text, match),
        match=match,
    )


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def api_settings(**values: Any) -> Settings:
    """Settings of an API test app: the studio follows STUDIO_ENABLED, the voice
    TTS_PROVIDER (mock unless it says off)."""
    values.setdefault("tts_provider", "mock" if TTS_ON else "off")
    return Settings(studio_enabled=STUDIO_ON, **values)


def mock_service(**settings: Any) -> ClaimService:
    """A claim service on the mock LLM; small timeouts so injected hangs end quickly."""
    values: dict[str, Any] = {"llm_timeout_s": 0.2, "claim_budget_s": 2.0}
    values.update(settings)
    return ClaimService(Settings(**values))


def verify(
    text: str,
    *,
    service: ClaimService | None = None,
    scenario: str = DL[0],
    seed: int = DL[1],
    at: str | None = None,
    language: str = "auto",
    claim_id: str = "test-c0001",
) -> ClaimResult:
    match = generated(scenario, seed)
    request = ClaimRequest(
        match_id=match.meta.match_id,
        text=text,
        language=language,  # type: ignore[arg-type]
        as_of_seq=seq_at(match.events, at),
        include_trace=True,
    )
    svc = service or mock_service()
    return run(svc.verify(request, source(scenario, seed), claim_id=claim_id))
