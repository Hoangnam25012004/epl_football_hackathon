"""The studio workflow on Microsoft Agent Framework (docs/day3-design.md section 14.2).

One Executor per agent, typed messages on every edge, a fan-out to three branches that run in
the same superstep and a fan-in that waits for all three:

    StudioIntake -> FactPackBuilder -+-> TalkingPointWriter --+
                                     +-> GraphicsDesigner ----+-> StudioPublisher -> StudioOutput
                                     +-> Commentator ---------+

Every branch sends exactly one BranchResult, also when it does not apply to the trigger, so the
fan-in always completes. The publisher merges the results in a fixed order, so the output does
not depend on which branch finished first. A new workflow is built for every job (MAF refuses
concurrent runs of one instance; building one takes about 2 ms).
"""

from typing import Never

from agent_framework import Executor, Workflow, WorkflowBuilder, WorkflowContext, handler

from pundit.agents.contracts import BranchResult, FactPack, StudioJob, StudioOutput, StudioPlan
from pundit.orchestration.studio_steps import StudioSteps

WORKFLOW_NAME = "pundit-studio"


class StudioIntakeExecutor(Executor):
    def __init__(self, steps: StudioSteps) -> None:
        super().__init__(id="StudioIntake")
        self.steps = steps

    @handler
    async def handle(self, job: StudioJob, ctx: WorkflowContext[StudioPlan]) -> None:
        await ctx.send_message(self.steps.intake(job))


class FactPackBuilderExecutor(Executor):
    def __init__(self, steps: StudioSteps) -> None:
        super().__init__(id="FactPackBuilder")
        self.steps = steps

    @handler
    async def handle(self, plan: StudioPlan, ctx: WorkflowContext[FactPack]) -> None:
        await ctx.send_message(self.steps.build_pack(plan))


class TalkingPointExecutor(Executor):
    def __init__(self, steps: StudioSteps) -> None:
        super().__init__(id="TalkingPointWriter")
        self.steps = steps

    @handler
    async def handle(self, pack: FactPack, ctx: WorkflowContext[BranchResult]) -> None:
        await ctx.send_message(await self.steps.talking_point(pack))


class GraphicsExecutor(Executor):
    def __init__(self, steps: StudioSteps) -> None:
        super().__init__(id="GraphicsDesigner")
        self.steps = steps

    @handler
    async def handle(self, pack: FactPack, ctx: WorkflowContext[BranchResult]) -> None:
        await ctx.send_message(await self.steps.lower_third(pack))


class CommentatorExecutor(Executor):
    def __init__(self, steps: StudioSteps) -> None:
        super().__init__(id="Commentator")
        self.steps = steps

    @handler
    async def handle(self, pack: FactPack, ctx: WorkflowContext[BranchResult]) -> None:
        await ctx.send_message(await self.steps.commentary(pack))


class StudioPublisherExecutor(Executor):
    def __init__(self, steps: StudioSteps) -> None:
        super().__init__(id="StudioPublisher")
        self.steps = steps

    @handler
    async def handle(
        self, results: list[BranchResult], ctx: WorkflowContext[Never, StudioOutput]
    ) -> None:
        await ctx.yield_output(self.steps.publish(results))


def build_studio_workflow(steps: StudioSteps) -> Workflow:
    intake = StudioIntakeExecutor(steps)
    builder = FactPackBuilderExecutor(steps)
    branches: list[Executor] = [
        TalkingPointExecutor(steps),
        GraphicsExecutor(steps),
        CommentatorExecutor(steps),
    ]
    publisher = StudioPublisherExecutor(steps)
    return (
        WorkflowBuilder(
            max_iterations=8,
            name=WORKFLOW_NAME,
            description="Talking points, lower-thirds and commentary for one match moment",
            start_executor=intake,
        )
        .add_edge(intake, builder)
        .add_fan_out_edges(builder, branches)
        .add_fan_in_edges(branches, publisher)
        .build()
    )
