"""Keepalive layer 2 (docs/day7-design.md sections 1.2 and 3.5): keeps the public demo awake and
healthy until 10/11, with nobody visiting it. Run every 20 minutes by .github/workflows/watchdog.yml.

One run: read the Space's stage, probe /healthz and /scenarios; a RUNNING Space that fails the
probe twice (60 s apart), or a sleeping / stopped / crashed one, is restarted and waited for (10
minutes at most). A Space paused by hand is never restarted, only reported. Outages open (or
comment on) an issue labelled `demo-down` through the GitHub CLI; a healthy run closes it.

    HF_SPACE=owner/name DEMO_URL=https://owner-name.hf.space HF_TOKEN=... python tools/ops/watchdog.py

Exit 0 when the demo is healthy at the end, 1 otherwise. The token is never printed.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

RESTART_STAGES = frozenset({"SLEEPING", "STOPPED", "RUNTIME_ERROR", "BUILD_ERROR"})
WAIT_STAGES = frozenset({"BUILDING", "APP_STARTING", "RUNNING_BUILDING", "RUNNING_APP_STARTING"})
UP_STAGES = frozenset({"RUNNING", "RUNNING_BUILDING", "RUNNING_APP_STARTING"})  # a deploy: the
# previous container serves until the new one is up
PROBE_TIMEOUT_S = 20.0
RETRY_AFTER_S = 60.0
POLL_EVERY_S = 30.0
RESTART_WAIT_S = 600.0
LABEL = "demo-down"
TITLE = "Demo is down"


class Hub(Protocol):
    def stage(self) -> str: ...
    def restart(self) -> None: ...


@dataclass(frozen=True)
class Probe:
    healthy: bool
    latency_ms: int
    detail: str


Fetch = Callable[[str, float], tuple[int, str]]
Gh = Callable[[Sequence[str]], str]


def fetch(url: str, timeout: float) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, type(exc).__name__


def probe(base: str, get: Fetch = fetch, clock: Callable[[], float] = time.monotonic) -> Probe:
    """Healthy: /healthz is 200 with JSON status "ok", and /scenarios a non-empty JSON list."""
    started = clock()
    status, body = get(f"{base.rstrip('/')}/healthz", PROBE_TIMEOUT_S)
    latency = int((clock() - started) * 1000)
    try:
        health = json.loads(body) if status == 200 else None
    except json.JSONDecodeError:
        health = None
    if not isinstance(health, dict) or health.get("status") != "ok":
        return Probe(False, latency, f"/healthz {status or body or 'no answer'}")
    status, body = get(f"{base.rstrip('/')}/scenarios", PROBE_TIMEOUT_S)
    try:
        scenarios = json.loads(body) if status == 200 else None
    except json.JSONDecodeError:
        scenarios = None
    if not isinstance(scenarios, list) or not scenarios:
        return Probe(False, latency, f"/scenarios {status or 'no answer'}")
    return Probe(True, latency, f"ok, build {health.get('build') or '-'}")


class GitHubIssues:
    """The `demo-down` issue through the GitHub CLI (`gh` reads GH_TOKEN)."""

    def __init__(self, gh: Gh) -> None:
        self.gh = gh

    def open_number(self) -> str | None:
        out = self.gh(["issue", "list", "--label", LABEL, "--state", "open",
                       "--json", "number", "--jq", ".[0].number"])  # fmt: skip
        return out.strip() or None

    def report(self, body: str) -> None:
        number = self.open_number()
        if number is not None:
            self.gh(["issue", "comment", number, "--body", body])
            return
        self.gh(["label", "create", LABEL, "--color", "B60205", "--force",
                 "--description", "The public demo failed the watchdog"])  # fmt: skip
        self.gh(["issue", "create", "--title", TITLE, "--label", LABEL, "--body", body])

    def resolve(self, when: str) -> bool:
        number = self.open_number()
        if number is None:
            return False
        self.gh(["issue", "close", number, "--comment", f"Recovered at {when}"])
        return True


def run(
    hub: Hub,
    base: str,
    issues: GitHubIssues,
    *,
    get: Fetch = fetch,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    out: Callable[[str], None] = print,
) -> tuple[int, str]:
    """One run; returns the exit code and the one-line summary."""
    stamp = now().strftime("%Y-%m-%d %H:%M UTC")
    stage = hub.stage()
    out(f"stage {stage}")
    if stage == "PAUSED":
        issues.report(f"{stamp}: the Space is PAUSED (by hand or by Hugging Face). Not restarted.")
        return 1, f"{stamp} · PAUSED · - · reported, no restart"
    action = "none"
    result = probe(base, get)
    out(f"probe {result.detail} in {result.latency_ms} ms")
    restart = stage in RESTART_STAGES and not result.healthy
    if not result.healthy and not restart:
        sleep(RETRY_AFTER_S)
        result = probe(base, get)
        out(f"probe again: {result.detail} in {result.latency_ms} ms")
        restart = not result.healthy and stage not in WAIT_STAGES
    if restart or (not result.healthy and stage in WAIT_STAGES):
        if restart:
            hub.restart()
            action = "restarted"
            out("restart requested")
        waited = 0.0
        while waited < RESTART_WAIT_S:
            sleep(POLL_EVERY_S)
            waited += POLL_EVERY_S
            stage = hub.stage()
            if stage in UP_STAGES:
                result = probe(base, get)
                if result.healthy:
                    break
        out(f"after {int(waited)} s: stage {stage}, probe {result.detail}")
        if action == "none":
            action = f"waited for {stage}"
    elif result.healthy and stage not in UP_STAGES:
        stage = hub.stage()  # it came up between the two probes
    if stage in UP_STAGES and result.healthy:
        if issues.resolve(stamp):
            action += ", issue closed"
        return 0, f"{stamp} · {stage} · {result.latency_ms} ms · {action}"
    issues.report(f"{stamp}: stage {stage}, probe {result.detail}; action: {action}.")
    return 1, f"{stamp} · {stage} · {result.latency_ms} ms · {action}, issue reported"


class HfHub:
    def __init__(self, space: str, token: str) -> None:
        from huggingface_hub import HfApi

        self.space = space
        self.api = HfApi(token=token)

    def stage(self) -> str:
        return str(self.api.get_space_runtime(self.space).stage)

    def restart(self) -> None:
        self.api.restart_space(self.space)


def gh_cli(args: Sequence[str]) -> str:
    done = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        print(f"gh {args[0]} {args[1]}: exit {done.returncode}", file=sys.stderr)
    return done.stdout


def main() -> int:
    missing = [n for n in ("HF_SPACE", "DEMO_URL", "HF_TOKEN") if not os.environ.get(n)]
    if missing:
        print(f"missing environment: {', '.join(missing)}", file=sys.stderr)
        return 2
    hub = HfHub(os.environ["HF_SPACE"], os.environ["HF_TOKEN"])
    code, line = run(hub, os.environ["DEMO_URL"], GitHubIssues(gh_cli))
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"- watchdog: {line}\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
