"""The claim verification workflow on Microsoft Agent Framework (design section 9.1).

One Executor per agent, typed messages on every edge, conditional edges for every fallback:

    ClaimIntake -> ClaimAnalyst -> RuleAnalyst -> EvidenceCollector -> Judge -> Explainer
         |                                                         |          |   ^
         |                                                         v          v   | retry
         +-------------(as_of out of range)--------------> TemplateExplainer -> Guardrail
                                                                                  |
                                                                  ClaimResult <- Publisher

The retry loop Guardrail -> Explainer is bounded by `attempt` inside the message. A new
workflow is built for every claim (MAF refuses concurrent runs of one instance; building one
takes about 2 ms), so executors may keep per-claim state.
"""

from collections.abc import Callable
from typing import Never

from agent_framework import Executor, Workflow, WorkflowBuilder, WorkflowContext, handler

from pundit.agents.contracts import (
    AnalystOutcome,
    ClaimAnalysis,
    ClaimContext,
    ClaimRequest,
    ClaimResult,
    DraftForReview,
    EvidenceBundle,
    ExplainRequest,
    Publication,
    TemplateRequest,
)
from pundit.orchestration.steps import ClaimSteps

WORKFLOW_NAME = "pundit-claim-verifier"


class ClaimIntakeExecutor(Executor):
    def __init__(self, steps: ClaimSteps) -> None:
        super().__init__(id="ClaimIntake")
        self.steps = steps

    @handler
    async def handle(
        self, request: ClaimRequest, ctx: WorkflowContext[ClaimContext | TemplateRequest]
    ) -> None:
        await ctx.send_message(self.steps.intake(request))


class ClaimAnalystExecutor(Executor):
    def __init__(self, steps: ClaimSteps) -> None:
        super().__init__(id="ClaimAnalyst")
        self.steps = steps

    @handler
    async def handle(self, claim: ClaimContext, ctx: WorkflowContext[AnalystOutcome]) -> None:
        await ctx.send_message(await self.steps.analyze(claim))


class RuleAnalystExecutor(Executor):
    def __init__(self, steps: ClaimSteps) -> None:
        super().__init__(id="RuleAnalyst")
        self.steps = steps

    @handler
    async def handle(self, outcome: AnalystOutcome, ctx: WorkflowContext[ClaimAnalysis]) -> None:
        await ctx.send_message(self.steps.reconcile(outcome))


class EvidenceCollectorExecutor(Executor):
    def __init__(self, steps: ClaimSteps) -> None:
        super().__init__(id="EvidenceCollector")
        self.steps = steps

    @handler
    async def handle(self, analysis: ClaimAnalysis, ctx: WorkflowContext[EvidenceBundle]) -> None:
        await ctx.send_message(self.steps.collect(analysis))


class JudgeExecutor(Executor):
    def __init__(self, steps: ClaimSteps) -> None:
        super().__init__(id="Judge")
        self.steps = steps

    @handler
    async def handle(
        self, bundle: EvidenceBundle, ctx: WorkflowContext[ExplainRequest | TemplateRequest]
    ) -> None:
        await ctx.send_message(self.steps.judge(bundle))


class ExplainerExecutor(Executor):
    def __init__(self, steps: ClaimSteps) -> None:
        super().__init__(id="Explainer")
        self.steps = steps

    @handler
    async def handle(
        self, request: ExplainRequest, ctx: WorkflowContext[DraftForReview | TemplateRequest]
    ) -> None:
        await ctx.send_message(await self.steps.explain(request))


class TemplateExplainerExecutor(Executor):
    def __init__(self, steps: ClaimSteps) -> None:
        super().__init__(id="TemplateExplainer")
        self.steps = steps

    @handler
    async def handle(self, request: TemplateRequest, ctx: WorkflowContext[DraftForReview]) -> None:
        await ctx.send_message(self.steps.template(request))


class GuardrailExecutor(Executor):
    def __init__(self, steps: ClaimSteps) -> None:
        super().__init__(id="Guardrail")
        self.steps = steps

    @handler
    async def handle(
        self,
        item: DraftForReview,
        ctx: WorkflowContext[Publication | ExplainRequest | TemplateRequest],
    ) -> None:
        await ctx.send_message(self.steps.review(item))


class PublisherExecutor(Executor):
    def __init__(self, steps: ClaimSteps) -> None:
        super().__init__(id="Publisher")
        self.steps = steps

    @handler
    async def handle(self, item: Publication, ctx: WorkflowContext[Never, ClaimResult]) -> None:
        await ctx.yield_output(self.steps.publish(item))


def _is(kind: type) -> Callable[[object], bool]:
    return lambda message: isinstance(message, kind)


def build_claim_workflow(steps: ClaimSteps) -> Workflow:
    intake = ClaimIntakeExecutor(steps)
    analyst = ClaimAnalystExecutor(steps)
    rules = RuleAnalystExecutor(steps)
    collector = EvidenceCollectorExecutor(steps)
    judge = JudgeExecutor(steps)
    explainer = ExplainerExecutor(steps)
    template = TemplateExplainerExecutor(steps)
    guardrail = GuardrailExecutor(steps)
    publisher = PublisherExecutor(steps)
    return (
        WorkflowBuilder(
            max_iterations=32,
            name=WORKFLOW_NAME,
            description="Verify a commentator's claim against deterministic match statistics",
            start_executor=intake,
        )
        .add_edge(intake, analyst, condition=_is(ClaimContext))
        .add_edge(intake, template, condition=_is(TemplateRequest))
        .add_edge(analyst, rules)
        .add_edge(rules, collector)
        .add_edge(collector, judge)
        .add_edge(judge, explainer, condition=_is(ExplainRequest))
        .add_edge(judge, template, condition=_is(TemplateRequest))
        .add_edge(explainer, guardrail, condition=_is(DraftForReview))
        .add_edge(explainer, template, condition=_is(TemplateRequest))
        .add_edge(template, guardrail)
        .add_edge(guardrail, publisher, condition=_is(Publication))
        .add_edge(guardrail, explainer, condition=_is(ExplainRequest))
        .add_edge(guardrail, template, condition=_is(TemplateRequest))
        .build()
    )
