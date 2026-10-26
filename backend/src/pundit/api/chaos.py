"""Chaos of one private session (docs/day7-design.md section 2.6).

The first switch gives the session its own claim service and studio service: the same ports,
wrapped by the session's chaos, and breakers of their own. Another session, the shared match and
their breakers never see the fault. Every text the session publishes is checked against the
numbers "the model lies" changed: `lies_published` is the count shown to the judge, and must
stay 0 (the guardrail rejects every one).
"""

import logging
from collections.abc import Iterator
from typing import Any

from pundit.agents.contracts import ChaosStatus
from pundit.llm.chaos import ChaosFault, ChaosState, chaos_port
from pundit.orchestration.resilience import CircuitBreaker
from pundit.orchestration.service import ClaimService
from pundit.orchestration.studio_service import StudioService
from pundit.orchestration.studio_steps import StudioPorts

log = logging.getLogger("pundit.chaos")
SWITCHES_PER_MINUTE = 10


def _texts(value: Any) -> Iterator[str]:
    """The texts of a published item. Its trace is left out: it records the drafts the guardrail
    rejected, and it never reaches a screen (the broadcast items carry no trace)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key != "trace":
                yield from _texts(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _texts(item)


class SessionChaos:
    def __init__(self, claims: ClaimService, studio: StudioService | None) -> None:
        self.state = ChaosState()
        settings = claims.settings
        base = claims.ports_factory
        state = self.state
        self.claims = ClaimService(
            settings,
            ports=lambda: tuple(chaos_port(p, state) for p in base()),  # type: ignore[arg-type,return-value]
            breaker=CircuitBreaker(cooldown_s=settings.llm_breaker_cooldown_s),
        )
        self.studio: StudioService | None = None
        if studio is not None:
            studio_base = studio.ports_factory

            def studio_ports() -> StudioPorts:
                ports = studio_base()
                return StudioPorts(
                    talker=chaos_port(ports.talker, state),
                    commentator=chaos_port(ports.commentator, state),
                    designer=chaos_port(ports.designer, state),
                )

            self.studio = StudioService(
                studio.settings,
                ports=studio_ports,
                breaker=CircuitBreaker(cooldown_s=studio.settings.llm_breaker_cooldown_s),
                budgets=studio.budgets,
            )
        self.lies_published = 0
        self.switches: list[float] = []

    def switch(self, fault: ChaosFault | None, duration_s: float, now: float) -> None:
        self.switches = [t for t in self.switches if now - t < 60] + [now]
        self.state.switch(fault, duration_s)
        if fault is None:  # reset: the next model call is the breaker's trial
            for service in (self.claims, self.studio):
                if service is not None:
                    service.breaker.half_open_now()

    def may_switch(self, now: float) -> bool:
        return sum(1 for t in self.switches if now - t < 60) < SWITCHES_PER_MINUTE

    def check_published(self, payload: Any) -> None:
        """Count a published text that a lie changed (never, while the guardrail holds)."""
        if not self.state.lies:
            return
        key = (
            payload.get("claim_id") or payload.get("trigger_id")
            if isinstance(payload, dict)
            else None
        )
        for text in _texts(payload):
            if self.state.is_lie(text, key):
                self.lies_published += 1
                log.error("a text the chaos changed was published: %r", text[:120])

    def status(self) -> ChaosStatus:
        return ChaosStatus(
            available=True,
            fault=self.state.fault,
            seconds_left=self.state.seconds_left,
            calls=self.state.calls,
            lies_told=len(self.state.lies),
            lies_published=self.lies_published,
            claim_breaker=self.claims.breaker.state,
            studio_breaker=self.studio.breaker.state if self.studio is not None else None,
        )
