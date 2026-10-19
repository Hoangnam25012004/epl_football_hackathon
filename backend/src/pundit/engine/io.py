"""Reading and writing matches as JSON Lines (UTF-8, LF line endings)."""

import hashlib
import json
from pathlib import Path
from typing import Any

from pundit.domain.events import EVENT_ADAPTER, Event
from pundit.domain.matchtime import FULL, H1, H2
from pundit.domain.meta import MatchMeta
from pundit.engine.match import GeneratedMatch
from pundit.stats import MetricParams, compute

EVENTS_SUFFIX = ".events.jsonl"
META_SUFFIX = ".meta.json"

SUMMARY_TEAM_METRICS = (
    "score",
    "possession",
    "pass_share",
    "passes",
    "pass_accuracy",
    "shots",
    "shots_on_target",
    "xg",
    "final_third_entries",
    "corners",
    "fouls",
    "cards",
    "offsides",
)


def events_to_jsonl(events: tuple[Event, ...] | list[Event]) -> bytes:
    return b"".join(EVENT_ADAPTER.dump_json(event) + b"\n" for event in events)


def events_from_jsonl(data: bytes) -> list[Event]:
    return [EVENT_ADAPTER.validate_json(line) for line in data.splitlines() if line.strip()]


def match_hash(events: tuple[Event, ...] | list[Event]) -> str:
    return hashlib.sha256(events_to_jsonl(events)).hexdigest()


def write_match(match: GeneratedMatch, out_dir: Path) -> tuple[Path, Path, str]:
    """Write events and metadata. Bytes are written as-is, so line endings stay LF on Windows."""
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / f"{match.meta.match_id}{EVENTS_SUFFIX}"
    meta_path = out_dir / f"{match.meta.match_id}{META_SUFFIX}"
    data = events_to_jsonl(match.events)
    events_path.write_bytes(data)
    meta_path.write_bytes((match.meta.model_dump_json(indent=2) + "\n").encode("utf-8"))
    return events_path, meta_path, hashlib.sha256(data).hexdigest()


def read_match(events_path: Path) -> GeneratedMatch:
    name = events_path.name
    if not name.endswith(EVENTS_SUFFIX):
        raise ValueError(f"expected a *{EVENTS_SUFFIX} file, got {name}")
    meta_path = events_path.with_name(name[: -len(EVENTS_SUFFIX)] + META_SUFFIX)
    meta = MatchMeta.model_validate_json(meta_path.read_bytes())
    events = events_from_jsonl(events_path.read_bytes())
    return GeneratedMatch(meta=meta, events=tuple(events))


def match_summary(match: GeneratedMatch) -> dict[str, Any]:
    """Human-reviewable summary used by the golden tests."""
    view = match.view()
    windows = {"FULL": FULL, "H1": H1, "H2": H2}
    stats: dict[str, Any] = {}
    for label, window in windows.items():
        per_team: dict[str, Any] = {}
        for team in match.meta.team_ids:
            values = {}
            for name in SUMMARY_TEAM_METRICS:
                value = compute(view, name, MetricParams(team=team, window=window)).value
                values[name] = None if value is None else round(value, 2)
            per_team[team] = values
        in_play = compute(view, "ball_in_play_time", MetricParams(window=window)).value
        stats[label] = {"teams": per_team, "ball_in_play_minutes": round(in_play or 0.0, 2)}
    return {
        "match_id": match.meta.match_id,
        "attempt": match.meta.attempt,
        "events": len(match.events),
        "stats": stats,
    }


def summary_json(match: GeneratedMatch) -> str:
    return json.dumps(match_summary(match), indent=2, ensure_ascii=False) + "\n"
