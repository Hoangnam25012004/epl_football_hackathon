"""Claim evaluation (design section 13.6): labelled claims -> accuracy, wrong verdicts, latency.

A *wrong* verdict is one that differs from the label and is not INSUFFICIENT_DATA: refusing is
allowed, misleading is not. Held-out claims (written apart, never used to tune the parser)
measure coverage honestly. Each labelled claim may also list the displays its facts must show,
so a change in the generated data shows up as a failed check, not as a silent drift.
"""

import json
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from pundit.agents.contracts import ClaimRequest, ClaimResult, ReasonCode, Verdict
from pundit.domain.events import Event
from pundit.domain.matchtime import MS_PER_MINUTE, parse_display_minute
from pundit.engine.generator import generate
from pundit.engine.match import GeneratedMatch
from pundit.orchestration.service import ClaimService, StaticSource

LLM_AGENTS = frozenset({"ClaimAnalyst", "Explainer"})
UNAVAILABLE = frozenset({"timeout", "budget", "error", "circuit_open"})


def llm_failures_of(result: ClaimResult) -> set[str]:
    """How the LLM calls of a claim failed (from its trace): "timeout", "budget", "error",
    "circuit_open" (the model was not available) or "invalid_output" (it answered twice with
    something unusable)."""
    found = {
        span.output["failure"]
        for span in result.trace or ()
        if span.agent in LLM_AGENTS and isinstance(span.output.get("failure"), str)
    }
    if "circuit_open" in result.degraded:
        found.add("circuit_open")
    return found


class EvalCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    text: str
    expected: Verdict
    at: str | None = None  # display minute ("30'") or None / "FT" for the end of the match
    expect_reason: ReasonCode | None = None
    expect_display: dict[str, str] = Field(default_factory=dict)  # fact key -> display
    note: str = ""


class EvalMatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario: str
    seed: int


class EvalFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    heldout: bool = False
    match: EvalMatch
    claims: tuple[EvalCase, ...]


def load_eval(path: Path) -> EvalFile:
    return EvalFile.model_validate(json.loads(path.read_text(encoding="utf-8")))


def seq_at(events: Sequence[Event], at: str | None) -> int | None:
    """Seq of the last event of a displayed minute ("63'" = up to 63:00 on the scoreboard)."""
    if at is None or at.strip().upper() == "FT":
        return None
    period, clock_ms = parse_display_minute(at)
    end = clock_ms + MS_PER_MINUTE
    last: int | None = None
    for event in events:
        if event.period < period or (event.period == period and event.clock_ms < end):
            last = event.seq
        else:
            break
    if last is None:
        raise ValueError(f"no event before {at!r}")
    return last


@dataclass(frozen=True)
class EvalRow:
    case: EvalCase
    heldout: bool
    result: ClaimResult

    @property
    def correct(self) -> bool:
        reason_ok = (
            self.case.expect_reason is None or self.case.expect_reason == self.result.reason_code
        )
        return self.result.verdict == self.case.expected and reason_ok

    @property
    def wrong(self) -> bool:
        return self.result.verdict not in (self.case.expected, "INSUFFICIENT_DATA")

    @property
    def fact_mismatches(self) -> list[str]:
        shown = {f.key: f.display for r in self.result.assertions for f in r.facts}
        return [
            f"{key}: expected {want}, got {shown.get(key)}"
            for key, want in self.case.expect_display.items()
            if shown.get(key) != want
        ]

    @property
    def rejected_drafts(self) -> int:
        trace = self.result.trace or ()
        return sum(
            1
            for span in trace
            if span.agent == "Guardrail"
            and span.status == "rejected"
            and span.input.get("source") == "llm"
        )

    @property
    def leaked(self) -> bool:
        return bool(self.result.explanation) and not self.result.guardrail.passed


