"""Moments and cues as studio triggers (docs/day3-design.md sections 3.1, 4.1 and 4.2).

A trigger adds what the writers and the Guardrail need to know about the event: who took part
(roles), what happened (outcome), and which rules a beginner may want explained.
"""

from collections.abc import Sequence

from pundit.agents.contracts import (
    Cue,
    EventOutcome,
    Moment,
    RuleId,
    Trigger,
)
from pundit.agents.cues import TICK_MINUTES, CueDetector
from pundit.agents.moments import MomentDetector
from pundit.domain.events import (
    CardEvent,
    Event,
    FoulEvent,
    GoalEvent,
    OffsideEvent,
    PassEvent,
    ShotEvent,
    SubstitutionEvent,
    TackleEvent,
    VarCheckEvent,
)
from pundit.domain.log import EventLog, LogView
from pundit.domain.matchtime import minute_exact
from pundit.domain.meta import MatchMeta

Described = tuple[dict[str, str], dict[str, str], EventOutcome | None, list[RuleId]]
CARD_OUTCOME: dict[str, EventOutcome] = {
    "yellow": "yellow",
    "red": "red",
    "second_yellow": "second_yellow",
}
KIND_RULES: dict[str, tuple[RuleId, ...]] = {
    "momentum_shift": ("momentum",),
    "added_time": ("added_time",),
    "stat_tick": ("possession", "xg", "chaos", "tempo"),
    "lull": ("possession",),
}
CARD_RULE: dict[str, RuleId] = {
    "yellow": "yellow_card",
    "red": "red_card",
    "second_yellow": "second_yellow",
}


def _shot(shot: ShotEvent) -> Described:
    roles = {"shooter": shot.player} if shot.player else {}
    if shot.goalkeeper is not None:
        roles["goalkeeper"] = shot.goalkeeper
    details: dict[str, str] = {
        "situation": shot.situation,
        "body_part": shot.body_part,
        "outcome": shot.outcome,
    }
    rules: list[RuleId] = ["penalty"] if shot.situation == "penalty" else []
    return roles, details, shot.outcome, rules


def _describe(kind: str, event: Event, view: LogView) -> Described:
    """Roles, details, outcome and rules of a trigger of this kind fired by this event."""
    if kind == "goal" and isinstance(event, GoalEvent):
        shot = view.get(event.related_ids[0])
        roles: dict[str, str] = {"scorer": event.player} if event.player else {}
        details: dict[str, str] = {"own_goal": "true" if event.own_goal else "false"}
        rules: list[RuleId] = []
        if event.assist is not None:
            roles["assist"] = event.assist
        if isinstance(shot, ShotEvent):
            if shot.goalkeeper is not None:
                roles["goalkeeper"] = shot.goalkeeper
            details |= {"situation": shot.situation, "body_part": shot.body_part}
            if shot.situation == "penalty":
                rules.append("penalty")
        return roles, details, "goal", [*rules, "xg"]
    if kind == "goal_overturned" and isinstance(event, VarCheckEvent):
        goal = view.get(event.related_ids[0])
        roles = {"scorer": goal.player} if goal.player else {}
        details = {"var_reason": event.reason, "check_type": event.check_type}
        rules = ["var", "offside"] if event.reason == "offside" else ["var"]
        return roles, details, "overturned", rules
    if isinstance(event, CardEvent):
        roles = {"carded": event.player} if event.player else {}
        return roles, {"color": event.color}, CARD_OUTCOME[event.color], [CARD_RULE[event.color]]
    if kind in ("big_chance", "rocket_shot", "shot") and isinstance(event, ShotEvent):
        roles, details, outcome, rules = _shot(event)
        return roles, details, outcome, [*rules, "xg"] if kind != "rocket_shot" else rules
    if isinstance(event, SubstitutionEvent):
        roles = {"player_in": event.player_in}
        if event.player is not None:
            roles["player_out"] = event.player
        return roles, {}, None, ["substitution"]
    if kind == "corner" and isinstance(event, PassEvent):
        roles = {"taker": event.player} if event.player else {}
        return roles, {}, None, ["corner"]
    if kind == "offside" and isinstance(event, OffsideEvent):
        roles = {"offside_player": event.player} if event.player else {}
        details = {"via_var": "true" if event.via_var else "false"}
        rules = ["offside", "var"] if event.via_var else ["offside"]
        return roles, details, "offside", rules
    if kind == "tackle" and isinstance(event, TackleEvent):
        roles = {"tackled": event.target_player}
        if event.player is not None:
            roles["tackler"] = event.player
        return roles, {}, None, []
    if kind == "foul" and isinstance(event, FoulEvent):
        roles = {"fouled": event.fouled_player}
        if event.player is not None:
            roles["fouler"] = event.player
        return roles, {}, None, []
    if kind == "box_entry" and isinstance(event, PassEvent):
        roles = {"passer": event.player} if event.player else {}
        if event.recipient is not None:
            roles["receiver"] = event.recipient
        return roles, {}, None, []
    if kind == "var_review" and isinstance(event, VarCheckEvent):
        details = {
            "check_type": event.check_type,
            "decision": event.decision,
            "var_reason": event.reason,
        }
        roles = {}
        if event.check_type == "goal" and event.related_ids:
            goal = view.get(event.related_ids[0])
            if goal.player is not None:
                roles["scorer"] = goal.player
        rules = ["var"]
        if event.check_type == "penalty":
            rules.append("penalty")
        elif event.check_type == "red_card":
            rules.append("red_card")
        decided: EventOutcome = "overturned" if event.decision == "overturned" else "stands"
        return roles, details, decided, rules
    return {}, {}, None, list(KIND_RULES.get(kind, ()))


