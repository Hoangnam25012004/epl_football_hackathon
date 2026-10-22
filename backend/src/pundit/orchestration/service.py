"""ClaimService: ClaimRequest -> ClaimResult (design sections 9.3 and 11).

Pins the view at `as_of_seq`, builds one workflow run, and never lets an LLM problem escape:
every failure ends in a deterministic answer with a `degraded` flag. If the workflow engine
itself fails, the same steps run in a plain asyncio loop (`orchestrator_fallback`).
"""

import json
import logging
import time
from collections.abc import Callable, Sequence
from typing import Protocol

from pundit.agents.contracts import ClaimRequest, ClaimResult, Degraded
from pundit.agents.trace import TraceRecorder
from pundit.domain.events import Event
from pundit.domain.log import EventLog, LogView
from pundit.domain.meta import MatchMeta
from pundit.llm.faults import FaultPlan
from pundit.llm.mock import MockClaimAnalyst, MockExplainer
from pundit.llm.ports import ClaimAnalystPort, ExplainerPort
from pundit.llm.settings import Settings
from pundit.orchestration.claim_workflow import build_claim_workflow
from pundit.orchestration.resilience import Budget, CircuitBreaker, Clock
from pundit.orchestration.steps import ClaimRun, ClaimSteps, run_pipeline

log = logging.getLogger("pundit.claims")

Ports = tuple[ClaimAnalystPort | None, ExplainerPort | None]
PortsFactory = Callable[[], Ports]


class MatchSource(Protocol):
    """Read access to a match that may still be growing (a live session) or not (a file)."""

    @property
    def meta(self) -> MatchMeta: ...

    def latest_seq(self) -> int: ...

    def view_at(self, seq: int) -> LogView: ...


class StaticSource:
    """A finished match (generated or read from a file)."""

    def __init__(self, meta: MatchMeta, events: Sequence[Event]) -> None:
        self._log = EventLog(meta)
        for event in events:
            self._log.append(event)

    @property
    def meta(self) -> MatchMeta:
        return self._log.meta

    def latest_seq(self) -> int:
        return len(self._log) - 1

    def view_at(self, seq: int) -> LogView:
        return self._log.view(seq + 1)


def mock_ports(settings: Settings) -> PortsFactory:
    """Fresh mock ports per claim, each with its own fault counter."""
    plan = FaultPlan.parse(settings.mock_llm_faults)
    latency = settings.mock_llm_latency_ms / 1000

    def make() -> Ports:
        counter = plan.counter()
        return MockClaimAnalyst(counter, latency_s=latency), MockExplainer(
            counter, latency_s=latency
        )

    return make


def llm_ports(settings: Settings) -> PortsFactory:
    """Real-model ports (Ollama, OpenAI-compatible or Foundry), shared by every claim."""
    from pundit.llm.maf_backend import LlmClaimAnalyst, LlmExplainer, build_backend

    backend = build_backend(settings)
    analyst, explainer = LlmClaimAnalyst(backend), LlmExplainer(backend)
    return lambda: (analyst, explainer)


def default_ports(settings: Settings) -> PortsFactory:
    return mock_ports(settings) if settings.provider == "mock" else llm_ports(settings)


class ClaimService:
    def __init__(
        self,
        settings: Settings,
        *,
        ports: PortsFactory | None = None,
        breaker: CircuitBreaker | None = None,
        clock: Clock = time.monotonic,
        trace_clock: Clock = time.perf_counter,
        use_workflow: bool = True,
    ) -> None:
        self.settings = settings
        self._ports = ports or default_ports(settings)
        self.breaker = breaker or CircuitBreaker(clock=clock)
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
        request: ClaimRequest,
        source: MatchSource,
        claim_id: str,
        *,
        deterministic: bool,
        degraded: Sequence[Degraded] = (),
    ) -> ClaimRun:
        latest = source.latest_seq()
        seq = latest if request.as_of_seq is None else request.as_of_seq
        analyst, explainer = (None, None) if deterministic else self._ports()
        return ClaimRun(
            claim_id=claim_id,
            request=request,
            view=source.view_at(min(seq, latest)),
            out_of_range=seq > latest,
            analyst=analyst,
            explainer=explainer,
            breaker=self.breaker,
            budget=Budget(self.settings.claim_budget_s, clock=self._clock),
            llm_timeout_s=self.settings.llm_timeout_s,
            trace=TraceRecorder(claim_id, clock=self._trace_clock),
            model=self.model,
            degraded=list(degraded),
        )

    async def verify(
        self, request: ClaimRequest, source: MatchSource, *, claim_id: str
    ) -> ClaimResult:
        """Always returns a result (with its trace); LLM failures only add `degraded` flags."""
        run = self._run(request, source, claim_id, deterministic=False)
        steps = ClaimSteps(run)
        try:
            if self._use_workflow:
                outputs = (await build_claim_workflow(steps).run(request)).get_outputs()
                if len(outputs) != 1 or not isinstance(outputs[0], ClaimResult):
                    raise RuntimeError(f"workflow produced {len(outputs)} outputs")
                result: ClaimResult = outputs[0]
            else:
                result = await run_pipeline(steps, request)
        except Exception:
            log.exception("claim workflow failed; answering with the deterministic pipeline")
            fallback = self._run(
                request, source, claim_id, deterministic=True, degraded=["orchestrator_fallback"]
            )
            result = await run_pipeline(ClaimSteps(fallback), request)
        log.info(
            json.dumps(
                {
                    "event": "claim_verified",
                    "claim_id": result.claim_id,
                    "match_id": result.match_id,
                    "as_of_seq": result.as_of_seq,
                    "verdict": result.verdict,
                    "reason_code": result.reason_code,
                    "degraded": list(result.degraded),
                    "explanation_source": result.explanation_source,
                    "total_ms": result.timings_ms.get("total"),
                }
            )
        )
        return result
