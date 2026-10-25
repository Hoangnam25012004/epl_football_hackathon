"""Smoke test of the public demo (docs/day7-design.md section 2.3), standard library only.

Waits until /healthz answers for the expected build (the Space may still serve the previous
container for a while), then checks the page, the scenarios, MCP (9 tools) and one claim through
MCP's `verify_claim` (the rules, no model). Prints a Markdown summary, also to the job summary.

    python tools/ops/smoke_space.py https://owner-name.hf.space --build <commit>
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

MCP_TOOLS = 9
CLAIM = {"match_id": "dominant_loser-42", "text": "Đội nhà áp đảo hiệp 2"}
# the rules' verdict on the fixed seed, held by backend/tests/mcp/test_mcp_transports.py
CLAIM_VERDICT = "TRUE"
CLAIM_MAX_S = 5.0  # the rules answer in well under a second; the rest is the network
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class SmokeError(Exception):
    pass


def fetch(url: str, body: dict[str, Any] | None = None, timeout: float = 30) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=HEADERS if body else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def check_health(body: dict[str, Any], build: str | None) -> list[str]:
    """What is wrong with a /healthz answer (empty when it is the expected, healthy build)."""
    problems = []
    if body.get("status") != "ok":
        problems.append(f"status {body.get('status')!r}")
    if body.get("synthetic") is not True:
        problems.append("synthetic is not true")
    if build is not None and body.get("build") != build:
        problems.append(f"build {body.get('build')!r}, expected {build!r}")
    return problems


def rpc(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        message["params"] = params
    return message


def wait_for_build(base: str, build: str | None, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last = "no answer"
    while True:
        try:
            status, text = fetch(f"{base}/healthz", timeout=15)
            if status == 200:
                body: dict[str, Any] = json.loads(text)
                problems = check_health(body, build)
                if not problems:
                    return body
                last = "; ".join(problems)
            else:
                last = f"HTTP {status}"
        except (OSError, ValueError) as exc:
            last = f"{type(exc).__name__}: {exc}"
        if time.monotonic() > deadline:
            raise SmokeError(f"/healthz after {timeout_s:.0f} s: {last}")
        time.sleep(10)


def smoke(base: str, build: str | None, timeout_s: float) -> list[str]:
    health = wait_for_build(base, build, timeout_s)
    lines = [f"- `/healthz` ok: model `{health.get('model')}` ({health.get('llm_provider')}), "
             f"build `{health.get('build')}`"]  # fmt: skip
    keepalive = health.get("keepalive")
    lines.append(f"- keepalive: {keepalive['url'] if keepalive else 'off'}")

    status, page = fetch(f"{base}/")
    if status != 200 or "<html" not in page.lower():
        raise SmokeError(f"/ answered {status}, not the UI")
    lines.append("- `/` serves the UI")

    status, text = fetch(f"{base}/scenarios")
    scenarios = json.loads(text) if status == 200 else []
    if not scenarios:
        raise SmokeError(f"/scenarios answered {status} with no scenario")
    lines.append(f"- `/scenarios`: {len(scenarios)}")

    status, text = fetch(f"{base}/mcp", rpc("tools/list"))
    tools = json.loads(text).get("result", {}).get("tools", []) if status == 200 else []
    if len(tools) != MCP_TOOLS:
        raise SmokeError(f"MCP tools/list answered {status} with {len(tools)} tools")
    lines.append(f"- MCP `tools/list`: {len(tools)} tools")

    began = time.monotonic()
    status, text = fetch(
        f"{base}/mcp", rpc("tools/call", {"name": "verify_claim", "arguments": CLAIM})
    )
    took = time.monotonic() - began
    content = (
        json.loads(text).get("result", {}).get("structuredContent", {}) if status == 200 else {}
    )
    if content.get("verdict") != CLAIM_VERDICT or took > CLAIM_MAX_S:
        raise SmokeError(
            f"verify_claim answered {status}, {content.get('verdict')!r} in {took:.1f} s"
        )
    lines.append(f'- claim "{CLAIM["text"]}": {CLAIM_VERDICT} in {took:.2f} s (rules, via MCP)')
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("url", help="the public URL of the demo")
    parser.add_argument("--build", help="the commit /healthz must name")
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds to wait for it")
    args = parser.parse_args(argv)
    base = args.url.rstrip("/")
    try:
        lines = smoke(base, args.build, args.timeout)
    except SmokeError as exc:
        print(f"smoke_space: {exc}", file=sys.stderr)
        return 1
    report = "\n".join([f"### Smoke test of {base}", "", *lines, ""])
    if hasattr(sys.stdout, "reconfigure"):  # the claim is Vietnamese; a Windows console is not
        sys.stdout.reconfigure(encoding="utf-8")
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write(report + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
