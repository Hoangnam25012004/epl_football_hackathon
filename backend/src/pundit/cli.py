"""Command line: `pundit generate | replay | stats | validate | schema | scenarios`.

Exit codes: 0 success, 1 invalid data or unsatisfiable scenario, 2 bad arguments.
"""

import argparse
import asyncio
import io
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pundit.domain.errors import InvalidEventError, PunditError
from pundit.domain.events import EVENT_ADAPTER, PassEvent
from pundit.domain.matchtime import RollingWindow, minute_exact, parse_window
from pundit.domain.meta import MatchMeta
from pundit.engine.generator import generate
from pundit.engine.io import match_hash, read_match, summary_json, write_match
from pundit.engine.match import GeneratedMatch
from pundit.engine.replay import RealClock, describe_event, replay, status_line
from pundit.engine.scenarios import SCENARIOS
from pundit.engine.validate import validate_match
from pundit.state.match_state import MatchState
from pundit.stats import REGISTRY, MetricParams, StatResult, compute, display
from pundit.stats.possession import possession_totals

REPO_ROOT = Path(__file__).resolve().parents[3]
RECENT_MINUTES = 10.0  # window of the "LAST 10'" possession in replay status lines
DATA_DIR = REPO_ROOT / "data" / "matches"
GOLDEN_DIR = REPO_ROOT / "backend" / "tests" / "golden" / "data"
SCHEMA_DIR = REPO_ROOT / "packages" / "schemas"

STATS_TABLE = (
    "score",
    "possession",
    "possession_time",
    "pass_share",
    "passes",
    "pass_accuracy",
    "shots",
    "shots_on_target",
    "xg",
    "final_third_entries",
    "box_entries",
    "corners",
    "fouls",
    "cards",
    "offsides",
    "ppda",
    "momentum",
    "tempo",
)


def _load(args: argparse.Namespace) -> GeneratedMatch:
    if args.file is not None:
        return read_match(Path(args.file))
    if args.scenario is None:
        raise SystemExit("error: give --scenario (with --seed) or --file")
    return generate(args.scenario, args.seed, physical=not getattr(args, "no_physical", False))


def _cmd_generate(args: argparse.Namespace) -> int:
    match = generate(args.scenario, args.seed, physical=not args.no_physical)
    events_path, meta_path, digest = write_match(match, Path(args.out))
    print(f"wrote {events_path} ({len(match.events)} events, attempt {match.meta.attempt})")
    print(f"wrote {meta_path}")
    if args.print_hash:
        print(f"sha256 {digest}")
    if args.update_golden:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        (GOLDEN_DIR / f"{match.meta.match_id}.sha256").write_bytes(f"{digest}\n".encode())
        (GOLDEN_DIR / f"{match.meta.match_id}.summary.json").write_bytes(
            summary_json(match).encode("utf-8")
        )
        print(f"updated golden files in {GOLDEN_DIR}")
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    match = _load(args)
    meta: MatchMeta = match.meta
    state = MatchState(meta)
    quiet = {"pass", "pressure", "ball_dead", "interception", "ball_recovery", "tackle"}
    next_status = args.status_every if args.status_every > 0 else float("inf")
    printed = 0

    async def run() -> None:
        nonlocal next_status, printed
        async for item in replay(
            match.events, speed=args.speed, clock=RealClock(), from_minute=args.from_minute
        ):
            event = item.event
            state.apply(event)
            if item.catching_up:
                continue
            now = minute_exact(event.period, event.clock_ms)
            is_corner = isinstance(event, PassEvent) and event.play_type == "corner"
            show = not (args.compact and event.type in quiet and not is_corner)
            status_due = now >= next_status
            if not (show or status_due):
                continue
            snapshot = state.snapshot()  # only when something is printed
            line = describe_event(event, meta, snapshot) if show else None
            if line is not None:
                print(line, flush=True)
                printed += 1
            if status_due:
                recent = possession_totals(
                    state.view(), RollingWindow.last(RECENT_MINUTES, snapshot.as_of_ms)
                )
                recent_pct: dict[str, float | None] = {
                    team: (100.0 * ms / recent.total_ms if recent.total_ms else None)
                    for team, ms in recent.team_ms.items()
                }
                print(status_line(meta, snapshot, recent_pct, RECENT_MINUTES), flush=True)
                while next_status <= now:
                    next_status += args.status_every
            if args.max_events and printed >= args.max_events:
                return

    asyncio.run(run())
    return 0


