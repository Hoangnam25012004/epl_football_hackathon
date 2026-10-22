"""Studio workflow (docs/day3-design.md sections 14 and 16.4): MAF fan-out/fan-in equals the
asyncio runner, determinism, the 10 x 10 fault matrix, the studio breaker, fallbacks, recaps."""

import time
from typing import Any

import pytest
from helpers import mock_service, run
from studio_helpers import (
    FAST,
    first_trigger,
    offline,
    public,
    run_job,
    studio_service,
    triggers,
    view_at,
)

from pundit.agents.contracts import StudioJob, StudioOutput, Trigger
from pundit.agents.studio_guard import check_studio, scope_of
from pundit.agents.talking_points import wants_talking_point
from pundit.engine.replay import FakeClock
from pundit.llm.settings import Settings
from pundit.orchestration import studio_service as studio_module
from pundit.orchestration.recap_service import RecapService
from pundit.orchestration.resilience import CircuitBreaker
from pundit.orchestration.studio_eval import pack_of
from pundit.orchestration.studio_service import StudioService
from pundit.orchestration.studio_steps import StudioBudgets

DL = ("dominant_loser", 42)
KINDS = (
    "ok",
    "timeout",
    "error",
    "invalid_json",
    "hallucinate:1",
    "hallucinate:2",
    "unsafe",
    "wrong_player",
    "wrong_outcome",
    "wrong_language",
)
DRAFT_FAULTS = {"hallucinate:1", "hallucinate:2", "unsafe", "wrong_player", "wrong_outcome",
                "wrong_language"}  # fmt: skip


def goal_trigger() -> Trigger:
    return first_trigger(*DL, "goal")


def shot_trigger() -> Trigger:
    return first_trigger(*DL, "shot", outcome="saved")


def published_texts_pass(output: StudioOutput, trigger: Trigger) -> None:
    """No rejected text leaves the studio: every published text passes a fresh check."""
    built = pack_of(trigger, view_at(*DL, trigger.seq))
    scope = scope_of(built)
    for text in output.commentary.texts:
        report = check_studio(
            text.text,
            scope,
            language=text.language,
            product="commentary",
            mode=text.mode,
            model_text=text.source == "llm",
        )
        assert report.passed, (text, report.violations)
    tp = output.talking_point
    if tp is not None:
        for language, lines, source in (("en", tp.en, tp.source_en), ("vi", tp.vi, tp.source_vi)):
            for line in lines:
                report = check_studio(
                    line.text,
                    scope,
                    language=language,
                    product="talking_point",
                    model_text=source == "llm",
                )
                assert report.passed, (line, report.violations)


def spec(commentator: str, talker: str) -> str:
    parts = [f"commentator:{commentator}" if commentator != "ok" else ""]
    parts.append(f"talker:{talker}" if talker != "ok" else "")
    return ",".join(p for p in parts if p)


_RUNS: dict[tuple[str, str, str], tuple[StudioOutput, float]] = {}


def timed_job(trigger: Trigger, commentator: str, talker: str) -> tuple[StudioOutput, float]:
    """One job with the faults (each combination runs once; seconds it took)."""
    key = (trigger.id, commentator, talker)
    if key not in _RUNS:
        started = time.perf_counter()
        output = run_job(studio_service(spec(commentator, talker)), *DL, trigger)
        _RUNS[key] = (output, time.perf_counter() - started)
    return _RUNS[key]


def job(trigger: Trigger, commentator: str, talker: str) -> StudioOutput:
    return timed_job(trigger, commentator, talker)[0]


# --------------------------------------------------------------------------- the normal path


