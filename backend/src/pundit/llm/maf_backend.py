"""Real LLM adapter (docs/day2-design.md section 8.3; Day 3 section 13.5): Microsoft Foundry, a
local Ollama model or any OpenAI-compatible endpoint, through Microsoft Agent Framework's
OpenAI-compatible chat client (Chat Completions + structured output).

Every provider speaks the same API; only the base URL and the credential change. The HTTP client
never retries (`max_retries=0`): retries are decided by orchestration/resilience.py, so a slow
provider cannot multiply the attempts.
"""

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx
from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from pundit.agents.contracts import (
    ClaimContext,
    ClaimSpec,
    CommentaryDraft,
    ExplanationDraft,
    FactPack,
    GraphicsChoice,
    GraphicsOptions,
    GuardrailViolation,
    JudgedClaim,
    Lang,
    RecapDraft,
    Register,
    TalkingDraft,
)
from pundit.agents.recap import RecapPack
from pundit.llm.dto import (
    LlmClaimSpec,
    LlmCommentary,
    LlmExplanation,
    LlmGraphicsChoice,
    LlmRecap,
    LlmTalkingPoint,
    dto_to_spec,
    to_commentary_draft,
    to_graphics_choice,
    to_recap_draft,
    to_talking_draft,
)
from pundit.llm.ports import ContentFiltered, LlmError, LlmOutputError
from pundit.llm.prompts import analyst_messages, explainer_messages
from pundit.llm.settings import ConfigError, Settings
from pundit.llm.studio_prompts import (
    commentary_messages,
    graphics_messages,
    recap_messages,
    talking_messages,
)

T = TypeVar("T", bound=BaseModel)
FOUNDRY_SCOPE = "https://cognitiveservices.azure.com/.default"
_FILTER_MARKERS = ("content_filter", "content management policy", "responsibleaipolicyviolation")


