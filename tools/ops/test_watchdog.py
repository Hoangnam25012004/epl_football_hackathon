"""The watchdog (docs/day7-design.md section 3.5) without the network: a fake Hub, fake HTTP and a
fake GitHub CLI."""

import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import watchdog
from watchdog import GitHubIssues, probe, run

BASE = "https://owner-name.hf.space"
HEALTHY = json.dumps({"status": "ok", "build": "abc1234"})
SCENARIOS = json.dumps([{"key": "balanced"}])
TOKEN = "hf_" + "fake" * 6  # built, so the secret scanner sees no literal token


class FakeHub:
    def __init__(self, stages: Sequence[str]) -> None:
        self.stages = list(stages)
        self.restarts = 0

    def stage(self) -> str:
        return self.stages.pop(0) if len(self.stages) > 1 else self.stages[0]

    def restart(self) -> None:
        self.restarts += 1


class FakeWeb:
    """Answers of /healthz in order (the last one repeats); /scenarios is always fine."""

    def __init__(self, health: Sequence[tuple[int, str]]) -> None:
        self.health = list(health)

    def __call__(self, url: str, timeout: float) -> tuple[int, str]:
        if url.endswith("/scenarios"):
            return 200, SCENARIOS
        return self.health.pop(0) if len(self.health) > 1 else self.health[0]


class FakeGh:
    def __init__(self, open_issue: str = "") -> None:
        self.open_issue = open_issue
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> str:
        self.calls.append(list(args))
        if args[:2] == ["issue", "list"]:
            return self.open_issue
        return ""

    def did(self, *verb: str) -> bool:
        return any(call[: len(verb)] == list(verb) for call in self.calls)


def go(hub: FakeHub, web: FakeWeb, gh: FakeGh) -> tuple[int, str, list[float]]:
    slept: list[float] = []
    code, line = run(
        hub, BASE, GitHubIssues(gh), get=web, sleep=slept.append,
        now=lambda: datetime(2026, 10, 28, 3, 0, tzinfo=UTC), out=lambda _: None,
    )  # fmt: skip
    return code, line, slept


def test_healthy_does_nothing() -> None:
    hub, gh = FakeHub(["RUNNING"]), FakeGh()
    code, line, slept = go(hub, FakeWeb([(200, HEALTHY)]), gh)
    assert (code, hub.restarts, slept) == (0, 0, [])
    assert not gh.did("issue", "create") and "RUNNING" in line


def test_sleeping_is_restarted_then_healthy() -> None:
    hub, gh = FakeHub(["SLEEPING", "APP_STARTING", "RUNNING"]), FakeGh()
    code, line, _ = go(hub, FakeWeb([(0, "URLError"), (0, "URLError"), (200, HEALTHY)]), gh)
    assert (code, hub.restarts) == (0, 1) and "restarted" in line
    assert not gh.did("issue", "create")


def test_runtime_error_is_restarted() -> None:
    hub = FakeHub(["RUNTIME_ERROR", "RUNNING"])
    code, _, _ = go(hub, FakeWeb([(503, ""), (200, HEALTHY)]), FakeGh())
    assert (code, hub.restarts) == (0, 1)


def test_one_failed_probe_is_retried_without_a_restart() -> None:
    hub = FakeHub(["RUNNING"])
    code, _, slept = go(hub, FakeWeb([(502, ""), (200, HEALTHY)]), FakeGh())
    assert (code, hub.restarts, slept) == (0, 0, [watchdog.RETRY_AFTER_S])


def test_two_failed_probes_restart_and_a_dead_restart_opens_an_issue() -> None:
    hub, gh = FakeHub(["RUNNING"]), FakeGh()
    code, line, slept = go(hub, FakeWeb([(502, "")]), gh)
    assert (code, hub.restarts) == (1, 1) and "issue reported" in line
    assert sum(slept) == watchdog.RETRY_AFTER_S + watchdog.RESTART_WAIT_S
    assert gh.did("label", "create") and gh.did("issue", "create")


def test_an_open_issue_gets_a_comment_not_a_second_issue() -> None:
    gh = FakeGh(open_issue="12")
    code, _, _ = go(FakeHub(["RUNNING"]), FakeWeb([(502, "")]), gh)
    assert code == 1 and gh.did("issue", "comment", "12") and not gh.did("issue", "create")


def test_healthy_again_closes_the_open_issue() -> None:
    gh = FakeGh(open_issue="12")
    code, line, _ = go(FakeHub(["RUNNING"]), FakeWeb([(200, HEALTHY)]), gh)
    assert code == 0 and gh.did("issue", "close", "12") and "issue closed" in line


def test_paused_is_reported_never_restarted() -> None:
    hub, gh = FakeHub(["PAUSED"]), FakeGh()
    code, _, _ = go(hub, FakeWeb([(200, HEALTHY)]), gh)
    assert (code, hub.restarts) == (1, 0) and gh.did("issue", "create")


def test_a_deploy_in_progress_is_healthy_while_the_old_container_serves() -> None:
    hub = FakeHub(["RUNNING_APP_STARTING"])
    code, _, _ = go(hub, FakeWeb([(200, HEALTHY)]), FakeGh())
    assert (code, hub.restarts) == (0, 0)


def test_a_starting_space_is_waited_for_not_restarted() -> None:
    hub = FakeHub(["APP_STARTING", "APP_STARTING", "RUNNING"])
    code, _, _ = go(hub, FakeWeb([(0, "URLError"), (0, "URLError"), (200, HEALTHY)]), FakeGh())
    assert (code, hub.restarts) == (0, 0)


@pytest.mark.parametrize(
    ("health", "scenarios", "healthy"),
    [
        ((200, HEALTHY), (200, SCENARIOS), True),
        ((200, json.dumps({"status": "degraded"})), (200, SCENARIOS), False),
        ((200, "<html>sleeping</html>"), (200, SCENARIOS), False),
        ((200, HEALTHY), (200, "[]"), False),
        ((404, ""), (200, SCENARIOS), False),
    ],
)
def test_the_probe(health: tuple[int, str], scenarios: tuple[int, str], healthy: bool) -> None:
    def get(url: str, timeout: float) -> tuple[int, str]:
        return scenarios if url.endswith("/scenarios") else health

    assert probe(BASE, get).healthy is healthy


def test_the_token_is_never_printed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("HF_SPACE", "owner/name")
    monkeypatch.setenv("DEMO_URL", BASE)
    monkeypatch.setenv("HF_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(watchdog, "HfHub", lambda space, token: FakeHub(["RUNNING"]))
    monkeypatch.setattr(watchdog, "fetch", FakeWeb([(200, HEALTHY)]))
    monkeypatch.setattr(watchdog, "gh_cli", FakeGh())
    monkeypatch.setattr(watchdog, "run", _run_with_fakes)
    assert watchdog.main() == 0
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err + summary.read_text(encoding="utf-8")
    assert "watchdog:" in summary.read_text(encoding="utf-8")


_real_run = run


def _run_with_fakes(hub: watchdog.Hub, base: str, issues: GitHubIssues) -> tuple[int, str]:
    return _real_run(hub, base, issues, get=watchdog.fetch, sleep=lambda _: None)


def test_missing_settings_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HF_SPACE", "DEMO_URL", "HF_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert watchdog.main() == 2
