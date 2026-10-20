"""Day 2 CLI commands: claim, moments, eval (serve is covered by the API tests)."""

import json
from pathlib import Path

import pytest

from pundit import cli
from pundit.cli import EVAL_FILES, main


@pytest.fixture(autouse=True)
def mock_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The CLI reads the environment and backend/.env: neither may switch these tests to a real
    LLM (no network, no token)."""
    monkeypatch.setattr(cli, "ENV_FILE", tmp_path / "absent.env")
    for name in (
        "USE_MOCK_LLM",
        "LLM_PROVIDER",
        "MOCK_LLM_FAULTS",
        "LLM_TIMEOUT_S",
        "CLAIM_BUDGET_S",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("USE_MOCK_LLM", "true")


def test_claim_prints_the_verdict_and_trace(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "claim",
            "Đội nhà áp đảo hiệp 2",
            "--scenario",
            "dominant_loser",
            "--seed",
            "42",
            "--trace",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "VERDICT   TRUE" in out and "61%" in out and "2.24" in out and "0.91" in out
    assert "TRACE" in out and "EvidenceCollector" in out and "synthetic data" in out


def test_claim_at_a_minute_and_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "claim",
            "HAR áp đảo hiệp 2",
            "--scenario",
            "dominant_loser",
            "--seed",
            "42",
            "--at",
            "30'",
            "--json",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["verdict"] == "INSUFFICIENT_DATA"
    assert result["reason_code"] == "future_window" and result["trace"] is None


def test_injected_hallucination_ends_in_the_template(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MOCK_LLM_FAULTS", "explainer:hallucinate:2")
    main(
        [
            "claim",
            "Đội nhà áp đảo hiệp 2",
            "--scenario",
            "dominant_loser",
            "--seed",
            "42",
            "--trace",
        ]
    )
    out = capsys.readouterr().out
    assert "rejected" in out and "explainer_fallback" in out and "explanation template" in out


def test_bad_configuration_exits_with_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("USE_MOCK_LLM", "false")
    code = main(["claim", "Đội nhà áp đảo hiệp 2", "--scenario", "dominant_loser", "--seed", "42"])
    assert code == 2 and "LLM_PROVIDER" in capsys.readouterr().err


def test_moments(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(["moments", "--scenario", "comeback_var", "--seed", "7", "--min-importance", "3"]) == 0
    )
    out = capsys.readouterr().out
    assert "goal_overturned" in out and "goal " in out and "substitution" not in out
    assert main(["moments", "--scenario", "comeback_var", "--seed", "7", "--json"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert all(json.loads(line)["match_id"] == "comeback_var-7" for line in lines)


def test_eval(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["eval"]) == 0
    out = capsys.readouterr().out
    assert "accuracy (labelled set): 100%" in out and "wrong verdicts (all sets): 0" in out
    assert out.strip().endswith("PASSED")


def test_eval_fails_on_a_wrong_label(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = json.loads(EVAL_FILES[0].read_text(encoding="utf-8"))
    data["claims"] = data["claims"][:1]
    data["claims"][0]["expected"] = "FALSE"  # the claim is TRUE: the mock must report a WRONG
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert main(["eval", "--file", str(broken)]) == 1
    assert "FAILED" in capsys.readouterr().out
