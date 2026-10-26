"""Measure the public demo (docs/day7-design.md section 2.10, C10) over HTTP only.

`eval`: the 100 claims of backend/evals on the Space, as a judge sends them: POST /claims/verify
(rev.1 from the rules), then GET /claims/{id} until rev.2 (the Space's model). Each claim is
written as soon as it is done, in the format of `pundit eval --out`, so
`pundit eval --from-runs <file>` scores it like every other column of docs/eval.md (the final
answer, rev.2). The as_of seq of a claim is computed here from the same seed the Space uses.
`--resume` keeps what the file holds. A session the Space removed (404) is made again.
Claims sent one after another trip the claim breaker after a few model timeouts, and the
rules answer the rest (what a judge's burst sees); `--wait-model` waits for the breaker to
close before each claim instead, so the column measures the model itself.

`load`: N viewers send a claim at the same moment, R rounds: rev.1 latency and how often the
model pass was skipped. `studio`: a match at 20x, the studio's event -> talking point lag (p95
of /healthz) while it plays.

    python tools/ops/measure_space.py eval https://owner-name.hf.space --out run.jsonl --resume
    python tools/ops/measure_space.py load https://owner-name.hf.space --viewers 3 --rounds 5
    python tools/ops/measure_space.py studio https://owner-name.hf.space --minutes 3
"""

import argparse
import concurrent.futures
import datetime
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from pundit.domain.percentiles import p95
from pundit.engine.generator import generate
from pundit.orchestration.evaluation import load_eval, seq_at

ROOT = Path(__file__).resolve().parents[2]
EVALS = ROOT / "backend" / "evals"
REV2_TIMEOUT_S = 150.0  # a model pass waits its turn (one worker), then has 60 s
CLAIM = "Đội nhà áp đảo hiệp 2"


def p50(values: list[float]) -> float | None:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else None


def summary(values: list[float]) -> str:
    if not values:
        return "-"
    return f"p50 {p50(values):.0f} ms, p95 {p95(values) or 0:.0f} ms (n={len(values)})"


class Space:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.http = httpx.Client(timeout=60.0)

    def session(self, scenario: str, seed: int) -> str:
        """The shared session of this match, replayed to the end."""
        body = {"scenario": scenario, "seed": seed, "speed": 1000, "autostart": True}
        response = self.http.post(f"{self.base}/matches", json=body)
        response.raise_for_status()
        match_id: str = response.json()["match_id"]
        deadline = time.monotonic() + 300
        while True:
            info = self.http.get(f"{self.base}/matches/{match_id}").json()["info"]
            if info["status"] == "finished":
                return match_id
            if info["status"] in ("created", "paused", "stopped"):
                self.http.post(f"{self.base}/matches/{match_id}/start")
            if time.monotonic() > deadline:
                raise RuntimeError(f"{match_id} did not finish: {info['status']}")
            time.sleep(1)

    def verify(
        self, match_id: str, text: str, as_of_seq: int | None
    ) -> tuple[dict[str, Any], float]:
        body: dict[str, Any] = {"match_id": match_id, "text": text}
        if as_of_seq is not None:
            body["as_of_seq"] = as_of_seq
        began = time.monotonic()
        response = self.http.post(f"{self.base}/claims/verify", json=body)
        took = (time.monotonic() - began) * 1000
        response.raise_for_status()
        rev1: dict[str, Any] = response.json()
        return rev1, took

    def breaker_closed(self, timeout_s: float = 300.0) -> None:
        """Until the claim breaker is no longer open (its cooldown over: the next pass is the
        trial). Waiting here measures the model; a judge's burst would see the rules instead."""
        deadline = time.monotonic() + timeout_s
        while "circuit_open" in self.http.get(f"{self.base}/healthz").json()["degraded"]:
            if time.monotonic() > deadline:
                raise RuntimeError("the claim breaker stayed open")
            time.sleep(5)

    def final(self, rev1: dict[str, Any]) -> dict[str, Any]:
        """rev.2, or rev.1 when no model pass will come."""
        if rev1.get("model_pass") in (None, "skipped") and rev1.get("revision", 1) == 1:
            return rev1
        deadline = time.monotonic() + REV2_TIMEOUT_S
        while time.monotonic() < deadline:
            response = self.http.get(f"{self.base}/claims/{rev1['claim_id']}")
            response.raise_for_status()  # 404: the session was removed; the caller makes it again
            claim: dict[str, Any] = response.json()
            if claim.get("revision") == 2:
                return claim
            time.sleep(1)
        raise RuntimeError(f"no rev.2 for {rev1['claim_id']} in {REV2_TIMEOUT_S:.0f} s")


def done_cases(out: Path) -> set[tuple[str, str]]:
    if not out.is_file():
        return set()
    done = set()
    for raw in out.read_text(encoding="utf-8").splitlines():
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            continue
        done.add((line["file"], line["case"]))
    return done


