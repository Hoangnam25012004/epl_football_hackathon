"""CLI smoke tests: commands run, write files, and use the right exit codes."""

from pathlib import Path

import pytest

from pundit.cli import main


def test_generate_validate_and_stats(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(["generate", "--scenario", "comeback_var", "--seed", "1", "--out", str(tmp_path)]) == 0
    )
    events = tmp_path / "comeback_var-1.events.jsonl"
    assert events.exists() and (tmp_path / "comeback_var-1.meta.json").exists()
    assert b"\r\n" not in events.read_bytes()
    assert main(["validate", "--file", str(events)]) == 0
    assert main(["stats", "--file", str(events), "--window", "H2"]) == 0
    out = capsys.readouterr().out
    assert "possession" in out and "IRO" in out and "synthetic" in out


def test_validate_rejects_tampered_data(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["generate", "--scenario", "balanced", "--seed", "2", "--out", str(tmp_path)])
    events = tmp_path / "balanced-2.events.jsonl"
    lines = events.read_bytes().splitlines(keepends=True)
    del lines[40]  # drop one event: the sequence breaks
    events.write_bytes(b"".join(lines))
    assert main(["validate", "--file", str(events)]) == 1
    assert "INV-01" in capsys.readouterr().err


def test_replay_prints_events_and_status(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "replay",
            "--scenario",
            "dominant_loser",
            "--seed",
            "42",
            "--speed",
            "100000",
            "--status-every",
            "1",
            "--compact",
            "--max-events",
            "40",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out.splitlines()
    assert any("KICK-OFF" in line for line in out)
    assert any("POSSESSION HAR" in line for line in out)


def test_schema_and_scenarios(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["schema", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "event.schema.json").exists()
    assert main(["scenarios"]) == 0
    assert "dominant_loser" in capsys.readouterr().out


def test_bad_arguments_exit_with_2() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["generate", "--scenario", "nope"])
    assert exit_info.value.code == 2