def trigger_from_moment(moment: Moment, event: Event, view: LogView) -> Trigger:
    roles, details, outcome, rules = _describe(moment.kind, event, view)
    return Trigger(
        id=moment.id,
        match_id=moment.match_id,
        source="moment",
        kind=moment.kind,
        importance=moment.importance,
        team=moment.team,
        player=moment.player,
        period=moment.period,
        clock_ms=moment.clock_ms,
        match_ms=event.match_ms,
        display_minute=moment.display_minute,
        seq=moment.seq,
        trigger_event_ids=moment.trigger_event_ids,
        facts=moment.facts,
        rules=tuple(dict.fromkeys(rules)),
        outcome=outcome,
        roles=roles,
        details=details,
        related_ids=moment.related_moment_ids,
    )


def trigger_from_cue(cue: Cue, event: Event, view: LogView) -> Trigger:
    roles, details, outcome, rules = _describe(cue.kind, event, view)
    if cue.kind == "stat_tick":
        minute = minute_exact(event.period, event.clock_ms)
        marks = [t for t in TICK_MINUTES.get(event.period, ()) if t <= minute]
        details = {"tick_minute": f"{max(marks):g}"} if marks else {}
    return Trigger(
        id=cue.id,
        match_id=cue.match_id,
        source="cue",
        kind=cue.kind,
        importance=1,
        team=cue.team,
        player=cue.player,
        period=cue.period,
        clock_ms=cue.clock_ms,
        match_ms=event.match_ms,
        display_minute=cue.display_minute,
        seq=cue.seq,
        trigger_event_ids=cue.trigger_event_ids,
        facts=cue.facts,
        rules=tuple(dict.fromkeys(rules)),
        outcome=outcome,
        roles=roles,
        details=details,
    )


class TriggerFeed:
    """Moments, then cues, of each event as studio triggers, in detection order."""

    def __init__(self, meta: MatchMeta) -> None:
        self.moments = MomentDetector(meta)
        self.cues = CueDetector(meta)

    def feed(self, event: Event, view: LogView) -> tuple[list[Moment], list[Trigger]]:
        moments = self.moments.feed(event, view)
        cues = self.cues.feed(event, view, moments)
        triggers = [trigger_from_moment(m, event, view) for m in moments]
        triggers += [trigger_from_cue(c, event, view) for c in cues]
        return moments, triggers


def detect_triggers(meta: MatchMeta, events: Sequence[Event]) -> list[Trigger]:
    """Batch detection over a whole match; identical to the live session."""
    feed = TriggerFeed(meta)
    log = EventLog(meta)
    found: list[Trigger] = []
    for event in events:
        log.append(event)
        found += feed.feed(event, log.view())[1]
    return found
