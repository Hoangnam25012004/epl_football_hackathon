"""Chaos (docs/day7-design.md section 2.6): a judge breaks the model of one private session.

`ChaosState` holds the fault the session's owner switched on (one at a time, off after its
duration). `chaos_port` wraps any model port (claim analyst, explainer, studio writers) and
plays that fault on every call, with the same typed errors a real adapter raises, so the
orchestrator answers it the way it answers a real failure: timeouts, retries with feedback,
template fallbacks, the breaker. "The model lies" lets the real call answer, then changes one
number of its text: the guardrail must reject it. Every text it changed is remembered, so the
session can prove that none of them reached a screen.
"""

import asyncio
import re
import time
from collections.abc import Callable
from typing import Any, get_args

from pydantic import BaseModel

from pundit.agents.contracts import ChaosFaultName
from pundit.llm.ports import ContentFiltered, LlmError, LlmOutputError

ChaosFault = ChaosFaultName
FAULTS: tuple[ChaosFault, ...] = get_args(ChaosFault)
MAX_DURATION_S = 300.0
RETRY_AFTER_S = 30.0
SLOW_S = 3600.0  # longer than any budget: the step's own timeout ends the call
NUMBER = re.compile(r"\d+")
CALLS = frozenset({"analyze", "explain", "write", "comment", "choose"})
WRITERS = frozenset({"explain", "write", "comment"})  # calls whose text can carry a number


class ChaosState:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._fault: ChaosFault | None = None
        self._until = 0.0
        self._retry_until = 0.0
        self.calls = 0
        # every text "the model lies" changed, with the claim or trigger it was written for
        self.lies: list[tuple[str | None, str]] = []

    def switch(self, fault: ChaosFault | None, duration_s: float = MAX_DURATION_S) -> None:
        self._fault = fault
        self._until = self._clock() + min(duration_s, MAX_DURATION_S) if fault else 0.0
        self._retry_until = 0.0

    @property
    def fault(self) -> ChaosFault | None:
        if self._fault is not None and self._clock() >= self._until:
            self._fault = None  # off by itself after its duration
        return self._fault

    @property
    def seconds_left(self) -> float | None:
        return round(self._until - self._clock(), 1) if self.fault is not None else None

    def is_lie(self, text: str, key: str | None = None) -> bool:
        """`text` is a lie told for `key` (a claim id, a trigger id; None: for anything).
        The same words can be true later: "6 corners" lied at the third corner is the truth
        at the sixth."""
        return bool(text) and any(
            told == text and (key is None or k is None or k == key) for k, told in self.lies
        )

    async def call(self, method: str, real: Callable[[], Any], key: str | None = None) -> Any:
        fault = self.fault
        self.calls += fault is not None
        if fault is None:
            return await real()
        if fault == "model_down":
            raise LlmError("connection refused (chaos: the model is down)")
        if fault == "model_slow":
            await asyncio.sleep(SLOW_S)
        if fault == "model_garbage":
            raise LlmOutputError("invalid JSON (chaos)", raw='{"spec": [oops')
        if fault == "rate_limited":
            now = self._clock()
            if now >= self._retry_until:
                self._retry_until = now + RETRY_AFTER_S
            left = self._retry_until - now
            raise LlmError(f"429 too many requests (chaos), retry after {left:.0f} s")
        if fault == "content_filter":
            raise ContentFiltered("content_filter (chaos)")
        answer = await real()
        if method not in WRITERS or not isinstance(answer, BaseModel):
            return answer  # the reading of the claim is left alone: the rules check it anyway
        lied, changed = _change_a_number(answer.model_dump(mode="json"))
        if not changed:
            return answer
        self.lies.append((key, changed))
        return type(answer).model_validate(lied)


def _change_a_number(data: Any) -> tuple[Any, str | None]:
    """The first number of the first text with one, made wrong (+3); the new text, or None."""
    if isinstance(data, str):
        match = NUMBER.search(data)
        if match is None:
            return data, None
        wrong = str(int(match.group()) + 3)
        text = data[: match.start()] + wrong + data[match.end() :]
        return text, text
    if isinstance(data, list):
        for index, item in enumerate(data):
            new, changed = _change_a_number(item)
            if changed:
                return [*data[:index], new, *data[index + 1 :]], changed
        return data, None
    if isinstance(data, dict):
        for key, value in data.items():
            if key in ("source", "language", "attempt", "template"):
                continue
            new, changed = _change_a_number(value)
            if changed:
                return {**data, key: new}, changed
    return data, None


def _key(subject: Any) -> str | None:
    """What a call writes for: the claim (JudgedClaim, ClaimContext) or the trigger (FactPack)."""
    claim_id = getattr(subject, "claim_id", None)
    if isinstance(claim_id, str):
        return claim_id
    trigger_id = getattr(getattr(subject, "trigger", None), "id", None)
    return trigger_id if isinstance(trigger_id, str) else None


class ChaosPort:
    """A model port whose calls go through the session's chaos first."""

    def __init__(self, inner: Any, state: ChaosState) -> None:
        self._inner = inner
        self._state = state
        self.model = getattr(inner, "model", "model")

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if name not in CALLS or not callable(attribute):
            return attribute

        async def call(*args: Any, **kwargs: Any) -> Any:
            key = _key(args[0]) if args else None
            return await self._state.call(name, lambda: attribute(*args, **kwargs), key)

        return call


def chaos_port[T](inner: T | None, state: ChaosState) -> T | None:
    return None if inner is None else ChaosPort(inner, state)  # type: ignore[return-value]
