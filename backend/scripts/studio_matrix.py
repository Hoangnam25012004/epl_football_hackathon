"""Day 3 system check with the mock (docs/day3-design.md section 17, phase P4).

Runs the studio report on 4 scenarios x 3 seeds, the 10 x 10 fault matrix on a goal and a
saved shot, determinism (S-1) and offline == live (S-2), and prints one summary table.
Exit code 1 when any threshold of section 16.7 is missed.

    cd backend
    uv run python scripts/studio_matrix.py
"""

import asyncio
import io
import statistics
import sys
import time

from pundit.agents.contracts import StudioJob, StudioOutput, Trigger
from pundit.agents.studio_guard import check_studio, scope_of
from pundit.api.app import default_crew
from pundit.api.sessions import MatchSession
from pundit.engine.generator import generate
from pundit.engine.replay import SteppingClock
from pundit.llm.settings import Settings
from pundit.orchestration.recap_service import RecapService
from pundit.orchestration.service import ClaimService
from pundit.orchestration.studio_eval import OfflineRun, build_report, pack_of, run_offline
from pundit.orchestration.studio_service import StudioService
from pundit.orchestration.studio_steps import StudioBudgets

SCENARIOS = ("dominant_loser", "comeback_var", "red_card_swing", "balanced")
SEEDS = (3, 7, 42)
FAULTS = (
    "ok", "timeout", "error", "invalid_json", "hallucinate:1", "hallucinate:2", "unsafe",
    "wrong_player", "wrong_outcome", "wrong_language",
)  # fmt: skip
FAST = StudioBudgets(
    talking_point_s=0.3, commentary_s=0.3, graphics_s=0.2, job_s=0.5, llm_timeout_s=0.2,
    min_llm_s=0.01,
)  # fmt: skip


async def offline_run(scenario: str, seed: int) -> OfflineRun:
    settings = Settings()
    return await run_offline(
        generate(scenario, seed), StudioService(settings, offline=True), RecapService(settings)
    )


def public(output: StudioOutput) -> dict[str, object]:
    data: dict[str, object] = output.model_dump(mode="json", exclude={"trace", "timings_ms"})
    return data


async def reports() -> bool:
    print("4 scenarios x 3 seeds (mock)")
    print(
        f"{'match':<20}{'items':>6}{'tp':>4}{'lt':>4}{'leaks':>6}{'oracle':>7}{'D1':>6}{'D2':>6}"
        f"{'D2s':>6}{'D3':>6}{'D4':>6}{'quiet':>7}{'p95 ms':>8}  result"
    )
    ok = True
    for scenario in SCENARIOS:
        for seed in SEEDS:
            report = build_report(await offline_run(scenario, seed))
            failures = report.failures()
            ok &= not failures
            lat = sorted(report.latencies_ms)
            p95 = statistics.quantiles(lat, n=20)[18]
            print(
                f"{report.match_id:<20}{report.counts['commentary']:>6}"
                f"{report.counts['talking_point']:>4}{report.counts['lower_third']:>4}"
                f"{len(report.leaks):>6}{len(report.oracle):>7}"
                f"{100 * report.d1.rate:>5.0f}%{100 * report.d2.rate:>5.0f}%"
                f"{100 * report.d2_strict.rate:>5.0f}%{100 * report.d3.rate:>5.0f}%"
                f"{100 * report.d4.rate:>5.0f}%{report.silence_min:>6.1f}'{p95:>8.0f}  "
                f"{'PASS' if not failures else 'FAIL: ' + '; '.join(failures)}"
            )
    return ok


def _texts_pass(output: StudioOutput, trigger: Trigger, run: OfflineRun) -> bool:
    scope = scope_of(pack_of(trigger, run.view_at(trigger.seq)))
    for text in output.commentary.texts:
        report = check_studio(
            text.text,
            scope,
            language=text.language,
            product="commentary",
            mode=text.mode,
            model_text=text.source == "llm",
        )
        if not report.passed:
            return False
    return True


async def fault_matrix(run: OfflineRun) -> bool:
    goal = next(t for t in run.triggers if t.kind == "goal")
    shot = next(t for t in run.triggers if t.kind == "shot" and t.outcome == "saved")
    runs = leaks = slow = 0
    fallbacks = 0
    longest = 0.0
    for commentator in FAULTS:
        for talker in FAULTS:
            spec = ",".join(
                part
                for part in (
                    f"commentator:{commentator}" if commentator != "ok" else "",
                    f"talker:{talker}" if talker != "ok" else "",
                )
                if part
            )
            service = StudioService(Settings(mock_llm_faults=spec), budgets=FAST)
            for trigger in (goal, shot):
                started = time.perf_counter()
                output = await service.run(StudioJob(trigger=trigger), run.view_at(trigger.seq))
                elapsed = time.perf_counter() - started
                runs += 1
                longest = max(longest, elapsed)
                slow += int(elapsed > FAST.job_s + 0.3)
                leaks += int(not _texts_pass(output, trigger, run))
                fallbacks += int("writer_fallback" in output.degraded)
    ok = leaks == 0 and slow == 0
    print(
        f"\nfault matrix 10 x 10 on a goal and a saved shot: {runs} jobs, {fallbacks} with "
        f"template fallback, leaks {leaks}, over budget {slow}, longest {longest * 1000:.0f} ms "
        f"-> {'PASS' if ok else 'FAIL'}"
    )
    return ok


async def determinism_and_live(run: OfflineRun) -> bool:
    again = await offline_run("dominant_loser", 42)
    same = [public(a) for a in run.outputs] == [public(b) for b in again.outputs]
    settings = Settings(studio_queue_size=4096, studio_drop_at=4096)
    session = MatchSession(
        generate("dominant_loser", 42),
        service=ClaimService(Settings()),
        speed=1000.0,
        clock=SteppingClock(),
        studio=default_crew(settings),
    )
    session.start()
    await session.wait()
    assert session.director is not None
    await session.director.idle()
    await session.director.stop()
    live = list(session.director.commentary.values()) == [o.commentary for o in run.outputs]
    print(f"S-1 determinism (two runs): {'PASS' if same else 'FAIL'}")
    print(f"S-2 offline == live session at 1000x: {'PASS' if live else 'FAIL'}")
    return same and live


async def main() -> int:
    ok = await reports()
    reference = await offline_run("dominant_loser", 42)
    ok &= await fault_matrix(reference)
    ok &= await determinism_and_live(reference)
    print("\nPASSED" if ok else "\nFAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main()))
