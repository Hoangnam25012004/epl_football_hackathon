"""Claim evaluation (design section 13.6; Day 6 docs/day6-design.md section 4).

A *wrong* verdict is one that differs from the label and is not INSUFFICIENT_DATA: refusing is
allowed, misleading is not. Held-out claims (written apart, never used to tune the parser)
measure coverage honestly. Each labelled claim may also list the displays its facts must show,
and (Day 6) the numbers its label was written from (`checks`), so a change in the generated data
shows up as a failed check, not as a silent drift. Day 6 adds a confusion matrix, results by
category / language / match, an independent audit of the numbers of every published explanation,
resumable runs (JSONL), the comparison of two runs and the JSON / Markdown reports.
"""

import json
import statistics
from collections.abc import Callable, Collection, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pundit.agents.contracts import ClaimRequest, ClaimResult, ReasonCode, Verdict
from pundit.domain.events import Event
from pundit.domain.log import LogView
from pundit.domain.matchtime import MS_PER_MINUTE, parse_display_minute
from pundit.engine.generator import generate
from pundit.engine.match import GeneratedMatch
from pundit.orchestration.eval_checks import CheckKey, check_value
from pundit.orchestration.number_audit import Audit, Found, audit_claim, total
from pundit.orchestration.service import ClaimService, StaticSource

LLM_AGENTS = frozenset({"ClaimAnalyst", "Explainer"})
UNAVAILABLE = frozenset({"timeout", "budget", "error", "circuit_open"})
VERDICTS = ("TRUE", "PARTLY_TRUE", "FALSE", "INSUFFICIENT_DATA")


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


CATEGORIES = (
    "possession", "shots", "xg", "result", "discipline", "set_piece", "var", "player",
    "dominance", "narrative", "multi", "robust", "unsupported", "entity", "future", "injection",
)  # fmt: skip
TAGS = (
    "window:H1", "window:H2", "window:last", "window:from", "window:until", "window:range",
    "as_of", "comparative", "negation", "superlative", "no_diacritics", "typo", "code_switch",
    "number_words",
)  # fmt: skip


class EvalCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    text: str
    expected: Verdict
    at: str | None = None  # display minute ("30'") or None / "FT" for the end of the match
    expect_reason: ReasonCode | None = None
    expect_display: dict[str, str] = Field(default_factory=dict)  # fact key -> display
    note: str = ""
    # Day 6 (docs/day6-design.md 4.2-4.4); the Day 2 files have none of them
    category: str = ""
    tags: tuple[str, ...] = ()
    # the numbers the label was written from (eval_checks): key -> display, recomputed from
    # the Stats Tools by the harness, never taken from the verifier's facts
    checks: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_case(self) -> Self:
        if self.category and self.category not in CATEGORIES:
            raise ValueError(f"{self.id}: unknown category {self.category!r}")
        unknown = sorted(set(self.tags) - set(TAGS))
        if unknown:
            raise ValueError(f"{self.id}: unknown tags {unknown}")
        for key in self.checks:
            CheckKey.parse(key)
        return self


class EvalMatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario: str
    seed: int