def run_eval(space: Space, out: Path, resume: bool, wait_model: bool = False) -> int:
    health = space.http.get(f"{space.base}/healthz").json()
    info = {
        "provider": f"{health['llm_provider']} (Space)",
        "model": health["model"],
        "commit": health.get("build", ""),
        "date": datetime.date.today().isoformat(),
    }
    if not resume:
        out.write_text("", encoding="utf-8")
    done = done_cases(out)
    timings = out.with_suffix(".timings.jsonl")
    sessions: dict[tuple[str, int], str] = {}
    for path in sorted(EVALS.glob("*.json")):
        spec = load_eval(path)
        key = (spec.match.scenario, spec.match.seed)
        events = generate(*key).events
        for case in spec.claims:
            if (spec.name, case.id) in done:
                continue
            for attempt in (1, 2):
                if key not in sessions:
                    sessions[key] = space.session(*key)
                try:
                    rev1, took = space.verify(sessions[key], case.text, seq_at(events, case.at))
                    while wait_model and rev1.get("skip_reason") == "circuit_open":
                        space.breaker_closed()  # the model gets its chance on every claim
                        rev1, took = space.verify(sessions[key], case.text, seq_at(events, case.at))
                    final = space.final(rev1)
                    break
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 404 or attempt == 2:
                        raise
                    sessions.pop(key)  # removed by the Space's janitor: made again
            final["trace"] = None
            line = {"file": spec.name, "case": case.id, **info, "result": final}
            with out.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
            timing = {
                "file": spec.name, "case": case.id, "rev1_ms": round(took, 1),
                "rev1_verdict": rev1["verdict"], "verdict": final["verdict"],
                "model_pass": final.get("model_pass"), "skip_reason": final.get("skip_reason"),
                "model_pass_ms": final.get("timings_ms", {}).get("model_pass"),
                "explanation_source": final["explanation_source"],
                "analyst_source": final["analyst_source"],
            }  # fmt: skip
            with timings.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(timing, ensure_ascii=False) + "\n")
            outcome = f"{final.get('model_pass')} {final.get('skip_reason') or ''}"
            print(f"{spec.name} {case.id} {final['verdict']:<17} rev1 {took:.0f} ms {outcome}")
            sys.stdout.flush()
    return report_timings(timings)


def report_timings(timings: Path) -> int:
    rows = [json.loads(r) for r in timings.read_text(encoding="utf-8").splitlines() if r.strip()]
    rev1 = [r["rev1_ms"] for r in rows]
    passes = [r["model_pass_ms"] for r in rows if r["model_pass_ms"] is not None]
    done = sum(1 for r in rows if r["model_pass"] == "done")
    reasons: dict[str, int] = {}
    for r in rows:
        if r["model_pass"] == "skipped":
            reasons[r["skip_reason"]] = reasons.get(r["skip_reason"], 0) + 1
    changed = sum(1 for r in rows if r["rev1_verdict"] != r["verdict"])
    print(f"claims: {len(rows)}")
    print(f"rev.1 round trip (rules, through the internet): {summary(rev1)}")
    print(f"model pass: {summary(passes)}")
    print(f"model pass done {done}, skipped {sum(reasons.values())} {reasons}")
    print(f"model explanations published: {sum(r['explanation_source'] == 'llm' for r in rows)}")
    print(f"verdicts the model pass changed: {changed}")
    return 0


def run_load(space: Space, viewers: int, rounds: int) -> int:
    match_id = space.session("dominant_loser", 42)
    rev1_ms: list[float] = []
    outcomes: dict[str, int] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=viewers) as pool:
        for _ in range(rounds):
            futures = [pool.submit(space.verify, match_id, CLAIM, None) for _ in range(viewers)]
            rev1s = []
            for future in futures:
                rev1, took = future.result()
                rev1_ms.append(took)
                rev1s.append(rev1)
            for rev1 in rev1s:
                final = space.final(rev1)
                key = final.get("skip_reason") or final.get("model_pass") or "one-pass"
                outcomes[key] = outcomes.get(key, 0) + 1
    print(f"{viewers} viewers x {rounds} rounds: rev.1 {summary(rev1_ms)}; model pass {outcomes}")
    return 0


def run_studio(space: Space, minutes: float) -> int:
    body = {"scenario": "comeback_var", "seed": 7, "speed": 20, "restart": True, "autostart": True}
    match_id = space.http.post(f"{space.base}/matches", json=body).json()["match_id"]
    lags: list[float] = []
    deadline = time.monotonic() + minutes * 60
    while time.monotonic() < deadline:
        studio = space.http.get(f"{space.base}/healthz").json().get("studio") or {}
        if studio.get("lag_ms_p95") is not None:
            lags.append(float(studio["lag_ms_p95"]))
        time.sleep(10)
    space.http.post(f"{space.base}/matches/{match_id}/stop")
    last = lags[-1] if lags else "-"
    worst = max(lags) if lags else "-"
    print(f"studio event -> item lag p95 (/healthz, every 10 s): last {last} ms, worst {worst} ms "
          f"over {minutes} min at 20x")  # fmt: skip
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("what", choices=["eval", "load", "studio", "report"])
    parser.add_argument("url")
    parser.add_argument("--out", default="space-eval.jsonl")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--wait-model",
        action="store_true",
        help="eval: wait for the claim breaker to close, so every claim gets a model pass",
    )
    parser.add_argument("--viewers", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--minutes", type=float, default=3.0)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    space = Space(args.url)
    if args.what == "eval":
        return run_eval(space, Path(args.out), args.resume, args.wait_model)
    if args.what == "report":
        return report_timings(Path(args.out).with_suffix(".timings.jsonl"))
    if args.what == "load":
        return run_load(space, args.viewers, args.rounds)
    return run_studio(space, args.minutes)


if __name__ == "__main__":
    sys.exit(main())
