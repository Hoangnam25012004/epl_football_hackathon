"""The Day 6 eval files themselves (docs/day6-design.md 4.2-4.3): they load, they have the
designed size and mix, every number a label was written from is still the Stats Tools' (and the
independent count's), and a held-out file has not changed since the commit that froze it."""

import json
import subprocess
from collections import Counter
from functools import cache
from pathlib import Path

import pytest
from helpers import generated

from pundit.orchestration.eval_checks import CheckKey, check_value
from pundit.orchestration.evaluation import (
    CATEGORIES,
    EvalCase,
    EvalFile,
    load_eval,
    seq_at,
)
from pundit.orchestration.label_sheet import label_sheet

EVALS = Path(__file__).resolve().parents[2] / "evals"
PATHS = sorted(EVALS.glob("*.json"))
FILES = {path.name: load_eval(path) for path in PATHS}
DAY6 = {name: f for name, f in FILES.items() if any(c.category for c in f.claims)}
NEW = [case for f in DAY6.values() for case in f.claims]
REFUSED = {
    "unsupported": "unsupported_claim",
    "entity": "unknown_entity",
    "future": "future_window",
}


@cache
def checks() -> list[tuple[str, str, EvalCase, str]]:
    """(file, check key, case, display written) for every check of every file."""
    return [
        (name, key, case, written)
        for name, spec in FILES.items()
        for case in spec.claims
        for key, written in case.checks.items()
    ]


def test_one_hundred_claims_on_three_matches() -> None:
    labelled = [c for f in FILES.values() if not f.heldout for c in f.claims]
    heldout = [c for f in FILES.values() if f.heldout for c in f.claims]
    assert (len(labelled), len(heldout)) == (72, 28)
    ids = [c.id for f in FILES.values() for c in f.claims]
    assert len(ids) == len(set(ids)) == 100
    matches = {(f.match.scenario, f.match.seed) for f in FILES.values()}
    assert matches == {("dominant_loser", 42), ("comeback_var", 7), ("red_card_swing", 7)}


def test_the_new_claims_have_the_designed_mix() -> None:
    assert len(NEW) == 70
    assert {c.category for c in NEW} == set(CATEGORIES)  # every category is used
    tags = Counter(tag for c in NEW for tag in c.tags)
    windowed = sum(any(t.startswith("window:") for t in c.tags) for c in NEW)
    assert windowed >= 25
    assert tags["as_of"] >= 8 and tags["comparative"] >= 8 and tags["negation"] >= 4
    verdicts = Counter(c.expected for c in NEW)
    assert min(verdicts.values()) >= 5  # every verdict is represented
    vietnamese = sum(any(ch in c.text for ch in "ăâđêôơưàáạảãèéẹẻẽ") for c in NEW)
    assert 30 <= vietnamese <= 45


def test_a_claim_at_a_minute_is_tagged_so() -> None:
    for case in NEW:
        assert (case.at is not None) == ("as_of" in case.tags), case.id


def test_refusals_say_why_and_injections_are_never_true() -> None:
    for case in NEW:
        if case.category in REFUSED:
            assert case.expected == "INSUFFICIENT_DATA", case.id
            assert case.expect_reason == REFUSED[case.category], case.id
        if case.category == "injection":
            assert case.expected == "FALSE", case.id
        if case.expected != "INSUFFICIENT_DATA" and case.category not in ("narrative",):
            assert case.checks, f"{case.id}: a label needs the numbers it was written from"


def test_every_check_is_still_the_stats_tools_number() -> None:
    """Drift detection: a change in the generated data or the metrics fails here, by key."""
    drift: list[str] = []
    for name, key, case, written in checks():
        spec = FILES[name]
        match = generated(spec.match.scenario, spec.match.seed)
        seq = seq_at(match.events, case.at)
        events = match.events if seq is None else match.events[: seq + 1]
        value = check_value(match.meta, events, key)
        if value.display != written:
            drift.append(f"{case.id} {key}: written {written}, now {value.display}")
        if value.counted is not None and value.counted != value.result.value:
            drift.append(f"{case.id} {key}: counted {value.counted}, Stats Tools {value.display}")
    assert drift == []
    assert len(checks()) > 150


@pytest.mark.parametrize(
    "key",
    ["possession", "possession:harbor_lions", "nope:harbor_lions:H2", "xg:harbor_lions:H3"],
)
def test_bad_check_keys(key: str) -> None:
    with pytest.raises(ValueError):
        CheckKey.parse(key)


def test_check_keys() -> None:
    assert CheckKey.parse("shots:harbor_lions-09:FULL") == CheckKey(
        "shots", "harbor_lions", "harbor_lions-09", "FULL"
    )
    assert CheckKey.parse("player_top_speed:harbor_lions-09:H2").team is None
    assert CheckKey.parse("chaos_index:-:H1") == CheckKey("chaos_index", None, None, "H1")
    assert CheckKey.parse("possession:kestrel_athletic:last15").window == "last15"


@pytest.mark.parametrize(
    "case",
    [
        {"id": "x", "text": "abc", "expected": "TRUE", "category": "gossip"},
        {"id": "x", "text": "abc", "expected": "TRUE", "tags": ["window:H3"]},
        {"id": "x", "text": "abc", "expected": "TRUE", "checks": {"xg:harbor_lions": "1"}},
    ],
)
def test_a_case_is_checked_when_loaded(case: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        EvalCase.model_validate(case)


def test_duplicate_ids_are_refused() -> None:
    claims = [{"id": "x", "text": "abc", "expected": "TRUE"}] * 2
    with pytest.raises(ValueError, match="duplicate"):
        EvalFile.model_validate(
            {"name": "n", "match": {"scenario": "balanced", "seed": 1}, "claims": claims}
        )


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", *args], cwd=EVALS, capture_output=True, text=True, encoding="utf-8", timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


@pytest.mark.parametrize("name", sorted(n for n, f in DAY6.items() if f.heldout))
def test_a_held_out_file_is_frozen(name: str) -> None:
    """Committed before any Day 6 parser change; only the review may be added since."""
    spec = DAY6[name]
    assert spec.frozen_at, f"{name} has no frozen_at commit"
    then = _git("show", f"{spec.frozen_at}:backend/evals/{name}")
    if then is None:
        pytest.skip("the freezing commit is not in this clone (shallow checkout)")
    now = json.loads((EVALS / name).read_text(encoding="utf-8"))
    before = json.loads(then)
    for document in (now, before):
        for field in ("frozen_at", "reviewed"):
            document.pop(field, None)
    assert now == before


def test_the_label_sheet() -> None:
    sheet = label_sheet(list(DAY6.values()))
    rows = [line for line in sheet.splitlines() if line.startswith("| ") and "---" not in line]
    assert len(rows) == 70 + len(DAY6)  # one header per file
    assert "Synthetic data" in sheet
    assert "≠ Stats Tools" not in sheet and "label written from" not in sheet
    assert "| c16 | Ironvale áp đảo hiệp 2 | FT |" in sheet
    assert label_sheet(list(DAY6.values())) == sheet  # deterministic
