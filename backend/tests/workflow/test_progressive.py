"""The progressive claim (docs/day7-design.md section 2.4): rev.1 from the rules never waits for
a model; the model pass runs on one worker; rev.2 is the one-pass answer, and its verdict only
changes for a claim the rules could not read."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from helpers import DL, generated, mock_service, run, source

from pundit.agents.contracts import ClaimRequest, ClaimResult
from pundit.orchestration.evaluation import load_eval, seq_at
from pundit.orchestration.progressive import (
    ModelPassQueue,
    rules_could_not_read,
    second_revision,
    skipped,
)
from pundit.orchestration.service import ClaimService

CLAIM = "Đội nhà áp đảo hiệp 2"
EVALS = Path(__file__).resolve().parents[2] / "evals"


def request(text: str = CLAIM, as_of_seq: int | None = None) -> ClaimRequest:
    return ClaimRequest(match_id=generated(*DL).meta.match_id, text=text, as_of_seq=as_of_seq)


def stable(result: ClaimResult) -> dict[str, Any]:
    """Everything but the measured times and the progressive fields."""
    return result.model_dump(
        mode="json",
        exclude={"timings_ms", "trace", "revision", "model_pass", "skip_reason", "rev1_verdict"},
    )


class Recorder:
    def __init__(self) -> None:
        self.published: list[ClaimResult] = []

    def __call__(self, result: ClaimResult) -> None:
        self.published.append(result)


async def progressive(
    service: ClaimService, req: ClaimRequest, *, claim_id: str = "p-c0001", **queue: Any
) -> tuple[ClaimResult, list[ClaimResult], ModelPassQueue]:
    passes = ModelPassQueue(service, **{"max_waiting": 4, "budget_s": 10.0, **queue})
    recorder = Recorder()
    src = source(*DL)
    rev1 = await service.verify_rules(req, src, claim_id=claim_id)
    first = passes.submit(rev1, req, src, recorder)
    recorder(first)
    while passes.waiting or passes.stats()["running"]:
        await asyncio.sleep(0.01)
    if passes._task is not None:
        await passes._task
    await passes.aclose()
    return first, recorder.published, passes


def test_rev1_comes_from_the_rules_without_calling_a_model() -> None:
    async def scenario() -> None:
        calls = 0

        def ports() -> Any:
            nonlocal calls
            calls += 1
            raise AssertionError("rev.1 asked for the model")

        service = ClaimService(mock_service().settings, ports=ports)
        rev1 = await service.verify_rules(request(), source(*DL), claim_id="r-c0001")
        assert calls == 0
        assert (rev1.verdict, rev1.analyst_source, rev1.explanation_source) == (
            "TRUE",
            "rule",
            "template",
        )
        assert "explainer_fallback" not in rev1.degraded  # the template is by design here
        assert rev1.revision == 1 and rev1.model_pass is None

    run(scenario())


def test_rev1_then_running_then_rev2_from_the_model() -> None:
    first, published, passes = run(progressive(mock_service(), request()))
    assert first.model_pass == "queued" and first.revision == 1
    states = [(r.revision, r.model_pass) for r in published]
    assert states == [(1, "queued"), (1, "running"), (2, "done")]
    rev2 = published[-1]
    assert (rev2.verdict, rev2.rev1_verdict) == ("TRUE", "TRUE")
    assert (rev2.analyst_source, rev2.explanation_source) == ("llm", "llm")
    assert rev2.timings_ms["model_pass"] >= 0
    assert rev2.as_of_seq == first.as_of_seq  # the same shared state as rev.1
    assert passes.stats()["done"] == 1


def test_rev2_is_the_one_pass_answer() -> None:
    service = mock_service()
    _, published, _ = run(progressive(service, request()))
    one_pass = run(service.verify(request(), source(*DL), claim_id="p-c0001"))
    assert stable(published[-1]) == stable(one_pass)


def test_a_full_queue_skips_the_model_pass_and_keeps_rev1() -> None:
    async def scenario() -> None:
        service = mock_service(mock_llm_latency_ms=100, llm_timeout_s=5.0)
        passes = ModelPassQueue(service, max_waiting=1, budget_s=10.0)
        recorder = Recorder()
        src = source(*DL)
        # every rev.1 first: how long the rules take must not decide what the queue sees
        rev1s = [
            await service.verify_rules(request(), src, claim_id=f"q-c{n:04d}") for n in range(3)
        ]
        answers = [passes.submit(rev1s[0], request(), src, recorder)]
        await asyncio.sleep(0)  # the first one starts running
        assert passes.stats()["running"]
        # the other two at once: one waits, the queue (max 1 waiting) is then full
        answers += [passes.submit(rev1, request(), src, recorder) for rev1 in rev1s[1:]]
        assert [a.model_pass for a in answers] == ["queued", "queued", "skipped"]
        assert answers[2].skip_reason == "busy" and answers[2].revision == 1
        while passes.waiting or passes.stats()["running"]:
            await asyncio.sleep(0.02)
        assert passes.stats()["busy"] == 1 and passes.stats()["done"] == 2
        await passes.aclose()

    run(scenario())


def test_an_open_breaker_skips_the_model_pass_at_once() -> None:
    async def scenario() -> None:
        service = mock_service()
        for _ in range(10):
            service.breaker.record_failure()
        assert service.breaker.state == "open"
        passes = ModelPassQueue(service, max_waiting=4, budget_s=10.0)
        rev1 = await service.verify_rules(request(), source(*DL), claim_id="b-c0001")
        first = passes.submit(rev1, request(), source(*DL), Recorder())
        assert (first.model_pass, first.skip_reason) == ("skipped", "circuit_open")
        assert passes.waiting == 0
        await passes.aclose()

    run(scenario())


def test_a_model_that_never_answers_leaves_rev1_with_a_reason() -> None:
    service = mock_service(mock_llm_faults="analyst:timeout:9,explainer:timeout:9")
    first, published, _ = run(progressive(service, request(), budget_s=1.0))
    rev2 = published[-1]
    assert (rev2.revision, rev2.model_pass, rev2.skip_reason) == (2, "skipped", "no_answer")
    assert stable(rev2) == stable(first.model_copy(update={"model_pass": None}))


def test_shutdown_cancels_the_waiting_passes() -> None:
    async def scenario() -> None:
        service = mock_service(mock_llm_latency_ms=2000)
        passes = ModelPassQueue(service, max_waiting=4, budget_s=10.0)
        recorder = Recorder()
        rev1 = await service.verify_rules(request(), source(*DL), claim_id="s-c0001")
        passes.submit(rev1, request(), source(*DL), recorder)
        passes.submit(rev1, request(), source(*DL), recorder)
        await asyncio.sleep(0.05)
        await passes.aclose()
        assert passes.waiting == 0 and all(r.revision == 1 for r in recorder.published)

    run(scenario())


def test_second_revision_keeps_the_rules_verdict_the_rules_had_read() -> None:
    rev1 = run(mock_service().verify_rules(request(), source(*DL), claim_id="v-c0001"))
    other = rev1.model_copy(update={"verdict": "FALSE", "analyst_source": "llm"})
    rev2 = second_revision(rev1, other, 12.0)
    assert (rev2.verdict, rev2.model_pass) == ("TRUE", "done")
    assert "analyst_disagreement" in rev2.degraded
    failed = second_revision(rev1, None, 3.0)
    assert (failed.model_pass, failed.skip_reason, failed.verdict) == (
        "skipped",
        "no_answer",
        "TRUE",
    )
    assert skipped(rev1, "busy").revision == 1


def test_a_claim_has_at_most_two_revisions_in_increasing_order() -> None:
    _, published, _ = run(progressive(mock_service(), request()))
    revisions = [r.revision for r in published]
    assert revisions == sorted(revisions) and max(revisions) == 2 and revisions.count(2) == 1


@pytest.mark.parametrize("path", sorted(EVALS.glob("*.json")), ids=lambda p: p.stem)
def test_every_eval_claim_rev2_matches_one_pass_and_the_verdict_rule(path: Path) -> None:
    """The invariants of section 2.4 on the 100 claims of the eval (mock model)."""
    evals = load_eval(path)
    match = generated(evals.match.scenario, evals.match.seed)
    src = source(evals.match.scenario, evals.match.seed)
    service = mock_service()

    async def scenario() -> None:
        for case in evals.claims:
            req = ClaimRequest(
                match_id=match.meta.match_id,
                text=case.text,
                as_of_seq=seq_at(match.events, case.at),
            )
            claim_id = f"{case.id}-c0001"
            rev1 = await service.verify_rules(req, src, claim_id=claim_id)
            one_pass = await service.verify(req, src, claim_id=claim_id)
            passes = ModelPassQueue(service, max_waiting=4, budget_s=10.0)
            recorder = Recorder()
            passes.submit(rev1, req, src, recorder)
            while passes.waiting or passes.stats()["running"]:
                await asyncio.sleep(0)
            if passes._task is not None:
                await passes._task
            rev2 = recorder.published[-1]
            assert rev2.revision == 2, case.id
            if rev2.model_pass == "done":
                assert stable(rev2) == stable(one_pass), case.id
            if rev2.verdict != rev1.verdict:
                assert rules_could_not_read(rev1), case.id

    run(scenario())
