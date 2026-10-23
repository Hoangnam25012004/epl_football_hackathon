"""The label sheet a person reviews (docs/day6-design.md 4.3).

One row per claim: what it says, at which minute, how it is meant to be read, the Stats Tools
numbers its label was written from, the independent counts (eval_oracle) for the counted
metrics, the proposed label and why, and an empty column for the reviewer. Deterministic: the
same files give the same sheet, so a regenerated sheet only differs where the data or a label
changed.
"""

from collections.abc import Sequence

from pundit.engine.generator import generate
from pundit.engine.match import GeneratedMatch
from pundit.orchestration.eval_checks import check_value
from pundit.orchestration.evaluation import EvalCase, EvalFile, seq_at

COLUMNS = (
    "id", "claim", "at", "category · tags", "Stats Tools", "independent count", "label", "why",
    "review",
)  # fmt: skip
HEADER = "| " + " | ".join(COLUMNS) + " |"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _row(case: EvalCase, match: GeneratedMatch) -> str:
    seq = seq_at(match.events, case.at)
    events = match.events if seq is None else match.events[: seq + 1]
    stats: list[str] = []
    counts: list[str] = []
    for key, written in case.checks.items():
        value = check_value(match.meta, events, key)
        drift = "" if value.display == written else f" (**label written from {written}**)"
        stats.append(f"`{key}` {value.display}{drift}")
        if value.counted is not None:
            same = "" if value.counted == value.result.value else " **≠ Stats Tools**"
            counts.append(f"`{key}` {value.counted}{same}")
    kind = " · ".join([case.category or "-", *case.tags])
    label = case.expected + (f" (`{case.expect_reason}`)" if case.expect_reason else "")
    cells = [
        case.id,
        _cell(case.text),
        case.at or "FT",
        kind,
        "<br>".join(stats) or "-",
        "<br>".join(counts) or "-",
        label,
        _cell(case.note),
        "",
    ]
    return "| " + " | ".join(cells) + " |"


def label_sheet(files: Sequence[EvalFile]) -> str:
    claims = sum(len(spec.claims) for spec in files)
    pending = [spec.name for spec in files if spec.reviewed is None]
    lines = [
        "# Eval label sheet",
        "",
        "> Synthetic data: fictional teams and players in generated matches.",
        "",
        f"{claims} claims in {len(files)} files; "
        + (f"**pending review**: {len(pending)} files." if pending else "every file reviewed."),
        "Labels follow the Day 2 rules (docs/day2-design.md section 5): tolerances by unit, the",
        "dominance rubric (3 of 4 indicators), the verdict merge. A refusal (INSUFFICIENT_DATA) of",
        "a TRUE / FALSE claim is not a wrong verdict, only a lower accuracy.",
        "",
        "Review: write OK, or the label it should have, in the last column.",
    ]
    matches: dict[tuple[str, int], GeneratedMatch] = {}
    for spec in files:
        key = (spec.match.scenario, spec.match.seed)
        if key not in matches:
            matches[key] = generate(*key)
        state = "pending review"
        if spec.reviewed is not None:
            state = f"reviewed by {spec.reviewed.by} on {spec.reviewed.date}"
        kind = "held-out" + (f", frozen at {spec.frozen_at}" if spec.frozen_at else "")
        lines += [
            "",
            f"## {spec.name}",
            "",
            f"Match `{key[0]}-{key[1]}` · {kind if spec.heldout else 'labelled'} · {state}",
            "",
            HEADER,
            "|" + "---|" * len(COLUMNS),
        ]
        lines += [_row(case, matches[key]) for case in spec.claims]
    return "\n".join(lines) + "\n"