def _content_filtered(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = f"{type(current).__name__} {current}".lower()
        if any(marker in text for marker in _FILTER_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


class MafChatBackend:
    """One structured-output call per request, through a fresh MAF Agent."""

    def __init__(self, client: OpenAIChatCompletionClient, *, model: str) -> None:
        self._client = client
        self.model = model

    async def structured(self, *, system: str, user: str, schema: type[T]) -> T:
        agent = Agent(client=self._client, instructions=system)
        try:
            response = await agent.run(user, options={"response_format": schema, "temperature": 0})
        except Exception as exc:
            if _content_filtered(exc):
                raise ContentFiltered("the provider's content filter blocked the request") from exc
            raise LlmError(f"{type(exc).__name__}: {str(exc)[:300]}") from exc
        if str(getattr(response, "finish_reason", "") or "").lower() == "content_filter":
            raise ContentFiltered("the provider's content filter blocked the answer")
        try:
            value = response.value
        except (ValidationError, ValueError) as exc:
            value = None
            text = _strip_fences(response.text)
            try:
                value = schema.model_validate_json(text)
            except ValidationError:
                raise LlmOutputError(
                    "the answer does not match the schema", errors=[str(exc)[:500]], raw=text
                ) from exc
        if value is None:
            raise LlmOutputError("the model returned no content", raw=response.text)
        if not isinstance(value, schema):
            raise LlmOutputError("unexpected structured value", raw=response.text)
        return value


class LlmClaimAnalyst:
    """ClaimAnalystPort backed by a real model."""

    def __init__(self, backend: MafChatBackend) -> None:
        self._backend = backend
        self.model = backend.model

    async def analyze(self, ctx: ClaimContext, feedback: list[str] | None = None) -> ClaimSpec:
        system, user = analyst_messages(ctx, feedback)
        dto = await self._backend.structured(system=system, user=user, schema=LlmClaimSpec)
        return dto_to_spec(dto, ctx)


class LlmExplainer:
    """ExplainerPort backed by a real model."""

    def __init__(self, backend: MafChatBackend) -> None:
        self._backend = backend
        self.model = backend.model

    async def explain(
        self, judged: JudgedClaim, feedback: list[GuardrailViolation] | None = None
    ) -> ExplanationDraft:
        system, user = explainer_messages(judged, feedback)
        out = await self._backend.structured(system=system, user=user, schema=LlmExplanation)
        return ExplanationDraft(
            text=out.text.strip(), language=judged.language, source="llm", attempt=1
        )


class LlmTalkingPointWriter:
    """TalkingPointWriterPort backed by a real model."""

    def __init__(self, backend: MafChatBackend) -> None:
        self._backend = backend
        self.model = backend.model

    async def write(
        self, pack: FactPack, feedback: list[GuardrailViolation] | None = None
    ) -> TalkingDraft:
        system, user = talking_messages(pack, feedback)
        dto = await self._backend.structured(system=system, user=user, schema=LlmTalkingPoint)
        return to_talking_draft(dto)


class LlmCommentator:
    """CommentatorPort backed by a real model."""

    def __init__(self, backend: MafChatBackend) -> None:
        self._backend = backend
        self.model = backend.model

    async def comment(
        self, pack: FactPack, language: Lang, feedback: list[GuardrailViolation] | None = None
    ) -> CommentaryDraft:
        system, user = commentary_messages(pack, language, feedback)
        dto = await self._backend.structured(system=system, user=user, schema=LlmCommentary)
        return to_commentary_draft(dto)


class LlmGraphicsDesigner:
    """GraphicsDesignerPort backed by a real model."""

    def __init__(self, backend: MafChatBackend) -> None:
        self._backend = backend
        self.model = backend.model

    async def choose(self, pack: FactPack, options: GraphicsOptions) -> GraphicsChoice:
        system, user = graphics_messages(pack, options)
        dto = await self._backend.structured(system=system, user=user, schema=LlmGraphicsChoice)
        return to_graphics_choice(dto)


class LlmRecapWriter:
    """RecapWriterPort backed by a real model."""

    def __init__(self, backend: MafChatBackend) -> None:
        self._backend = backend
        self.model = backend.model

    async def write(
        self,
        pack: RecapPack,
        language: Lang,
        mode: Register,
        feedback: list[GuardrailViolation] | None = None,
    ) -> RecapDraft:
        system, user = recap_messages(pack, language, mode, feedback)
        dto = await self._backend.structured(system=system, user=user, schema=LlmRecap)
        return to_recap_draft(dto)


def foundry_base_url(endpoint: str) -> str:
    """`https://<resource>.openai.azure.com` -> `https://<resource>.openai.azure.com/openai/v1/`."""
    base = endpoint.rstrip("/")
    if not base.endswith("/openai/v1"):
        base += "/openai/v1"
    return base + "/"


def _entra_token_provider() -> Callable[[], Awaitable[str]]:
    try:
        from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
    except ImportError as exc:  # optional dependency, added when Foundry is attached
        raise ConfigError(
            "Foundry without FOUNDRY_API_KEY uses Entra ID: run `uv add azure-identity` "
            "and `az login` (or use a managed identity)"
        ) from exc
    provider: Callable[[], Awaitable[str]] = get_bearer_token_provider(
        DefaultAzureCredential(), FOUNDRY_SCOPE
    )
    return provider


def build_backend(
    settings: Settings, *, http_client: httpx.AsyncClient | None = None
) -> MafChatBackend:
    """The backend of the configured provider (never called in mock mode)."""
    timeout = settings.llm_timeout_s + 1.0
    extra: dict[str, Any] = {"http_client": http_client} if http_client is not None else {}
    api_key: str | Callable[[], Awaitable[str]]
    if settings.provider in ("ollama", "openai"):
        assert settings.base_url is not None
        base_url = settings.base_url
        # Ollama and most local servers ignore the key, but the SDK needs a non-empty one.
        api_key = (
            settings.llm_api_key.get_secret_value()
            if settings.llm_api_key is not None
            else settings.provider
        )
    elif settings.provider == "foundry":
        assert settings.foundry_endpoint is not None
        base_url = foundry_base_url(settings.foundry_endpoint)
        api_key = (
            settings.foundry_api_key.get_secret_value()
            if settings.foundry_api_key is not None
            else _entra_token_provider()
        )
    else:
        raise ConfigError("build_backend needs LLM_PROVIDER=ollama, openai or foundry")
    oai = AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=0, timeout=timeout, **extra)
    model = settings.model_name
    client = OpenAIChatCompletionClient(model=model, async_client=oai)
    return MafChatBackend(client, model=model)
