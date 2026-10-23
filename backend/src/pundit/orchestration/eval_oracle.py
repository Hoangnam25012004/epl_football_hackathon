"""Counts for the eval labels, made apart from the Stats Tools (docs/day6-design.md 4.3).

The labels of the eval set must not come from the code they judge. These counts read the events
themselves with plain filters and never import `pundit.stats`: goals (a goal overturned by VAR
does not count, nor the shot that scored it), shots, shots on target, corners, offsides, fouls,
cards by colour, VAR overturns; per team and window, at any moment of the match. Links between
events are found by their order (the VAR check reviews the goal just before it; a goal comes
from that team's last scoring shot), not by the ids the generator wrote, so a wrong link in the
data would show up as a difference.

Model metrics (possession by ball-in-play time, xG, PPDA, momentum) have no independent count:
their definition in the Stats Tools is the product's specification, and the label sheet shows
the Stats Tools value for them.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from pundit.domain.events import CardEvent, Event, PassEvent, ShotEvent, VarCheckEvent
from pundit.domain.matchtime import MS_PER_MINUTE

COUNTED = (
    "score", "shots", "shots_on_target", "corners", "offsides", "fouls", "cards",
    "yellow_cards", "red_cards", "var_overturns",
)  # fmt: skip
_RANGE = re.compile(r"^\s*(\d+(?:\.\d+)?)?\s*-\s*(\d+(?:\.\d+)?)?\s*$")


@dataclass(frozen=True)
class Span:
    """A window on the match clock: a half, displayed minutes [start, end), or both."""

    period: int | None = None
    start_min: float | None = None
    end_min: float | None = None

    def contains(self, event: Event) -> bool:
        if self.period is not None and event.period != self.period:
            return False
        minute = (0 if event.period == 1 else 45) + event.clock_ms / MS_PER_MINUTE
        if self.start_min is not None and minute < self.start_min:
            return False
        return self.end_min is None or minute < self.end_min


def span(label: str) -> Span:
    """FULL, H1, H2, "60-75", "75-", "-30" (the labels of the eval checks)."""
    text = label.strip().upper()
    if text == "FULL":
        return Span()
    if text in ("H1", "H2"):
        return Span(period=int(text[1]))
    found = _RANGE.match(text)
    if found is None or (found.group(1) is None and found.group(2) is None):
        raise ValueError(f"not a window: {label!r} (FULL, H1, H2, 60-75, 75-, -30)")
    start = float(found.group(1)) if found.group(1) is not None else None
    end = float(found.group(2)) if found.group(2) is not None else None
    return Span(start_min=start, end_min=end)


def _overturned(events: Sequence[Event]) -> list[tuple[Event, Event]]:
    """(VAR decision, the goal it overturned) for every goal overturned so far."""
    found: list[tuple[Event, Event]] = []
    last_goal: Event | None = None
    for event in events:
        if event.type == "goal":
            last_goal = event
        elif (
            isinstance(event, VarCheckEvent)
            and event.check_type == "goal"
            and event.decision == "overturned"
            and last_goal is not None
        ):
            found.append((event, last_goal))
    return found


def voided(events: Sequence[Event]) -> set[str]:
    """Ids of the goals overturned by VAR and of the shots that scored them."""
    out: set[str] = set()
    for _, goal in _overturned(events):
        out.add(goal.id)
        shots = [
            e
            for e in events
            if isinstance(e, ShotEvent)
            and e.team == goal.team
            and e.outcome == "goal"
            and e.seq < goal.seq
        ]
        if shots:
            out.add(shots[-1].id)
    return out


def _keep(metric: str, event: Event) -> bool:
    if metric == "score":
        return event.type == "goal"
    if metric == "shots":
        return event.type == "shot"
    if metric == "shots_on_target":
        return isinstance(event, ShotEvent) and event.outcome in ("goal", "saved")
    if metric == "corners":
        return isinstance(event, PassEvent) and event.play_type == "corner"
    if metric in ("offsides", "fouls"):
        return event.type == metric.removesuffix("s")
    if metric == "cards":
        return event.type == "card"
    if metric == "yellow_cards":  # a second yellow is a yellow, and a red
        return isinstance(event, CardEvent) and event.color in ("yellow", "second_yellow")
    if metric == "red_cards":
        return isinstance(event, CardEvent) and event.color in ("second_yellow", "red")
    raise ValueError(f"no independent count for {metric!r}; counted: {', '.join(COUNTED)}")


def count(
    events: Sequence[Event],
    metric: str,
    team: str,
    window: str = "FULL",
    *,
    player: str | None = None,
) -> int:
    """How many `metric` the team (or one of its players) has in `window`, over `events` (a
    prefix of the match for a moment before the end)."""
    within = span(window)
    if metric == "var_overturns":  # counted at the VAR decision, for the team of the goal
        return sum(
            1
            for decision, goal in _overturned(events)
            if goal.team == team and within.contains(decision)
        )
    gone = voided(events)
    return sum(
        1
        for event in events
        if event.team == team
        and (player is None or event.player == player)
        and event.id not in gone
        and within.contains(event)
        and _keep(metric, event)
    )


def score_at(events: Sequence[Event], teams: Sequence[str]) -> tuple[int, ...]:
    """The score after `events`, in the order of `teams` (home first)."""
    return tuple(count(events, "score", team) for team in teams)
