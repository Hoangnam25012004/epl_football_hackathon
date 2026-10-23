"""The numbers behind each eval label (docs/day6-design.md 4.3).

A check is `metric:subject:window`, evaluated at the claim's minute: the subject is a team id, a
player id or `-` (a match metric); the window is FULL, H1, H2, a minute range ("60-75", "75-",
"-30") or `lastN` (the last N minutes before the claim's minute). Each check carries the display
its label was written from. The harness recomputes it straight from the Stats Tools (never from
the verifier's facts), so a change in the generated data shows up as a failed check; counted
metrics are also counted apart (`eval_oracle`) for the label sheet.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from pundit.domain.events import Event
from pundit.domain.ids import team_of_player
from pundit.domain.log import LogView
from pundit.domain.matchtime import MinuteWindow, RollingWindow, parse_window
from pundit.domain.meta import MatchMeta
from pundit.orchestration import eval_oracle
from pundit.stats import REGISTRY, MetricParams, StatResult, compute, display

_LAST = re.compile(r"^last(\d+(?:\.\d+)?)$", re.IGNORECASE)
_PLAYER = re.compile(r"^[a-z0-9_]+-\d{2}$")


@dataclass(frozen=True)
class CheckKey:
    metric: str
    team: str | None
    player: str | None
    window: str

    @classmethod
    def parse(cls, key: str) -> "CheckKey":
        parts = key.split(":")
        if len(parts) != 3:
            raise ValueError(f"check {key!r}: write metric:subject:window")
        metric, subject, window = (part.strip() for part in parts)
        if metric not in REGISTRY:
            raise ValueError(f"check {key!r}: unknown metric {metric!r}")
        if _LAST.match(window) is None:
            parse_window(window)  # raises on a bad window
        if subject == "-":
            return cls(metric, None, None, window)
        if _PLAYER.match(subject):
            team = team_of_player(subject) if REGISTRY[metric].scope == "team" else None
            return cls(metric, team, subject, window)
        return cls(metric, subject, None, window)

    def span(self, view: LogView) -> MinuteWindow | RollingWindow:
        last = _LAST.match(self.window)
        if last is None:
            return parse_window(self.window)
        now = view.last.match_ms if view.last is not None else 0
        return RollingWindow.last(float(last.group(1)), now)


@dataclass(frozen=True)
class CheckValue:
    key: str
    result: StatResult
    display: str
    counted: int | None  # the independent count, for the counted metrics in a minute window


def check_value(meta: MatchMeta, events: Sequence[Event], key: str) -> CheckValue:
    """`key` over `events` (the match up to the claim's minute)."""
    parsed = CheckKey.parse(key)
    view = LogView.from_events(meta, events)
    params = MetricParams(team=parsed.team, player=parsed.player, window=parsed.span(view))
    result = compute(view, parsed.metric, params)
    counted = None
    if parsed.metric in eval_oracle.COUNTED and parsed.team and not _LAST.match(parsed.window):
        counted = eval_oracle.count(
            events, parsed.metric, parsed.team, parsed.window, player=parsed.player
        )
    return CheckValue(key=key, result=result, display=display(result), counted=counted)