def _row(result: StatResult) -> str:
    return display(result)


def _cmd_stats(args: argparse.Namespace) -> int:
    match = _load(args)
    view = match.view()
    window = parse_window(args.window)
    home, away = match.meta.team_ids
    names = (match.meta.home.team.short_name, match.meta.away.team.short_name)
    print(f"{match.meta.match_id} | window {window.label()} | synthetic data")
    print(f"{'metric':<22}{names[0]:>12}{names[1]:>12}")
    for name in STATS_TABLE:
        values = [compute(view, name, MetricParams(team=t, window=window)) for t in (home, away)]
        print(f"{name:<22}{_row(values[0]):>12}{_row(values[1]):>12}")
    in_play = compute(view, "ball_in_play_time", MetricParams(window=window))
    chaos = compute(view, "chaos_index", MetricParams(window=window))
    print(f"{'ball_in_play_time':<22}{_row(in_play):>24}")
    print(f"{'chaos_index':<22}{_row(chaos):>17} {chaos.label or ''}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    match = read_match(Path(args.file))
    validate_match(match.meta, match.events)
    print(f"OK {args.file}: {len(match.events)} events, sha256 {match_hash(match.events)}")
    return 0


def _schemas() -> dict[str, str]:
    def dump(schema: object) -> str:
        return json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"

    return {
        "event.schema.json": dump(EVENT_ADAPTER.json_schema(mode="serialization")),
        "match_meta.schema.json": dump(MatchMeta.model_json_schema(mode="serialization")),
        "stat_result.schema.json": dump(StatResult.model_json_schema(mode="serialization")),
    }


def _cmd_schema(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, text in _schemas().items():
        (out / name).write_bytes(text.encode("utf-8"))
        print(f"wrote {out / name}")
    return 0


def _cmd_scenarios(args: argparse.Namespace) -> int:
    for key, scenario in SCENARIOS.items():
        print(f"{key:<16} {scenario.home} vs {scenario.away}: {scenario.title_en}")
    print(f"\n{len(REGISTRY)} metrics: {', '.join(sorted(REGISTRY))}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pundit", description="Pundit Copilot match engine")
    sub = parser.add_subparsers(dest="command", required=True)

    def source(p: argparse.ArgumentParser) -> None:
        p.add_argument("--scenario", choices=sorted(SCENARIOS))
        p.add_argument("--seed", type=int, default=42)
        p.add_argument("--file", help="a *.events.jsonl file instead of generating")

    p = sub.add_parser("generate", help="generate a match and write it as JSONL")
    p.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default=str(DATA_DIR))
    p.add_argument("--no-physical", action="store_true", help="skip player_physical snapshots")
    p.add_argument("--print-hash", action="store_true")
    p.add_argument("--update-golden", action="store_true")
    p.set_defaults(func=_cmd_generate)

    p = sub.add_parser("replay", help="replay a match in real time")
    source(p)
    p.add_argument("--speed", type=float, default=20.0)
    p.add_argument(
        "--from-minute", type=float, default=0.0, help="scoreboard minute; 45 and above = 2nd half"
    )
    p.add_argument("--status-every", type=float, default=5.0, help="minutes; 0 disables")
    p.add_argument("--compact", action="store_true", help="hide passes, pressure and stoppages")
    p.add_argument("--max-events", type=int, default=0, help="stop after N printed lines")
    p.set_defaults(func=_cmd_replay)

    p = sub.add_parser("stats", help="print the stats table for both teams")
    source(p)
    p.add_argument("--window", default="FULL", help="FULL, H1, H2, 60-75, 75-, -30")
    p.set_defaults(func=_cmd_stats)

    p = sub.add_parser("validate", help="check every invariant of a JSONL match")
    p.add_argument("--file", required=True)
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("schema", help="export JSON Schemas")
    p.add_argument("--out", default=str(SCHEMA_DIR))
    p.set_defaults(func=_cmd_schema)

    p = sub.add_parser("scenarios", help="list scenarios and metrics")
    p.set_defaults(func=_cmd_scenarios)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")  # Vietnamese names on Windows consoles
    args = _parser().parse_args(argv)
    try:
        code: int = args.func(args)
    except InvalidEventError as exc:
        print(f"invalid data: {exc}", file=sys.stderr)
        return 1
    except PunditError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