def test_maf_workflow_equals_the_asyncio_runner_and_is_deterministic() -> None:
    settings = Settings()
    maf = StudioService(settings, use_workflow=True)
    plain = StudioService(settings, use_workflow=False)
    seen: set[str] = set()
    for trigger in triggers(*DL):
        first = tuple(r for r in trigger.rules if r not in seen)
        a = run_job(maf, *DL, trigger, first)
        b = run_job(plain, *DL, trigger, first)
        again = run_job(maf, *DL, trigger, first)
        seen.update(a.commentary.rules)
        assert public(a) == public(b) == public(again), trigger.id
        assert not a.degraded
        assert (a.talking_point is not None) == wants_talking_point(trigger)
        agents = [span.agent for span in a.trace or () if span.parent_id is None]
        assert agents[:2] == ["StudioIntake", "FactPackBuilder"] and agents[-1] == "StudioPublisher"


def test_branches_run_concurrently_in_one_superstep() -> None:
    """With a 0.15 s model, three branches take about 0.15 s, not 0.45 s."""
    settings = Settings(mock_llm_latency_ms=150)
    service = StudioService(settings, budgets=StudioBudgets.offline(settings))
    started = time.perf_counter()
    output = run_job(service, *DL, goal_trigger())
    elapsed = time.perf_counter() - started
    assert output.talking_point is not None and output.lower_third is not None
    assert elapsed < 0.4, elapsed


def test_the_view_must_be_pinned_at_the_trigger() -> None:
    trigger = goal_trigger()
    with pytest.raises(ValueError, match="pin the view"):
        run(
            StudioService(Settings()).run(StudioJob(trigger=trigger), view_at(*DL, trigger.seq + 5))
        )


# --------------------------------------------------------------------------- fault matrix 16.4


@pytest.mark.parametrize("talker", KINDS)
@pytest.mark.parametrize("commentator", KINDS)
def test_fault_matrix(commentator: str, talker: str) -> None:
    for trigger in (goal_trigger(), shot_trigger()):
        output, elapsed = timed_job(trigger, commentator, talker)
        assert elapsed <= FAST.job_s + 0.3, elapsed  # the job budget holds, hangs included
        published_texts_pass(output, trigger)
        vi = [t for t in output.commentary.texts if t.language == "vi"]
        if commentator == "ok":
            assert all(t.source == "llm" for t in output.commentary.texts)
        else:  # live commentary is not retried: a failed draft keeps its template
            assert all(t.source == "template" for t in vi)
            assert "writer_fallback" in output.commentary.degraded
        statuses = {span.status for span in output.trace or ()}
        if commentator in DRAFT_FAULTS:
            assert "rejected" in statuses
        # one branch's fault never changes the other branch's output
        alone = job(trigger, commentator, "ok")
        assert output.commentary == alone.commentary
        if trigger.kind == "goal":
            tp = output.talking_point
            assert tp is not None and tp == job(trigger, "ok", talker).talking_point
            if talker in ("timeout", "error", "invalid_json"):  # no draft at all
                assert tp.source_en == tp.source_vi == "template"
                assert "writer_fallback" in tp.degraded
        else:
            assert output.talking_point is None


def test_a_talking_point_keeps_the_lines_that_pass() -> None:
    """Design 6: a language is taken when at least 2 of its lines pass; the bad one is left out."""
    output = job(goal_trigger(), "ok", "hallucinate:2")
    tp = output.talking_point
    assert tp is not None and tp.source_en == tp.source_vi == "llm"
    assert len(tp.en) == len(tp.vi) == 2
    clean = job(goal_trigger(), "ok", "ok").talking_point
    assert clean is not None and tuple(tp.en) == clean.en[1:]


def test_offline_commentary_is_retried_with_feedback() -> None:
    settings = Settings(mock_llm_faults="commentator:hallucinate:1")
    service = StudioService(settings, budgets=StudioBudgets.offline(settings))
    output = run_job(service, *DL, goal_trigger())
    assert all(t.source == "llm" for t in output.commentary.texts)
    retries = [s for s in output.trace or () if s.kind == "llm" and s.attempt == 2]
    assert retries and retries[0].input["feedback"]


# --------------------------------------------------------------------------- breaker and fallbacks