@dataclass
class EvalReport:
    rows: list[EvalRow] = field(default_factory=list)

    def _rows(self, heldout: bool) -> list[EvalRow]:
        return [r for r in self.rows if r.heldout == heldout]

    @property
    def accuracy(self) -> float:
        main = self._rows(False)
        return sum(r.correct for r in main) / len(main) if main else 1.0

    @property
    def wrong(self) -> list[EvalRow]:
        return [r for r in self.rows if r.wrong]

    @property
    def heldout_coverage(self) -> float:
        held = self._rows(True)
        return (
            sum(r.result.verdict != "INSUFFICIENT_DATA" for r in held) / len(held) if held else 1.0
        )

    @property
    def rejected_drafts(self) -> int:
        return sum(r.rejected_drafts for r in self.rows)

    @property
    def leaks(self) -> int:
        return sum(r.leaked for r in self.rows)

    @property
    def fact_mismatches(self) -> list[str]:
        return [f"{r.case.id} {m}" for r in self.rows for m in r.fact_mismatches]

    def latency(self, quantile: float) -> float:
        values = sorted(r.result.timings_ms.get("total", 0.0) for r in self.rows)
        if not values:
            return 0.0
        if len(values) == 1:
            return values[0]
        return statistics.quantiles(values, n=100, method="inclusive")[round(quantile * 100) - 1]

    @property
    def llm_failures(self) -> int:
        """Claims where the model was not available: a call timed out or failed, or the open
        circuit skipped it. Unusable answers are quality, counted in `llm_invalid`."""
        return sum(bool(llm_failures_of(row.result) & UNAVAILABLE) for row in self.rows)

    @property
    def llm_invalid(self) -> int:
        """Claims where the model answered but its spec or text was unusable (rules / template)."""
        return sum("invalid_output" in llm_failures_of(row.result) for row in self.rows)

    @property
    def llm_analyst_answers(self) -> int:
        """Claims whose spec came from the LLM (it agreed with the rules or extended them)."""
        return sum(r.result.analyst_source == "llm" for r in self.rows)

    @property
    def llm_disagreements(self) -> int:
        """Claims where the LLM spec contradicted the rules and the rules were used."""
        return sum("analyst_disagreement" in r.result.degraded for r in self.rows)

    @property
    def llm_explanations(self) -> int:
        return sum(r.result.explanation_source == "llm" for r in self.rows)

    def llm_working(self, *, max_failure_share: float = 0.2) -> bool:
        """The model answered: it gave at least one usable spec and was unavailable on at most
        `max_failure_share` of the claims. Invalid answers, disagreements and rejected drafts
        are quality, reported but not counted here."""
        if not self.rows:
            return False
        answered = self.llm_analyst_answers + self.llm_disagreements
        return answered > 0 and self.llm_failures / len(self.rows) <= max_failure_share

    def passed(self, *, require_full_accuracy: bool) -> bool:
        ok = not self.wrong and self.leaks == 0 and not self.fact_mismatches
        return ok and (self.accuracy == 1.0 or not require_full_accuracy)

    def lines(self) -> list[str]:
        out = [f"{'id':<5} {'expected':<17} {'verdict':<17} {'ok':<3} claim"]
        for r in self.rows:
            mark = "ok" if r.correct else ("WRONG" if r.wrong else "-")
            tag = "H " if r.heldout else ""
            row = f"{tag}{r.case.id:<5} {r.case.expected:<17} {r.result.verdict:<17} {mark:<3}"
            out.append(f"{row} {r.case.text}")
        held = self._rows(True)
        out += [
            "",
            f"accuracy (labelled set): {self.accuracy:.0%} of {len(self._rows(False))}",
            f"wrong verdicts (all sets): {len(self.wrong)}",
            f"held-out coverage: {self.heldout_coverage:.0%} of {len(held)}",
            f"LLM drafts rejected by the guardrail: {self.rejected_drafts}",
            f"rejected text published (leaks): {self.leaks}",
            f"fact mismatches: {len(self.fact_mismatches)}",
            f"LLM spec used: {self.llm_analyst_answers} of {len(self.rows)} claims; "
            f"LLM spec overruled by the rules: {self.llm_disagreements}; "
            f"LLM explanations published: {self.llm_explanations}",
            f"claims with the LLM unavailable (timeout or error, fallback used): "
            f"{self.llm_failures}; with an unusable LLM answer (rules or template used): "
            f"{self.llm_invalid}",
            f"latency p50 {self.latency(0.5):.0f} ms, p95 {self.latency(0.95):.0f} ms",
        ]
        out += [f"  {m}" for m in self.fact_mismatches]
        return out


async def run_eval(files: Sequence[EvalFile], service: ClaimService) -> EvalReport:
    report = EvalReport()
    matches: dict[tuple[str, int], GeneratedMatch] = {}
    for spec in files:
        key = (spec.match.scenario, spec.match.seed)
        if key not in matches:
            matches[key] = generate(spec.match.scenario, spec.match.seed)
        match = matches[key]
        source = StaticSource(match.meta, match.events)
        for index, case in enumerate(spec.claims, start=1):
            request = ClaimRequest(
                match_id=match.meta.match_id,
                text=case.text,
                as_of_seq=seq_at(match.events, case.at),
                include_trace=True,
            )
            claim_id = f"{match.meta.match_id}-{'h' if spec.heldout else 'e'}{index:04d}"
            result = await service.verify(request, source, claim_id=claim_id)
            report.rows.append(EvalRow(case=case, heldout=spec.heldout, result=result))
    return report
