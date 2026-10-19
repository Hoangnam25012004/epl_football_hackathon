"""Same scenario + seed => same bytes, across runs and processes (design section 12.4, D1-D4)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pundit.engine.generator import generate
from pundit.engine.io import events_to_jsonl, match_hash


def test_same_seed_same_bytes() -> None:  # D1
    first = generate("comeback_var", 3)
    second = generate("comeback_var", 3)
    assert events_to_jsonl(first.events) == events_to_jsonl(second.events)
    assert first.meta == second.meta


@pytest.mark.parametrize("hash_seed", ["0", "1", "12345"])
def test_same_bytes_in_other_processes(hash_seed: str, tmp_path: Path) -> None:  # D2
    expected = match_hash(generate("dominant_loser", 42).events)
    env = {**os.environ, "PYTHONHASHSEED": hash_seed}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pundit",
            "generate",
            "--scenario",
            "dominant_loser",
            "--seed",
            "42",
            "--out",
            str(tmp_path),
            "--print-hash",
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert f"sha256 {expected}" in result.stdout


def test_different_seeds_differ() -> None:  # D3
    hashes = {match_hash(generate("balanced", seed).events) for seed in range(4)}
    assert len(hashes) == 4


def test_physical_snapshots_do_not_change_the_rest() -> None:  # D4
    with_physical = generate("red_card_swing", 5, physical=True)
    without = generate("red_card_swing", 5, physical=False)

    def comparable(events: tuple) -> list[dict]:  # type: ignore[type-arg]
        return [
            e.model_dump(exclude={"id", "seq", "related_ids"})
            for e in events
            if e.type != "player_physical"
        ]

    assert comparable(with_physical.events) == comparable(without.events)
    assert any(e.type == "player_physical" for e in with_physical.events)
    assert not any(e.type == "player_physical" for e in without.events)
