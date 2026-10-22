"""Day 3 CLI commands: studio and recap."""

import json
from pathlib import Path

import pytest

from pundit import cli
from pundit.cli import main


@pytest.fixture(autouse=True)
def mock_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Neither the environment nor backend/.env may switch these tests to a real LLM."""
    monkeypatch.setattr(cli, "ENV_FILE", tmp_path / "absent.env")
    for name in ("USE_MOCK_LLM", "LLM_PROVIDER", "MOCK_LLM_FAULTS", "LLM_TIMEOUT_S"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("USE_MOCK_LLM", "true")


def test_studio_report_passes(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    out = tmp_path / "studio.jsonl"
    code = main(
        [
            "studio",
            "--scenario",
            "balanced",
            "--seed",
            "3",
            "--report",
            "--quiet",
            "--json",
            str(out),
        ]
    )
    printed = capsys.readouterr().out
    assert code == 0 and "PASSED" in printed and "studio report balanced-3" in printed
    kinds = [json.loads(line)["type"] for line in out.read_text(encoding="utf-8").splitlines()]
    assert {"commentary", "talking_point", "lower_third", "recap", "trace"} <= set(kinds)


def test_studio_prints_the_profile_view(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "studio",
            "--scenario",
            "dominant_loser",
            "--seed",
            "42",
            "--profile",
            "mode=casual,lang=vi,club=HAR,beginner=1",
        ]
    )
    printed = capsys.readouterr().out
    assert code == 0
    assert "casual|vi|harbor_lions|-|1|-" in printed
    assert "framing: Đội của bạn" in printed and "rule (full): Việt vị" in printed
    assert "RECAP FULL revision 2" in printed


def test_studio_refuses_a_bad_profile(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["studio", "--scenario", "balanced", "--seed", "3", "--profile", "club=Nowhere"])
    assert code == 2 and "bad --profile" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["studio", "--scenario", "balanced", "--profile", "mode"])  # not key=value


def test_studio_report_with_injected_faults(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOCK_LLM_FAULTS", "commentator:wrong_outcome:1,talker:hallucinate")
    code = main(["studio", "--scenario", "dominant_loser", "--seed", "42", "--report", "--quiet"])
    printed = capsys.readouterr().out
    assert code == 0 and "WRONG_OUTCOME" in printed and "leaks 0" in printed


def test_recap_command(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        ["recap", "--scenario", "comeback_var", "--seed", "7", "--lang", "en", "--mode", "casual",
         "--trace"]
    )  # fmt: skip
    printed = capsys.readouterr().out
    assert code == 0 and "revision 2 [llm]" in printed and "TRACE" in printed
    code = main(["recap", "--scenario", "balanced", "--seed", "3", "--scope", "H1"])
    assert code == 0 and "recap H1" in capsys.readouterr().out