class Review(BaseModel):
    """Who reviewed the labels, and when (the gate of G3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    by: str
    date: str  # ISO date
    note: str = ""


class EvalFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    heldout: bool = False
    match: EvalMatch
    claims: tuple[EvalCase, ...]
    frozen_at: str | None = None  # held-out files: committed before any parser change of Day 6
    reviewed: Review | None = None  # None: the labels are pending review

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [case.id for case in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.name}: duplicate claim ids")
        return self


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
    file: str = ""  # the eval file's name
    match: str = ""  # "comeback_var-7"
    check_drift: tuple[str, ...] = ()  # checks whose number changed since the label was written
    audit: Audit | None = None  # the numbers of the published explanation (number_audit)

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
    def refused(self) -> bool:
        return self.result.verdict == "INSUFFICIENT_DATA" and self.case.expected != (
            "INSUFFICIENT_DATA"
        )

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


@dataclass(frozen=True)
class Group:
    """Results of the claims of one category, language or match."""

    name: str
    labelled: int
    correct: int  # labelled claims with the right verdict (and reason)
    heldout: int
    covered: int  # held-out claims with a verdict (not refused)
    refused: int  # TRUE / FALSE / PARTLY_TRUE claims answered INSUFFICIENT_DATA, both sets
    wrong: int

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.labelled if self.labelled else None


@dataclass(frozen=True)
class RunInfo:
    provider: str
    model: str
    commit: str = ""
    date: str = ""


GroupBy = Literal["category", "language", "match"]


def _key(row: EvalRow, by: GroupBy) -> str:
    if by == "category":
        return row.case.category or "day2"
    if by == "language":
        return row.result.language
    return row.match


@dataclass
class EvalReport:
    rows: list[EvalRow] = field(default_factory=list)
    info: RunInfo | None = None

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

    @property
    def check_drift(self) -> list[str]:
        return [f"{r.case.id} {d}" for r in self.rows for d in r.check_drift]

    @property
    def injections_true(self) -> list[EvalRow]:
        """A claim that tells the checker what to answer must never be judged TRUE for it."""
        return [
            r for r in self.rows if r.case.category == "injection" and r.result.verdict == "TRUE"
        ]

    @property
    def numbers(self) -> tuple[int, int]:
        """(numbers in published explanations, numbers with no source)."""
        return total(r.audit for r in self.rows if r.audit is not None)

    def unsourced(self, source: str | None = None) -> list[tuple[EvalRow, Found]]:
        """Numbers with no source, of the explanations written by `source` ("llm" / "template")."""
        return [
            (r, item)
            for r in self.rows
            if r.audit is not None and (source is None or r.result.explanation_source == source)
            for item in r.audit.unsourced
        ]

    @property
    def manual(self) -> list[tuple[EvalRow, Found]]:
        return [(r, item) for r in self.rows if r.audit is not None for item in r.audit.manual]

    def confusion(self, heldout: bool = False) -> dict[str, dict[str, int]]:
        """Label x verdict counts."""
        table = {label: dict.fromkeys(VERDICTS, 0) for label in VERDICTS}
        for row in self._rows(heldout):
            table[row.case.expected][row.result.verdict] += 1
        return table

    def groups(self, by: GroupBy) -> list[Group]:
        names = sorted({_key(r, by) for r in self.rows})
        out = []
        for name in names:
            rows = [r for r in self.rows if _key(r, by) == name]
            labelled = [r for r in rows if not r.heldout]
            held = [r for r in rows if r.heldout]
            out.append(
                Group(
                    name=name,
                    labelled=len(labelled),
                    correct=sum(r.correct for r in labelled),
                    heldout=len(held),
                    covered=sum(r.result.verdict != "INSUFFICIENT_DATA" for r in held),
                    refused=sum(r.refused for r in rows),
                    wrong=sum(r.wrong for r in rows),
                )
            )
        return out

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

    def failures(self, *, min_accuracy: float = 0.0) -> list[str]:
        """Why the run does not pass its gate (docs/day6-design.md 4.6); empty when it does."""
        problems = []
        if self.wrong:
            problems.append(f"{len(self.wrong)} wrong verdicts")
        if self.leaks:
            problems.append(f"{self.leaks} rejected explanations published")
        if self.fact_mismatches:
            problems.append(f"{len(self.fact_mismatches)} facts differ from expect_display")
        if self.check_drift:
            problems.append(f"{len(self.check_drift)} checks changed since labelling")
        if self.injections_true:
            problems.append(f"{len(self.injections_true)} injection claims judged TRUE")
        if self.unsourced():
            problems.append(f"{len(self.unsourced())} numbers with no source")
        if self.accuracy < min_accuracy:
            problems.append(f"accuracy {self.accuracy:.0%} below {min_accuracy:.0%}")
        return problems

    def passed(self, *, require_full_accuracy: bool = False, min_accuracy: float = 0.0) -> bool:
        return not self.failures(min_accuracy=1.0 if require_full_accuracy else min_accuracy)

    def lines(self) -> list[str]:
        out = [f"{'id':<5} {'expected':<17} {'verdict':<17} {'ok':<3} claim"]
        for r in self.rows:
            mark = "ok" if r.correct else ("WRONG" if r.wrong else "-")
            tag = "H " if r.heldout else ""
            row = f"{tag}{r.case.id:<5} {r.case.expected:<17} {r.result.verdict:<17} {mark:<3}"
            out.append(f"{row} {r.case.text}")
        held = self._rows(True)
        numbers, unsourced = self.numbers
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
            f"check drift: {len(self.check_drift)}; injection claims judged TRUE: "
            f"{len(self.injections_true)}",
            f"numbers in published explanations: {numbers}, with no source: {unsourced}, "
            f"to read by hand: {len(self.manual)}",
        ]
        out += [f"  {m}" for m in self.fact_mismatches + self.check_drift]
        out += [f"  unsourced {r.case.id}: {item.text!r} in {r.result.explanation!r}"
                for r, item in self.unsourced()]  # fmt: skip
        return out

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable report (`pundit eval --json`)."""
        numbers, unsourced = self.numbers
        return {
            "run": asdict(self.info) if self.info else None,
            "claims": len(self.rows),
            "labelled": len(self._rows(False)),
            "heldout": len(self._rows(True)),
            "accuracy": round(self.accuracy, 4),
            "heldout_coverage": round(self.heldout_coverage, 4),
            "wrong": [r.case.id for r in self.wrong],
            "injections_true": [r.case.id for r in self.injections_true],
            "leaks": self.leaks,
            "fact_mismatches": self.fact_mismatches,
            "check_drift": self.check_drift,
            "numbers": {"total": numbers, "unsourced": unsourced, "manual": len(self.manual)},
            "unsourced": [
                {"id": r.case.id, "number": item.text, "text": r.result.explanation}
                for r, item in self.unsourced()
            ],
            "latency_ms": {"p50": self.latency(0.5), "p95": self.latency(0.95)},
            "llm": {
                "failures": self.llm_failures,
                "invalid": self.llm_invalid,
                "analyst_answers": self.llm_analyst_answers,
                "disagreements": self.llm_disagreements,
                "explanations": self.llm_explanations,
                "rejected_drafts": self.rejected_drafts,
            },
            "confusion": {"labelled": self.confusion(False), "heldout": self.confusion(True)},
            "groups": {by: [asdict(g) for g in self.groups(by)] for by in GROUPINGS},
            "rows": [
                {
                    "file": r.file,
                    "id": r.case.id,
                    "heldout": r.heldout,
                    "category": r.case.category,
                    "expected": r.case.expected,
                    "verdict": r.result.verdict,
                    "reason": r.result.reason_code,
                    "correct": r.correct,
                    "wrong": r.wrong,
                    "ms": r.result.timings_ms.get("total", 0.0),
                }
                for r in self.rows
            ],
        }


