"""Ports of the LLM roles and the errors an adapter may raise (section 8.1; Day 3 section 13.1).

The orchestrator decides about retries and fallbacks from the error type: a timeout or a
transport error is never retried, an invalid output is retried once with feedback.
"""

from typing import Protocol

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


class LlmError(Exception):
    """The model could not be reached or failed (HTTP error, provider error, injected error)."""


class LlmTimeout(LlmError):
    """The call took longer than its timeout."""


class LlmOutputError(LlmError):
    """The model answered, but the answer is not valid for the schema or the match."""

    def __init__(self, message: str, *, errors: list[str] | None = None, raw: str = "") -> None:
        super().__init__(message)
        self.errors = errors or [message]
        self.raw = raw[:500]


class ContentFiltered(LlmError):
    """The provider's content filter blocked the prompt or the answer (Foundry content_filter)."""


class ClaimAnalystPort(Protocol):
    model: str

    async def analyze(self, ctx: ClaimContext, feedback: list[str] | None = None) -> ClaimSpec: ...


class ExplainerPort(Protocol):
    model: str

    async def explain(
        self, judged: JudgedClaim, feedback: list[GuardrailViolation] | None = None
    ) -> ExplanationDraft: ...


# --------------------------------------------------------------------------- studio (Day 3)


class TalkingPointWriterPort(Protocol):
    model: str

    async def write(
        self, pack: FactPack, feedback: list[GuardrailViolation] | None = None
    ) -> TalkingDraft: ...


class CommentatorPort(Protocol):
    model: str

    async def comment(
        self, pack: FactPack, language: Lang, feedback: list[GuardrailViolation] | None = None
    ) -> CommentaryDraft: ...


class GraphicsDesignerPort(Protocol):
    model: str

    async def choose(self, pack: FactPack, options: GraphicsOptions) -> GraphicsChoice: ...


class RecapWriterPort(Protocol):
    model: str

    async def write(
        self,
        pack: RecapPack,
        language: Lang,
        mode: Register,
        feedback: list[GuardrailViolation] | None = None,
    ) -> RecapDraft: ...
