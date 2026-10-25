"""Get the model ready as soon as Ollama answers (docs/day7-design.md section 2.2): pin its CPU
threads to the container's CPU quota, then load it into memory so the first claim does not pay
for it. Runs in the background of start.sh; failing only logs.

Why the threads: llama.cpp counts the host's cores (16 on the build machine), not the 2 vCPUs
the container may use, and 8 threads on 2 CPUs read a prompt at 10 tokens/s instead of ~100.
Ollama has no variable for it, so the model gets a `num_thread` parameter under the same name.
"""

import json
import math
import os
import sys
import time
import urllib.request
from pathlib import Path

OLLAMA = "http://127.0.0.1:11434"
READY_TIMEOUT_S = 120
CPU_MAX = Path("/sys/fs/cgroup/cpu.max")  # "200000 100000" is a quota of 2 CPUs


def log(message: str) -> None:
    print(f"warm_model: {message}", file=sys.stderr, flush=True)


def cpu_quota(cpu_max: str | None, cores: int) -> int:
    """CPUs the container may use: its cgroup quota, else every core."""
    if cpu_max:
        quota, _, period = cpu_max.strip().partition(" ")
        if quota != "max" and period:
            return max(1, min(cores, math.ceil(int(quota) / int(period))))
    return max(1, cores)


def threads() -> int:
    if value := os.environ.get("OLLAMA_NUM_THREAD"):
        return max(1, int(value))
    cpu_max = CPU_MAX.read_text() if CPU_MAX.is_file() else None
    return cpu_quota(cpu_max, os.cpu_count() or 1)


def post(path: str, body: dict[str, object], timeout: float) -> dict[str, object]:
    request = urllib.request.Request(
        f"{OLLAMA}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        answer: dict[str, object] = json.loads(response.read() or b"{}")
        return answer


def main(model: str) -> int:
    deadline = time.monotonic() + READY_TIMEOUT_S
    while True:
        try:
            urllib.request.urlopen(f"{OLLAMA}/api/version", timeout=2).close()
            break
        except OSError:
            if time.monotonic() > deadline:
                log(f"Ollama did not answer in {READY_TIMEOUT_S} s")
                return 1
            time.sleep(1)
    count = threads()
    try:
        post("/api/create", {"model": model, "from": model, "parameters": {"num_thread": count},
                             "stream": False}, timeout=120)  # fmt: skip
        log(f"{model} uses {count} CPU threads")
    except OSError as exc:  # still served, only slower
        log(f"setting {count} threads on {model} failed: {exc}")
    began = time.monotonic()
    try:  # an empty prompt only loads the model; keep_alive -1 keeps it in memory
        post("/api/generate", {"model": model, "prompt": "", "keep_alive": -1}, timeout=300)
    except OSError as exc:
        log(f"loading {model} failed: {exc}")
        return 1
    log(f"{model} loaded in {time.monotonic() - began:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
