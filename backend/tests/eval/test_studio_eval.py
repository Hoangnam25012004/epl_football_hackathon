"""Studio evaluation (docs/day3-design.md section 16.7): the report of the four sample matches
meets every threshold with the mock; injected faults are rejected and never published."""

import pytest
from helpers import generated, run
from studio_helpers import MATRIX, SAMPLES, offline, pack

from pundit.llm.settings import Settings
from pundit.orchestration.recap_service import RecapService
from pundit.orchestration.studio_eval import build_report, oracle_problems, run_offline
from pundit.orchestration.studio_service import StudioService


@pytest.mark.parametrize(("scenario", "seed"), SAMPLES)
def test_sample_reports_pass(scenario: str, seed: int) -> None:
    report = build_report(offline(scenario, seed))
    assert report.failures() == [], "\n".join(report.lines())
    assert not report.leaks and not report.oracle and report.drafts_rejected == 0
    assert report.d1.total > 0 and report.d2_strict.total > 0
    assert report.silence_min <= 5.0
    assert report.counts["recap"] == 2 and report.sources["recap:llm"] == 8


@pytest.mark.slow
@pytest.mark.parametrize(("scenario", "seed"), MATRIX)
def test_matrix_reports_pass(scenario: str, seed: int) -> None:
    settings = Settings()
    result = run(
        run_offline(
            generated(scenario, seed),
            StudioService(settings, offline=True),
            RecapService(settings),
        )
    )
    report = build_report(result)
    assert report.failures() == [], "\n".join(report.lines())


def test_injected_faults_are_rejected_and_never_published() -> None:
    """The Day 3 demo scene (section 17, P7 step 6)."""
    settings = Settings(mock_llm_faults="commentator:wrong_outcome:1,talker:hallucinate")
    result = run(
        run_offline(
            generated("dominant_loser", 42),
            StudioService(settings, offline=True),
            RecapService(settings),
        )
    )
    report = build_report(result)
    assert report.drafts_rejected > 0
    assert {"WRONG_OUTCOME", "UNVERIFIED_NUMBER"} <= set(report.rejection_codes)
    assert not report.leaks and not report.oracle
    assert report.failures() == [], "\n".join(report.lines())


def test_the_oracle_is_independent_of_the_guardrail() -> None:
    built = offline("dominant_loser", 42).recap_packs["FULL"]
    match = built.match
    scorer = next(p for p in match.players if p.id == built.goals[0].scorer)
    stranger = next(p for p in match.players if p.id not in built.players)
    common = {
        "facts": built.facts,
        "match": match,
        "players": built.players,
        "minutes": [],
        "window_numbers": built.window_numbers,
    }
    assert not oracle_problems(f"{scorer.short_name} ghi bàn.", no_goal=False, **common)
    assert oracle_problems(f"{stranger.short_name} ghi bàn.", no_goal=False, **common)
    assert oracle_problems("Có 987 cú sút.", no_goal=False, **common)
    assert oracle_problems(f"{scorer.short_name} ghi bàn.", no_goal=True, **common)


def test_the_oracle_reads_numbers_as_written() -> None:
    """Day 6, the Ollama studio audit: "1.5" for an xG shown 1.50 is the number on screen;
    "0.4" for a chance shown 0.44 is not."""
    for trigger, right, wrong in [
        ("red_card_swing-7-m0005", "Kestrel Athletic đã có 1.5 bàn thắng kỳ vọng.", "1.4 xG"),
        ("red_card_swing-7-m0040", "The shot had a 0.44 chance of going in.", "a 0.4 chance"),
    ]:
        built = pack("red_card_swing", 7, trigger)
        common = {
            "facts": built.facts,
            "match": built.match,
            "players": [],
            "minutes": [],
            "window_numbers": built.window_numbers,
        }
        assert not oracle_problems(right, no_goal=False, **common)
        assert oracle_problems(wrong, no_goal=False, **common)