def test_studio_breaker_opens_without_touching_the_claims() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock.now)
    every_call_fails = "talker:error:99,commentator:error:99,designer:error:99"
    service = studio_service(every_call_fails, breaker=breaker)
    claims = mock_service()
    first = run_job(service, *DL, goal_trigger())
    assert "writer_fallback" in first.degraded and breaker.state == "open"
    second = run_job(service, *DL, goal_trigger())
    assert {"llm_skipped", "circuit_open"} <= set(second.degraded)
    assert not [s for s in second.trace or () if s.kind == "llm"]  # no model call at all
    assert claims.breaker.state == "closed"
    clock.t += 61  # cooldown over: one trial call goes through (and fails again)
    third = run_job(service, *DL, goal_trigger())
    assert len([s for s in third.trace or () if s.kind == "llm"]) == 1
    assert breaker.state == "open"


def test_workflow_failure_falls_back_to_the_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any) -> None:
        raise RuntimeError("workflow engine down")

    monkeypatch.setattr(studio_module, "build_studio_workflow", broken)
    output = run_job(StudioService(Settings()), *DL, goal_trigger())
    assert "orchestrator_fallback" in output.degraded
    assert all(t.source == "template" for t in output.commentary.texts)
    assert output.talking_point is not None and output.talking_point.source_en == "template"
    assert output.lower_third is not None and output.lower_third.source == "rule"


def test_graphics_designer_choice_is_validated() -> None:
    good = run_job(studio_service(), *DL, goal_trigger())
    assert good.lower_third is not None and good.lower_third.source == "llm"
    bad = run_job(studio_service("designer:bad_choice"), *DL, goal_trigger())
    assert bad.lower_third is not None and bad.lower_third.source == "rule"
    assert "writer_fallback" in bad.degraded
    assert bad.lower_third.stats == good.lower_third.stats  # the rule table's choice


def test_low_importance_triggers_skip_the_model_by_policy() -> None:
    service = studio_service(llm_min_importance=2)
    output = run_job(service, *DL, shot_trigger())
    assert all(t.source == "template" for t in output.commentary.texts)
    assert not output.degraded  # a policy, not a degradation


# --------------------------------------------------------------------------- recap service


def recap_pack():  # type: ignore[no-untyped-def]
    return offline(*DL).recap_packs["FULL"]


def test_recap_revision_two_when_the_model_passes() -> None:
    service = RecapService(Settings())
    first = service.template(recap_pack())
    assert first.revision == 1 and {t.source for t in first.texts} == {"template"}
    improved, trace = run(service.improve(recap_pack(), first))
    assert improved is not None and improved.revision == 2 and not improved.degraded
    assert {t.source for t in improved.texts} == {"llm"}
    assert [t.language + t.mode for t in improved.texts] == [
        t.language + t.mode for t in first.texts
    ]
    assert any(span.kind == "guardrail" for span in trace.spans)


def test_recap_keeps_revision_one_when_the_model_fails() -> None:
    service = RecapService(Settings(mock_llm_faults="recapper:error:9"))
    first = service.template(recap_pack())
    improved, _ = run(service.improve(recap_pack(), first))
    assert improved is None


def test_recap_versions_fall_back_one_by_one() -> None:
    service = RecapService(Settings(mock_llm_faults="recapper:hallucinate:2"))
    first = service.template(recap_pack())
    improved, _ = run(service.improve(recap_pack(), first))
    assert improved is not None and "writer_fallback" in improved.degraded
    sources = {(t.language, t.mode): t.source for t in improved.texts}
    assert sources[("vi", "casual")] == "template"  # written first, rejected twice
    assert sources[("en", "analyst")] == "llm"


def test_recap_without_budget_is_skipped() -> None:
    service = RecapService(Settings(), budget_s=0.01)
    first = service.template(recap_pack())
    improved, trace = run(service.improve(recap_pack(), first))
    assert improved is None
    assert any(span.name == "skip_llm" for span in trace.spans)
