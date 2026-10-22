"""Command line: `pundit generate | replay | stats | validate | schema | scenarios` (Day 1),
`pundit claim | moments | eval | serve` (Day 2) and `pundit studio | recap` (Day 3).

Exit codes: 0 success, 1 invalid data, unsatisfiable scenario or failed evaluation, 2 bad
arguments or configuration.
"""

import argparse
import asyncio
import io
import json
import logging
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import BaseModel, ValidationError

from pundit.agents.context import build_match_context
from pundit.agents.contracts import (
    ClaimRequest,
    ClaimResult,
    CommentaryView,
    FactView,
    LowerThird,
    LowerThirdView,
    MatchContext,
    Moment,
    Profile,
    RecapView,
    RuleId,
    StudioOutput,
    TalkingPoint,
    Trigger,
    WsMessage,
)
from pundit.agents.moments import detect_all
from pundit.agents.personalize import (
    ProfileError,
    metric_facts,
    parse_profile,
    render_commentary,
    tracker_facts,
)
from pundit.agents.recap import RECAP_SCOPES, build_recap_pack
from pundit.agents.triggers import detect_triggers
from pundit.domain.errors import InvalidEventError, PunditError
from pundit.domain.events import EVENT_ADAPTER, PassEvent
from pundit.domain.log import EventLog, LogView
from pundit.domain.matchtime import (
    RollingWindow,
    minute_exact,
    parse_display_minute,
    parse_window,
)
from pundit.domain.meta import MatchMeta
from pundit.engine.generator import generate
from pundit.engine.io import match_hash, read_match, summary_json, write_match
from pundit.engine.match import GeneratedMatch
from pundit.engine.replay import RealClock, describe_event, replay, status_line
from pundit.engine.scenarios import SCENARIOS
from pundit.engine.validate import validate_match
from pundit.llm.settings import ConfigError, Settings, load_settings, read_env_file
from pundit.orchestration.evaluation import load_eval, run_eval, seq_at
from pundit.orchestration.recap_service import RecapService
from pundit.orchestration.service import ClaimService, StaticSource
from pundit.orchestration.studio_eval import build_report, run_offline
from pundit.orchestration.studio_service import StudioService
from pundit.state.match_state import MatchState
from pundit.stats import REGISTRY, MetricParams, StatResult, compute, display
from pundit.stats.possession import possession_totals

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO_ROOT / "backend"
ENV_FILE = BACKEND_DIR / ".env"  # optional, never committed
EVALS_DIR = BACKEND_DIR / "evals"
EVAL_FILES = (
    EVALS_DIR / "claims_dominant_loser_42.json",
    EVALS_DIR / "heldout_dominant_loser_42.json",
)
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
    from pundit.api.onair import OnAirCommand, OnAirState
    from pundit.api.panels import EventsPage, StatsPanel, TimelineView
    from pundit.api.payloads import WsPayloads
    from pundit.api.routes import (
        CreateMatch,
        Health,
        MatchInfo,
        MatchView,
        PlayerBrief,
        ScenarioInfo,
        StudioItemDetail,
    )

    def dump(schema: object) -> str:
        return json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"

    def serialized(model: type[BaseModel]) -> str:
        return dump(model.model_json_schema(mode="serialization"))

    day4 = {  # REST and WebSocket shapes the UI generates its types from (Day 4 section 3.8)
        "create_match.schema.json": dump(CreateMatch.model_json_schema(mode="validation")),
        "events_page.schema.json": serialized(EventsPage),
        "health.schema.json": serialized(Health),
        "match_info.schema.json": serialized(MatchInfo),
        "match_view.schema.json": serialized(MatchView),
        "on_air.schema.json": serialized(OnAirState),
        "on_air_command.schema.json": dump(OnAirCommand.model_json_schema(mode="validation")),
        "player_brief.schema.json": serialized(PlayerBrief),
        "scenario_info.schema.json": serialized(ScenarioInfo),
        "stats_panel.schema.json": serialized(StatsPanel),
        "studio_item_detail.schema.json": serialized(StudioItemDetail),
        "timeline.schema.json": serialized(TimelineView),
        "ws_payloads.schema.json": serialized(WsPayloads),
    }
    return day4 | {
        "event.schema.json": dump(EVENT_ADAPTER.json_schema(mode="serialization")),
        "match_meta.schema.json": dump(MatchMeta.model_json_schema(mode="serialization")),
        "stat_result.schema.json": dump(StatResult.model_json_schema(mode="serialization")),
        "claim_request.schema.json": dump(ClaimRequest.model_json_schema(mode="validation")),
        "claim_result.schema.json": dump(ClaimResult.model_json_schema(mode="serialization")),
        "moment.schema.json": dump(Moment.model_json_schema(mode="serialization")),
        "ws_message.schema.json": dump(WsMessage.model_json_schema(mode="serialization")),
        "talking_point.schema.json": dump(TalkingPoint.model_json_schema(mode="serialization")),
        "lower_third_view.schema.json": dump(
            LowerThirdView.model_json_schema(mode="serialization")
        ),
        "commentary_view.schema.json": dump(CommentaryView.model_json_schema(mode="serialization")),
        "recap_view.schema.json": dump(RecapView.model_json_schema(mode="serialization")),
        "profile.schema.json": dump(Profile.model_json_schema(mode="validation")),
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


def _print_claim(result: ClaimResult, *, trace: bool) -> None:
    vi = result.language == "vi"
    print(f"{result.match_id} @ {result.as_of_display} (seq {result.as_of_seq}) | synthetic data")
    print(f"CLAIM     {result.text}")
    print(f"VERDICT   {result.verdict}   (reason {result.reason_code})")
    print(f"WHY       {result.explanation or '(no explanation published)'}")
    for item in result.assertions:
        a = item.assertion
        value = "" if a.value is None else f"{a.value:g}"
        detail = " ".join(f"{a.kind} {a.metric or ''} {a.op or ''} {value}".split())
        negated = " (negated)" if a.negated else ""
        print(
            f"CHECK     {item.outcome:<12} {detail} [{a.window.label()}]{negated}"
            f" -> {item.rationale_code}"
        )
        for fact in item.facts:
            if not fact.derived:
                label = fact.label_vi if vi else fact.label_en
                events = len(fact.evidence_event_ids)
                noun = "event" if events == 1 else "events"
                print(f"            {label} = {fact.display}   [{events} {noun}]")
    ids = result.evidence_event_ids
    sample = ", ".join(ids[:3]) + (" ..." if len(ids) > 3 else "")
    print(f"EVIDENCE  {len(ids)} event ids: {sample}")
    guard = "passed" if result.guardrail.passed else "FAILED"
    print(
        f"SOURCE    analyst {result.analyst_source} ({result.model}) | "
        f"explanation {result.explanation_source} | guardrail {guard}"
    )
    print(f"DEGRADED  {', '.join(result.degraded) or '-'}")
    steps = ", ".join(f"{k} {v:.0f}" for k, v in result.timings_ms.items() if k != "total")
    print(f"TIME      total {result.timings_ms.get('total', 0.0):.0f} ms ({steps})")
    if trace and result.trace:
        print("TRACE")
        for span in result.trace:
            indent = "  " if span.parent_id else ""
            model = f" model={span.model}" if span.model else ""
            error = f" error={span.error}" if span.error else ""
            print(
                f"  {span.span_id} {indent}{span.agent:<18} {span.kind:<9} {span.name:<14} "
                f"{span.status:<9} attempt {span.attempt} {span.duration_ms:7.2f} ms{model}{error}"
            )


def _cmd_claim(args: argparse.Namespace) -> int:
    match = _load(args)
    request = ClaimRequest(
        match_id=match.meta.match_id,
        text=args.text,
        language=args.lang,
        as_of_seq=seq_at(match.events, args.at),
        include_trace=args.trace,
    )
    service = ClaimService(load_settings(ENV_FILE))
    source = StaticSource(match.meta, match.events)
    claim_id = f"{match.meta.match_id}-c0001"
    result = asyncio.run(service.verify(request, source, claim_id=claim_id))
    if args.json:
        shown = result if args.trace else result.model_copy(update={"trace": None})
        print(shown.model_dump_json(indent=2))
    else:
        _print_claim(result, trace=args.trace)
    return 0


def _cmd_moments(args: argparse.Namespace) -> int:
    match = _load(args)
    meta = match.meta
    moments = [m for m in detect_all(meta, match.events) if m.importance >= args.min_importance]
    if args.json:
        for moment in moments:
            print(moment.model_dump_json())
        return 0
    print(f"{meta.match_id} | {len(moments)} moments | synthetic data")
    for m in moments:
        team = meta.team_info(m.team).short_name if m.team else ""
        player = meta.players[m.player].short_name if m.player else ""
        facts = ", ".join(f"{f.label_en.split(' · ')[0]} {f.display}" for f in m.facts)
        stars = "*" * m.importance
        print(f"[{m.display_minute:>6}] {m.kind:<16} {stars:<3} {team:<3} {player:<16} {facts}")
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    files = [load_eval(Path(path)) for path in (args.file or EVAL_FILES)]
    if args.provider == "mock":
        settings = Settings()
    else:
        env = {**read_env_file(ENV_FILE), **os.environ}
        env.update({"USE_MOCK_LLM": "false", "LLM_PROVIDER": args.provider})
        settings = Settings.from_env(env)
    report = asyncio.run(run_eval(files, ClaimService(settings)))
    print(f"evaluation with provider {settings.provider} ({settings.model_name}) | synthetic data")
    for line in report.lines():
        print(line)
    min_accuracy = 1.0 if settings.provider == "mock" else 0.9
    passed = report.passed(require_full_accuracy=False) and report.accuracy >= min_accuracy
    if settings.provider != "mock" and not report.llm_working():
        print(
            "LLM NOT WORKING: the real model gave no usable answer or was unavailable (timeout "
            "or error) on more than 20% of the claims, so this run mostly tested the "
            "deterministic fallback. Check the provider, the server or key, and the network "
            "(pundit claim --trace)."
        )
        passed = False
    print("PASSED" if passed else "FAILED")
    return 0 if passed else 1


def _studio_settings(provider: str) -> Settings:
    """Settings of a studio run: the .env file and the environment (MOCK_LLM_FAULTS, budgets),
    with the provider of the command line."""
    env = {**read_env_file(ENV_FILE), **os.environ}
    if provider == "mock":
        env["USE_MOCK_LLM"] = "true"
    else:
        env.update({"USE_MOCK_LLM": "false", "LLM_PROVIDER": provider})
    return Settings.from_env(env)


def _profile_arg(text: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in (part.strip() for part in text.split(",")):
        if not item:
            continue
        key, sep, value = item.partition("=")
        if not sep:
            raise argparse.ArgumentTypeError(f"{item!r}: use key=value, e.g. mode=casual,lang=vi")
        params[key.strip()] = value.strip()
    return params


def _stars(importance: int) -> str:
    return "*" * importance


def _print_lower_third(item: LowerThird, profile: Profile) -> None:
    vi = profile.language == "vi"
    stats = []
    for stat in item.stats:
        label = stat.label_vi if vi else stat.label_en
        shown = stat.value if stat.value is not None else f"{stat.home}-{stat.away}"
        stats.append(f"{label} {shown}")
    title, subtitle = (item.title_vi, item.subtitle_vi) if vi else (item.title_en, item.subtitle_en)
    note = f" (replaces {', '.join(item.supersedes)})" if item.supersedes else ""
    print(f"        lower-third {item.template} [{item.source}]: {title} | {subtitle}{note}")
    print(f"            {' · '.join(stats)}")


def _print_output(
    output: StudioOutput,
    profile: Profile,
    seen: set[RuleId],
    views: Callable[[int], LogView],
    context_match: MatchContext,
) -> None:
    """One studio job as the viewer of `profile` sees it, with the studio's items."""
    item = output.commentary

    def tracker() -> tuple[FactView, ...] | None:
        if profile.player is None:
            return None
        return tracker_facts(views(item.as_of_seq), profile.player, profile.language)

    def metric() -> tuple[FactView, ...] | None:
        if profile.metric is None:
            return None
        return metric_facts(views(item.as_of_seq), profile.metric, profile.language)

    view, shown = render_commentary(
        item, profile, context_match, seen=seen, tracker=tracker, metric_facts=metric
    )
    if shown is not None:
        seen.add(shown)
    low = " (low)" if view.relevance == "low" else ""
    print(
        f"[{view.display_minute:>6}] {view.kind:<16} {_stars(view.importance):<3} "
        f"[{view.source}]{low} {view.text}"
    )
    if view.framing:
        print(f"        framing: {view.framing}")
    if view.facts:
        print(f"        facts: {' · '.join(f'{f.label} {f.display}' for f in view.facts)}")
    if view.player_tracker:
        tracked = " · ".join(f"{f.label} {f.display}" for f in view.player_tracker)
        print(f"        player: {tracked}")
    if view.explanation is not None:
        rule = view.explanation
        print(f"        rule ({rule.depth}): {rule.title}: {rule.text}")
    tp = output.talking_point
    if tp is not None:
        for language, lines, source in (("en", tp.en, tp.source_en), ("vi", tp.vi, tp.source_vi)):
            said = " | ".join(line.text for line in lines)
            print(f"        talking point {language} [{source}]: {said}")
    if output.lower_third is not None:
        _print_lower_third(output.lower_third, profile)
    if output.degraded:
        print(f"        degraded: {', '.join(output.degraded)}")


def _cmd_studio(args: argparse.Namespace) -> int:
    match = _load(args)
    settings = _studio_settings(args.provider)
    meta = match.meta
    context_match = build_match_context(LogView.from_events(meta, match.events[:1]))
    try:
        profile = parse_profile(args.profile, context_match)
    except ProfileError as exc:
        print(f"error: bad --profile: {exc}", file=sys.stderr)
        return 2
    min_importance = args.llm_min_importance or (1 if settings.provider == "mock" else 2)
    service = StudioService(settings, offline=True, llm_min_importance=min_importance)
    recaps = RecapService(settings)
    log = EventLog(meta)
    for event in match.events:
        log.append(event)
    seen: set[RuleId] = set()
    out = open(args.json, "w", encoding="utf-8") if args.json else None  # noqa: SIM115

    def write(kind: str, payload: dict[str, object]) -> None:
        if out is not None:
            out.write(json.dumps({"type": kind, **payload}, ensure_ascii=False) + "\n")

    def on_output(trigger: Trigger, output: StudioOutput) -> None:
        if not args.quiet:
            _print_output(output, profile, seen, lambda seq: log.view(seq + 1), context_match)
        write("commentary", {"item": output.commentary.model_dump(mode="json")})
        if output.talking_point is not None:
            write("talking_point", {"item": output.talking_point.model_dump(mode="json")})
        if output.lower_third is not None:
            write("lower_third", {"item": output.lower_third.model_dump(mode="json")})
        spans = [span.model_dump(mode="json") for span in output.trace or ()]
        write("trace", {"trigger_id": trigger.id, "kind": trigger.kind, "spans": spans})

    print(
        f"{meta.match_id} | studio | provider {settings.provider} ({settings.model_name}) | "
        f"profile {profile.key()} | LLM from importance {min_importance} | synthetic data"
    )
    try:
        run = asyncio.run(run_offline(match, service, recaps, on_output=on_output))
        for scope, revisions in run.recaps.items():
            final = revisions[-1]
            text = final.text(profile.language, profile.mode)
            if not args.quiet:
                print(f"RECAP {scope} revision {final.revision} [{text.source}]: {text.title}")
                for paragraph in text.paragraphs:
                    print(f"        {paragraph}")
            for recap in revisions:
                write("recap", {"item": recap.model_dump(mode="json")})
            spans = [span.model_dump(mode="json") for span in run.recap_traces.get(scope, ())]
            write("trace", {"trigger_id": final.id, "kind": "recap", "spans": spans})
    finally:
        if out is not None:
            out.close()
    if not args.report:
        return 0
    report = build_report(run)
    print()
    for line in report.lines():
        print(line)
    failures = report.failures()
    for failure in failures:
        print(f"FAIL        {failure}")
    print("PASSED" if not failures else "FAILED")
    return 0 if not failures else 1


def _cmd_recap(args: argparse.Namespace) -> int:
    match = _load(args)
    settings = _studio_settings(args.provider)
    meta = match.meta
    log = EventLog(meta)
    for event in match.events:
        log.append(event)
    kind = next(k for k, scope in RECAP_SCOPES.items() if scope == args.scope)
    trigger = next(t for t in detect_triggers(meta, match.events) if t.kind == kind)
    moments = [m for m in detect_all(meta, match.events) if m.seq <= trigger.seq]
    pack = build_recap_pack(args.scope, log.view(trigger.seq + 1), moments)
    service = RecapService(settings)
    first = service.template(pack)
    improved, trace = asyncio.run(service.improve(pack, first))
    final = improved or first
    text = final.text(args.lang, args.mode)
    words = sum(len(part.split()) for part in text.paragraphs)
    print(
        f"{meta.match_id} | recap {args.scope} | {args.lang}/{args.mode} | revision "
        f"{final.revision} [{text.source}] | {words} words | provider {settings.provider} "
        f"({settings.model_name}) | synthetic data"
    )
    print(text.title)
    for paragraph in text.paragraphs:
        print(f"  {paragraph}")
    if final.degraded:
        print(f"DEGRADED  {', '.join(final.degraded)}")
    if args.trace:
        print("TRACE")
        for span in trace.spans:
            indent = "  " if span.parent_id else ""
            error = f" error={span.error}" if span.error else ""
            print(
                f"  {span.span_id} {indent}{span.agent:<12} {span.kind:<9} {span.name:<18} "
                f"{span.status:<9} attempt {span.attempt} {span.duration_ms:9.1f} ms{error}"
            )
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from pundit.api.app import create_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("agent_framework").setLevel(logging.WARNING)  # one line per superstep
    settings = load_settings(ENV_FILE)
    if args.ui:  # Day 4: serve the static export of the frontend at "/"
        ui = Path(args.ui).resolve()
        if not (ui / "index.html").is_file():
            print(f"error: {ui} has no index.html; run `npm run build` in frontend/ first")
            return 2
        settings = settings.model_copy(update={"ui_dir": str(ui)})
    uvicorn.run(create_app(settings), host=args.host, port=args.port)
    return 0


def _window_arg(text: str) -> str:
    try:
        parse_window(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return text


def _minute_arg(text: str) -> str:
    if text.strip().upper() == "FT":
        return text
    try:
        parse_display_minute(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{exc} (e.g. 30', 45+2', 63' or FT)") from None
    return text


def _positive(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {text}")
    return value


def _non_negative(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {text}")
    return value


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
    p.add_argument("--speed", type=_positive, default=20.0)
    p.add_argument(
        "--from-minute",
        type=_non_negative,
        default=0.0,
        help="scoreboard minute; 45 and above = 2nd half",
    )
    p.add_argument("--status-every", type=_non_negative, default=5.0, help="minutes; 0 disables")
    p.add_argument("--compact", action="store_true", help="hide passes, pressure and stoppages")
    p.add_argument("--max-events", type=int, default=0, help="stop after N printed lines")
    p.set_defaults(func=_cmd_replay)

    p = sub.add_parser("stats", help="print the stats table for both teams")
    source(p)
    p.add_argument(
        "--window", type=_window_arg, default="FULL", help="FULL, H1, H2, 60-75, 75-, -30"
    )
    p.set_defaults(func=_cmd_stats)

    p = sub.add_parser("validate", help="check every invariant of a JSONL match")
    p.add_argument("--file", required=True)
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("schema", help="export JSON Schemas")
    p.add_argument("--out", default=str(SCHEMA_DIR))
    p.set_defaults(func=_cmd_schema)

    p = sub.add_parser("scenarios", help="list scenarios and metrics")
    p.set_defaults(func=_cmd_scenarios)

    p = sub.add_parser("claim", help="verify a claim against a match (Vietnamese or English)")
    p.add_argument("text", help='the claim, e.g. "Đội nhà áp đảo hiệp 2"')
    source(p)
    p.add_argument(
        "--at", type=_minute_arg, default=None, help="scoreboard minute, e.g. 63' (default: FT)"
    )
    p.add_argument("--lang", choices=["auto", "vi", "en"], default="auto")
    p.add_argument("--trace", action="store_true", help="print the agent trace")
    p.add_argument("--json", action="store_true", help="print the ClaimResult as JSON")
    p.set_defaults(func=_cmd_claim)

    p = sub.add_parser("moments", help="list the key moments of a match")
    source(p)
    p.add_argument("--min-importance", type=int, choices=[1, 2, 3], default=1)
    p.add_argument("--json", action="store_true", help="one Moment JSON per line")
    p.set_defaults(func=_cmd_moments)

    p = sub.add_parser("eval", help="run the claim evaluation sets")
    p.add_argument("--file", action="append", help="eval JSON file (repeatable); default: both")
    p.add_argument("--provider", choices=["mock", "ollama", "openai", "foundry"], default="mock")
    p.set_defaults(func=_cmd_eval)

    providers = ["mock", "ollama", "openai", "foundry"]
    p = sub.add_parser(
        "studio", help="run the studio over a whole match: commentary, talking points, graphics"
    )
    source(p)
    p.add_argument("--provider", choices=providers, default="mock")
    p.add_argument(
        "--profile",
        type=_profile_arg,
        default={},
        help="viewer, e.g. mode=casual,lang=vi,club=HAR,beginner=1 (also player=, metric=)",
    )
    p.add_argument(
        "--llm-min-importance",
        type=int,
        choices=[1, 2, 3],
        default=None,
        help="call the model from this importance (default: 1 with mock, 2 with a real model)",
    )
    p.add_argument("--json", help="write every item and trace to this JSONL file")
    p.add_argument("--report", action="store_true", help="print the evaluation report")
    p.add_argument("--quiet", action="store_true", help="do not print the items")
    p.set_defaults(func=_cmd_studio)

    p = sub.add_parser("recap", help="write the half-time or full-time recap of a match")
    source(p)
    p.add_argument("--scope", choices=["H1", "FULL"], default="FULL")
    p.add_argument("--lang", choices=["vi", "en"], default="vi")
    p.add_argument("--mode", choices=["analyst", "casual"], default="analyst")
    p.add_argument("--provider", choices=providers, default="mock")
    p.add_argument("--trace", action="store_true", help="print the agent trace")
    p.set_defaults(func=_cmd_recap)

    p = sub.add_parser("serve", help="run the API (REST + WebSocket) and, with --ui, the UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--ui", help="static export of the frontend to serve at / (frontend/out)")
    p.set_defaults(func=_cmd_serve)
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
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except PunditError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors()
        )
        print(f"error: {problems}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())
