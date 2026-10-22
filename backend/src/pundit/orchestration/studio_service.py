"""StudioService: StudioJob -> StudioOutput (docs/day3-design.md section 14.1).

Runs one MAF workflow per job on the view pinned at the trigger's seq, and never lets an LLM
problem escape: every failure ends in a template with a `degraded` flag. If the workflow engine
itself fails, the same steps run in a plain asyncio program, template only
(`orchestrator_fallback`). The studio has its own circuit breaker: its short real-time budgets
must never open the breaker of the claims.
"""

import json
import logging
import time
from collections.abc import Callable

from pundit.agents.contracts import Degraded, StudioJob, StudioOutput
from pundit.agents.trace import TraceRecorder
from pundit.domain.log import LogView
from pundit.llm.faults import FaultPlan
from pundit.llm.mock import MockCommentator, MockGraphicsDesigner, MockTalkingPointWriter
from pundit.llm.settings import Settings
from pundit.orchestration.resilience import CircuitBreaker, Clock
from pundit.orchestration.studio_steps import (
    StudioBudgets,
    StudioPorts,
    StudioRun,
    StudioSteps,
    run_studio_pipeline,
)
from pundit.orchestration.studio_workflow import build_studio_workflow

log = logging.getLogger("pundit.studio")

StudioPortsFactory = Callable[[], StudioPorts]


def studio_mock_ports(settings: Settings) -> StudioPortsFactory:
    """Fresh mock writers per job, sharing one fault counter (faults restart at every job)."""
    plan = FaultPlan.parse(settings.mock_llm_faults)
    latency = settings.mock_llm_latency_ms / 1000

    def make() -> StudioPorts:
        counter = plan.counter()
        return StudioPorts(
            talker=MockTalkingPointWriter(counter, latency_s=latency),
            commentator=MockCommentator(counter, latency_s=latency),
            designer=MockGraphicsDesigner(counter, latency_s=latency),
        )

    return make


def studio_llm_ports(settings: Settings, *, timeout_s: float | None = None) -> StudioPortsFactory:
    """Real-model writers (Ollama, OpenAI-compatible or Foundry), shared by every job."""
    from pundit.llm.maf_backend import (
        LlmCommentator,
        LlmGraphicsDesigner,
        LlmTalkingPointWriter,
        build_backend,
    )

    backend = build_backend(settings, timeout_s=timeout_s)
    ports = StudioPorts(
        talker=LlmTalkingPointWriter(backend),
        commentator=LlmCommentator(backend),
        designer=LlmGraphicsDesigner(backend),
    )
    return lambda: ports


def default_studio_ports(
    settings: Settings, *, timeout_s: float | None = None
) -> StudioPortsFactory:
    if settings.provider == "mock":
        return studio_mock_ports(settings)
    return studio_llm_ports(settings, timeout_s=timeout_s)


class StudioService:
    def __init__(
        self,
        settings: Settings,
        *,
        ports: StudioPortsFactory | None = None,
        breaker: CircuitBreaker | None = None,
        budgets: StudioBudgets | None = None,
        llm_min_importance: int | None = None,
        offline: bool = False,
        clock: Clock = time.monotonic,
        trace_clock: Clock = time.perf_counter,
        use_workflow: bool = True,
    ) -> None:
        self.settings = settings
        self.budgets = budgets or (
            StudioBudgets.offline(settings) if offline else StudioBudgets.live(settings)
        )
        # offline, a call may wait for the other requests of its job: the HTTP client must not
        # give up before the call budget does
        self._ports = ports or default_studio_ports(settings, timeout_s=self.budgets.llm_timeout_s)
        self.breaker = breaker or CircuitBreaker(clock=clock)
        self.llm_min_importance = llm_min_importance or settings.studio_llm_min_importance
        self._clock = clock
        self._trace_clock = trace_clock
        self._use_workflow = use_workflow

    @property
    def provider(self) -> str:
        return self.settings.provider

    @property
    def model(self) -> str:
        return self.settings.model_name

    def degraded_now(self) -> list[Degraded]:
        return ["circuit_open"] if self.breaker.state == "open" else []

    def _run(
        self,
        job: StudioJob,
        view: LogView,
        *,
        deterministic: bool,
        degraded: tuple[Degraded, ...] = (),
    ) -> StudioRun:
        return StudioRun(
            job=job,
            view=view,
            ports=StudioPorts() if deterministic else self._ports(),
            breaker=self.breaker,
            budgets=self.budgets,
            trace=TraceRecorder(job.trigger.id, clock=self._trace_clock),
            model=self.model,
            clock=self._clock,
            llm_min_importance=self.llm_min_importance,
            degraded=list(degraded),
        )

    async def run(self, job: StudioJob, view: LogView) -> StudioOutput:
        """Always returns an output (with its trace); LLM failures only add `degraded` flags."""
        trigger = job.trigger
        if view.last_seq != trigger.seq:
            raise ValueError(
                f"the view ends at seq {view.last_seq}; trigger {trigger.id} is at seq "
                f"{trigger.seq} (pin the view with view_at(trigger.seq))"
            )
        steps = StudioSteps(self._run(job, view, deterministic=False))
        try:
            if self._use_workflow:
                outputs = (await build_studio_workflow(steps).run(job)).get_outputs()
                if len(outputs) != 1 or not isinstance(outputs[0], StudioOutput):
                    raise RuntimeError(f"studio workflow produced {len(outputs)} outputs")
                output: StudioOutput = outputs[0]
            else:
                output = await run_studio_pipeline(steps, job)
        except Exception:
            log.exception("studio workflow failed; answering with the templates")
            fallback = self._run(job, view, deterministic=True, degraded=("orchestrator_fallback",))
            output = await run_studio_pipeline(StudioSteps(fallback), job)
        if log.isEnabledFor(logging.DEBUG):
            log.debug(
                json.dumps(
                    {
                        "event": "studio_job",
                        "trigger_id": trigger.id,
                        "kind": trigger.kind,
                        "talking_point": output.talking_point is not None,
                        "lower_third": output.lower_third is not None,
                        "degraded": list(output.degraded),
                        "total_ms": output.timings_ms.get("total"),
                    }
                )
            )
        return output
