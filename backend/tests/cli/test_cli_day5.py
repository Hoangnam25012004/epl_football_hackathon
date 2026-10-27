"""Day 5 CLI commands (docs/day5-design.md section 9.5): speak, tts warm, recap --audio."""

from pathlib import Path

import pytest

from pundit import cli
from pundit.cli import main
from pundit.speech.audio import parse_wav


@pytest.fixture(autouse=True)
def mock_voice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Neither the environment nor backend/.env may switch these tests to Azure."""
    monkeypatch.setattr(cli, "ENV_FILE", tmp_path / "absent.env")
    names = ("USE_MOCK_LLM", "LLM_PROVIDER", "MOCK_LLM_FAULTS", "MOCK_TTS_FAULTS", "SPEECH_KEY",
             "SPEECH_REGION", "SPEECH_ENDPOINT", "SPEECH_RESOURCE_ID")  # fmt: skip
    for name in names:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("USE_MOCK_LLM", "true")
    monkeypatch.setenv("TTS_PROVIDER", "mock")
    monkeypatch.setenv("MOCK_TTS_TIME_SCALE", "0.01")
    monkeypatch.setenv("AUDIO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("AUDIO_STORE_DIR", str(tmp_path / "store"))


def test_speak_prints_the_spoken_words_and_ssml(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["speak", "--text", "VÀOOO! J. Ellery ghi bàn, Harbor Lions FC 1-2 KES.",
                 "--preset", "peak", "--scenario", "dominant_loser", "--ssml-only"])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0
    assert "Vào! Ellery ghi bàn, Harbor Lions một, Kestrel Athletic hai." in out
    assert '<prosody rate="+25%" pitch="+14%">' in out and "guard   passed" in out  # peak


def test_speak_writes_audio_and_refuses_unreadable_text(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out = tmp_path / "line.wav"
    assert main(["speak", "--text", "Phút 63, bàn thắng!", "--out", str(out)]) == 0
    assert parse_wav(out.read_bytes()).duration_ms > 0
    assert main(["speak", "--text", "Mã 1.2.3"]) == 1
    assert "GUARD   NUMBERS" in capsys.readouterr().out


def test_tts_warm_makes_every_clip(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code = main(["tts", "warm", "--scenario", "balanced", "--seed", "3", "--voices", "female",
                 "--modes", "casual"])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0 and "0 not made" in out and "store local" in out
    stored = list((tmp_path / "store").glob("*.wav"))
    assert len(stored) == 3  # the greeting and the two recaps (mock lines are cache only)
    assert main(["tts", "warm", "--scenario", "balanced", "--voices", "robot"]) == 2


def test_tts_warm_needs_server_audio(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TTS_PROVIDER", "browser")
    assert main(["tts", "warm", "--scenario", "balanced"]) == 2
    assert "needs server audio" in capsys.readouterr().out


def test_recap_audio(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    out = tmp_path / "recap.wav"
    args = ["recap", "--scenario", "dominant_loser", "--seed", "42", "--scope", "H1", "--lang",
            "vi", "--mode", "casual", "--audio", str(out), "--voice", "male"]  # fmt: skip
    code = main(args)
    assert code == 0 and parse_wav(out.read_bytes()).duration_ms > 0
    assert "voice male" in capsys.readouterr().out
