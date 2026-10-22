"""Shared helpers of the Day 3 studio tests: cached logs, triggers, packs and offline runs."""

from functools import cache
from typing import Any

from helpers import generated, run

from pundit.agents.contracts import FactPack, StudioJob, StudioOutput, Trigger
from pundit.agents.triggers import detect_triggers
from pundit.domain.log import EventLog, LogView
from pundit.llm.settings import Settings
from pundit.orchestration.recap_service import RecapService
from pundit.orchestration.studio_eval import OfflineRun, pack_of, run_offline
from pundit.orchestration.studio_service import StudioService
from pundit.orchestration.studio_steps import StudioBudgets

SAMPLES = (("dominant_loser", 42), ("comeback_var", 7), ("red_card_swing", 7), ("balanced", 3))
MATRIX = tuple((s, n) for s, _ in SAMPLES for n in (3, 7, 42))  # 4 scenarios x 3 seeds
# small real-time budgets: an injected hang ends in half a second, yet a busy machine (a slow
# event loop) never turns an instant mock answer into a timeout
FAST = StudioBudgets(
    talking_point_s=0.8,
    commentary_s=0.8,
    graphics_s=0.6,
    job_s=1.2,
    llm_timeout_s=0.5,
    min_llm_s=0.01,
)


@cache
def full_log(scenario: str, seed: int) -> EventLog:
    match = generated(scenario, seed)
    log = EventLog(match.meta)
    for event in match.events:
        log.append(event)
    return log


def view_at(scenario: str, seed: int, seq: int) -> LogView:
    return full_log(scenario, seed).view(seq + 1)


@cache
def triggers(scenario: str, seed: int) -> tuple[Trigger, ...]:
    match = generated(scenario, seed)
    return tuple(detect_triggers(match.meta, match.events))


def first_trigger(scenario: str, seed: int, kind: str, **where: Any) -> Trigger:
    for trigger in triggers(scenario, seed):
        if trigger.kind == kind and all(getattr(trigger, k) == v for k, v in where.items()):
            return trigger
    raise LookupError(f"no {kind} {where} in {scenario}-{seed}")


@cache
def pack(scenario: str, seed: int, trigger_id: str) -> FactPack:
    trigger = next(t for t in triggers(scenario, seed) if t.id == trigger_id)
    return pack_of(trigger, view_at(scenario, seed, trigger.seq))


def studio_settings(**values: Any) -> Settings:
    return Settings(**values)


def studio_service(faults: str = "", **kwargs: Any) -> StudioService:
    settings = Settings(mock_llm_faults=faults)
    kwargs.setdefault("budgets", FAST)
    return StudioService(settings, **kwargs)


def run_job(
    service: StudioService, scenario: str, seed: int, trigger: Trigger, first_rules: tuple = ()
) -> StudioOutput:
    job = StudioJob(trigger=trigger, first_rules=first_rules)
    return run(service.run(job, view_at(scenario, seed, trigger.seq)))


@cache
def offline(scenario: str, seed: int) -> OfflineRun:
    """The whole match through the mock studio (cached: a few seconds per match)."""
    settings = Settings()
    service = StudioService(settings, offline=True)
    return run(run_offline(generated(scenario, seed), service, RecapService(settings)))


def public(output: StudioOutput) -> dict[str, Any]:
    """An output without what depends on the clock (trace, timings)."""
    data: dict[str, Any] = output.model_dump(mode="json", exclude={"trace", "timings_ms"})
    return data