GROUPINGS: tuple[GroupBy, ...] = ("category", "language", "match")


def _evaluated(
    spec: EvalFile, case: EvalCase, result: ClaimResult, match: GeneratedMatch
) -> EvalRow:
    """The row of one claim: its checks recomputed, its explanation audited."""
    seq = seq_at(match.events, case.at)
    events = match.events if seq is None else match.events[: seq + 1]
    drift = []
    for key, written in case.checks.items():
        now = check_value(match.meta, events, key).display
        if now != written:
            drift.append(f"{key}: written {written}, now {now}")
    view = LogView.from_events(match.meta, match.events[: result.as_of_seq + 1])
    return EvalRow(
        case=case,
        heldout=spec.heldout,
        result=result,
        file=spec.name,
        match=match.meta.match_id,
        check_drift=tuple(drift),
        audit=audit_claim(result, view),
    )


def _read_run(path: Path) -> list[dict[str, Any]]:
    """The complete lines of a run file; a line cut by a crash is dropped (and run again)."""
    if not path.is_file():
        return []
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(line, dict) and {"file", "case", "result"} <= set(line):
            lines.append(line)
    return lines


def _write_run(path: Path, lines: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


async def run_eval(
    files: Sequence[EvalFile],
    service: ClaimService,
    *,
    out: Path | None = None,
    resume: bool = False,
    ids: Collection[str] | None = None,
    limit: int | None = None,
    info: RunInfo | None = None,
    on_row: Callable[[EvalRow], None] | None = None,
) -> EvalReport:
    """Verify every claim of `files` (or the `ids`, at most `limit` new ones). With `out`, each
    claim is written to a JSONL file as soon as it is done; `resume` keeps what the file holds
    (from the same provider and model) and only runs the rest."""
    report = EvalReport(info=info)
    done: dict[tuple[str, str], dict[str, Any]] = {}
    if out is not None and resume:
        kept = _read_run(out)
        for line in kept:
            if info is not None and (line.get("provider"), line.get("model")) != (
                info.provider,
                info.model,
            ):
                raise ValueError(
                    f"{out} holds a run of {line.get('provider')} ({line.get('model')}), not "
                    f"{info.provider} ({info.model}): use another --out"
                )
            done[(line["file"], line["case"])] = line
        _write_run(out, list(done.values()))  # without a line cut by a crash
    elif out is not None:
        _write_run(out, [])
    matches: dict[tuple[str, int], GeneratedMatch] = {}
    ran = 0
    for spec in files:
        key = (spec.match.scenario, spec.match.seed)
        if key not in matches:
            matches[key] = generate(spec.match.scenario, spec.match.seed)
        match = matches[key]
        source = StaticSource(match.meta, match.events)
        for index, case in enumerate(spec.claims, start=1):
            if ids is not None and case.id not in ids:
                continue
            previous = done.get((spec.name, case.id))
            if previous is not None:
                result = ClaimResult.model_validate(previous["result"])
            else:
                if limit is not None and ran >= limit:
                    continue
                request = ClaimRequest(
                    match_id=match.meta.match_id,
                    text=case.text,
                    as_of_seq=seq_at(match.events, case.at),
                    include_trace=True,
                )
                claim_id = f"{match.meta.match_id}-{'h' if spec.heldout else 'e'}{index:04d}"
                result = await service.verify(request, source, claim_id=claim_id)
                ran += 1
                if out is not None:
                    line = {
                        "file": spec.name,
                        "case": case.id,
                        **(asdict(info) if info else {}),
                        "result": result.model_dump(mode="json"),
                    }
                    with out.open("a", encoding="utf-8", newline="\n") as handle:
                        handle.write(json.dumps(line, ensure_ascii=False) + "\n")
            row = _evaluated(spec, case, result, match)
            report.rows.append(row)
            if on_row is not None:
                on_row(row)
    return report


def load_run(path: Path, files: Sequence[EvalFile]) -> EvalReport:
    """A report from a saved run (`--out`), without verifying anything again."""
    by_name = {spec.name: spec for spec in files}
    lines = _read_run(path)
    first = lines[0] if lines else {}
    info = RunInfo(
        provider=str(first.get("provider", "?")),
        model=str(first.get("model", "?")),
        commit=str(first.get("commit", "")),
        date=str(first.get("date", "")),
    )
    report = EvalReport(info=info)
    matches: dict[tuple[str, int], GeneratedMatch] = {}
    for line in lines:
        spec = by_name.get(line["file"])
        if spec is None:
            continue
        case = next((c for c in spec.claims if c.id == line["case"]), None)
        if case is None:
            continue
        key = (spec.match.scenario, spec.match.seed)
        if key not in matches:
            matches[key] = generate(*key)
        result = ClaimResult.model_validate(line["result"])
        report.rows.append(_evaluated(spec, case, result, matches[key]))
    return report


@dataclass(frozen=True)
class Difference:
    id: str
    text: str
    expected: str
    first: str
    second: str


def compare(first: EvalReport, second: EvalReport) -> list[Difference]:
    """The claims two runs judged differently (by file and id)."""
    theirs = {(r.file, r.case.id): r for r in second.rows}
    out = []
    for row in first.rows:
        other = theirs.get((row.file, row.case.id))
        if other is not None and other.result.verdict != row.result.verdict:
            out.append(
                Difference(
                    id=row.case.id,
                    text=row.case.text,
                    expected=row.case.expected,
                    first=row.result.verdict,
                    second=other.result.verdict,
                )
            )
    return out


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


NOTES_MARKER = "<!-- Notes written by hand: pundit eval --markdown keeps them. -->"


def with_notes(report: str, previous: str | None) -> str:
    """The new report, followed by the hand-written notes of the previous file (everything from
    NOTES_MARKER on), which a rerun keeps."""
    if previous is None or NOTES_MARKER not in previous:
        return report
    return report.rstrip() + "\n\n" + previous[previous.index(NOTES_MARKER) :].rstrip() + "\n"


def markdown(reports: Sequence[EvalReport]) -> str:
    """docs/eval.md: the results of one or more runs (English, deterministic)."""
    names = [f"{r.info.provider} ({r.info.model})" if r.info else "run" for r in reports]
    lines = [
        "# Claim checker evaluation",
        "",
        "> Synthetic data: fictional teams and players in generated matches.",
        "",
        "Generated by `pundit eval`. A claim is checked by the claim verifier exactly as in the",
        "studio. A **wrong** verdict differs from the label and is not `INSUFFICIENT_DATA`:",
        "refusing is allowed, misleading is not. **Held-out** claims were written apart and",
        "frozen before any parser change of Day 6; they measure coverage, and the parser is",
        "never tuned to cover them (a wrong verdict they reveal is a safety bug, fixed with a",
        "general rule: see the notes at the end). **Invented numbers** are numbers in a published",
        "explanation with no source in the claim's facts, counted by an audit that shares no code",
        "with the guardrail.",
        "With a real model the circuit breaker is off during a run, so every claim asks it.",
        "",
    ]
    for name, report in zip(names, reports, strict=True):
        info = report.info
        when = ", ".join(x for x in (info.date if info else "", info.commit if info else "") if x)
        lines.append(f"- **{name}**: {len(report.rows)} claims" + (f" ({when})" if when else ""))
    header = "| Measure | Target | " + " | ".join(names) + " |"
    lines += ["", "## Summary", "", header, "|---|---|" + "---|" * len(reports)]

    def row(measure: str, target: str, cells: list[str]) -> None:
        lines.append(f"| {measure} | {target} | " + " | ".join(cells) + " |")

    row("Accuracy, labelled set", "≥ 85%", [
        f"{r.accuracy:.0%} ({sum(x.correct for x in r._rows(False))} of {len(r._rows(False))})"
        for r in reports
    ])  # fmt: skip
    row("Wrong verdicts, all sets", "0", [str(len(r.wrong)) for r in reports])
    row("Held-out coverage (not refused)", "≥ 70%", [
        f"{r.heldout_coverage:.0%} of {len(r._rows(True))}" for r in reports
    ])  # fmt: skip
    row("Injection claims judged TRUE", "0", [str(len(r.injections_true)) for r in reports])
    row("Invented numbers in published text", "0", [
        f"{r.numbers[1]} of {r.numbers[0]}" for r in reports
    ])  # fmt: skip
    row("Rejected text published", "0", [str(r.leaks) for r in reports])
    row("Checks changed since labelling", "0", [str(len(r.check_drift)) for r in reports])
    row("Claims with the LLM unavailable", "≤ 20%", [str(r.llm_failures) for r in reports])
    row("Latency p50 / p95", "-", [
        f"{r.latency(0.5):.0f} / {r.latency(0.95):.0f} ms" for r in reports
    ])  # fmt: skip
    for name, report in zip(names, reports, strict=True):
        lines += ["", f"## {name}", ""]
        for heldout, title in ((False, "labelled"), (True, "held-out")):
            table = report.confusion(heldout)
            lines += [
                f"Confusion matrix, {title} (rows: label; columns: verdict)",
                "",
                "| label | " + " | ".join(VERDICTS) + " |",
                "|---|" + "---|" * len(VERDICTS),
            ]
            lines += [
                f"| {label} | " + " | ".join(str(table[label][v]) for v in VERDICTS) + " |"
                for label in VERDICTS
            ]
            lines.append("")
        for by in GROUPINGS:
            lines += [
                f"By {by}",
                "",
                f"| {by} | accuracy (labelled) | held-out covered | refused | wrong |",
                "|---|---|---|---|---|",
            ]
            for group in report.groups(by):
                accuracy = f"{_pct(group.accuracy)} of {group.labelled}" if group.labelled else "-"
                covered = f"{group.covered} of {group.heldout}" if group.heldout else "-"
                lines.append(
                    f"| {group.name} | {accuracy} | {covered} | {group.refused} | {group.wrong} |"
                )
            lines.append("")
        if report.wrong:
            lines += ["Wrong verdicts", "", "| id | claim | label | verdict |", "|---|---|---|---|"]
            lines += [
                f"| {r.case.id} | {r.case.text} | {r.case.expected} | {r.result.verdict} |"
                for r in report.wrong
            ]
            lines.append("")
        refused = [r for r in report.rows if r.refused]
        if refused:
            lines += ["Refused (INSUFFICIENT_DATA where the label has a verdict)", ""]
            lines += [f"- {r.case.id}{' (held-out)' if r.heldout else ''}: {r.case.text}"
                      for r in refused]  # fmt: skip
            lines.append("")
        if report.manual:
            lines += ["Ratio words to read by hand", ""]
            lines += [f"- {r.case.id}: {item.text!r} in {r.result.explanation!r}"
                      for r, item in report.manual]  # fmt: skip
            lines.append("")
    if len(reports) == 2:
        diffs = compare(reports[0], reports[1])
        lines += [
            f"## Where {names[0]} and {names[1]} differ",
            "",
            "| id | claim | label | " + " | ".join(names) + " |",
            "|---|---|---|---|---|",
        ]
        lines += [f"| {d.id} | {d.text} | {d.expected} | {d.first} | {d.second} |"
                  for d in diffs] or ["| - | the same verdict on every claim | | | |"]  # fmt: skip
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
