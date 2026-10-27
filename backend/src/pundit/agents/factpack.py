"""FactPack: the verified numbers of one trigger (docs/day3-design.md section 5).

`plan()` lists the Stats Tools queries a trigger needs, `build_pack()` turns the tool calls into
Facts. Both run on the view pinned at the trigger's seq, so a replay that moves on never changes
the numbers of an item being written. The orchestration executes the plan through the shared
tool catalog, so every query is a `tool` span of the trace.
"""

from collections.abc import Sequence
from typing import Literal

from pundit.agents.context import build_match_context
from pundit.agents.contracts import Fact, FactPack, RuleId, StatQuery, ToolCall, Trigger
from pundit.agents.wording import fact_labels, format_value, window_numbers
from pundit.domain.events import ShotEvent
from pundit.domain.log import LogView
from pundit.domain.matchtime import FULL, H1, MinuteWindow, RollingWindow
from pundit.domain.meta import MatchMeta
from pundit.stats import StatResult, display

Window = MinuteWindow | RollingWindow


def stat_fact(meta: MatchMeta, result: StatResult, fallback_evidence: str) -> Fact:
    """A Fact from a Stats Tools result; same key format as the facts of moments."""
    assert result.value is not None
    owner = result.player or result.team or "match"
    if result.player is not None:
        who = meta.players[result.player].short_name
    elif result.team is not None:
        who = meta.team_info(result.team).short_name
    else:
        who = "match"
    label_vi, label_en = fact_labels(result.metric, who, result.window)
    return Fact(
        key=f"{result.metric}:{owner}:{result.window.label()}@{result.as_of_seq}",
        label_vi=label_vi,
        label_en=label_en,
        value=result.value,
        unit=result.unit,
        display=display(result),
        evidence_event_ids=result.evidence_event_ids or (fallback_evidence,),
        metric=result.metric,
        team=result.team,
        player=result.player,
    )


def shot_fact(shot: ShotEvent, kind: Literal["xg", "speed"]) -> Fact:
    if kind == "xg":
        value, unit, label_vi, label_en = shot.xg, "xg", "xG cú sút", "Shot xG"
    else:
        value, unit, label_vi, label_en = shot.speed_kmh, "kmh", "Tốc độ cú sút", "Shot speed"
    return Fact(
        key=f"shot_{kind}:{shot.id}",
        label_vi=label_vi,
        label_en=label_en,
        value=value,
        unit=unit,  # type: ignore[arg-type]
        display=format_value(value, unit),  # type: ignore[arg-type]
        evidence_event_ids=(shot.id,),
        team=shot.team,
        player=shot.player,
    )


# --------------------------------------------------------------------------- plan


def _both(metric: str, window: Window, teams: tuple[str, str]) -> list[StatQuery]:
    return [StatQuery(metric=metric, team=team, window=window) for team in teams]


def plan(trigger: Trigger, meta: MatchMeta) -> list[StatQuery]:
    """The extra Stats Tools queries of a trigger (section 5); order is the order of the facts."""
    teams = meta.team_ids
    kind, team = trigger.kind, trigger.team

    def last(minutes: float) -> RollingWindow:
        return RollingWindow.last(minutes, trigger.match_ms)

    queries: list[StatQuery] = []
    if kind in ("goal", "goal_overturned", "substitution", "period_start", "var_review",
                "added_time"):  # fmt: skip
        queries += _both("score", FULL, teams)
    if kind == "goal":
        queries += _both("xg", FULL, teams) + _both("shots", FULL, teams)
        scorer = trigger.roles.get("scorer")
        if scorer is not None and trigger.details.get("own_goal") != "true":
            queries.append(
                StatQuery(metric="score", team=meta.players[scorer].team_id, player=scorer,
                          window=FULL)
            )  # fmt: skip
    elif kind == "red_card" and team is not None:
        queries.append(StatQuery(metric="red_cards", team=team, window=FULL))
        queries += _both("possession", last(10), teams)
    elif kind == "yellow_card" and team is not None:
        queries.append(StatQuery(metric="yellow_cards", team=team, window=FULL))
        carded = trigger.roles.get("carded")
        if carded is not None:
            queries.append(StatQuery(metric="fouls", team=team, player=carded, window=FULL))
    elif kind == "big_chance":
        queries += _both("xg", FULL, teams) + _both("shots", FULL, teams)
    elif kind in ("rocket_shot", "shot") and team is not None:
        queries.append(StatQuery(metric="shots", team=team, window=FULL))
    elif kind == "dangerous_spell":
        queries += _both("box_entries", last(10), teams)
    elif kind == "momentum_shift":
        queries += _both("possession", last(10), teams) + _both("shots", last(10), teams)
    elif kind in ("period_end", "full_time"):
        window = H1 if kind == "period_end" else FULL
        queries += _both("score", FULL, teams)
        for metric in ("possession", "shots", "shots_on_target", "xg", "corners"):
            queries += _both(metric, window, teams)
    elif kind == "stat_tick":
        for metric in ("possession", "shots", "xg"):
            queries += _both(metric, FULL, teams)
        queries.append(StatQuery(metric="chaos_index", window=last(15)))
        queries += _both("tempo", last(15), teams)
    elif kind == "lull":
        queries += _both("possession", last(5), teams) + _both("passes", last(5), teams)
    return queries


# --------------------------------------------------------------------------- headline

HEADLINE: dict[str, tuple[str, ...]] = {  # kind -> metric / key prefixes, most important first
    "goal": ("score", "shot_xg:"),
    "goal_overturned": ("score",),
    "red_card": ("red_cards",),
    "yellow_card": ("yellow_cards",),
    "big_chance": ("shot_xg:",),
    "rocket_shot": ("shot_speed:",),
    "dangerous_spell": ("spell_shots:", "spell_box_entries:"),
    "momentum_shift": ("momentum",),
    "period_end": ("score", "possession", "shots", "xg"),
    "full_time": ("score", "possession", "shots", "xg"),
    "shot": ("shot_xg:",),
    "corner": ("corners",),
    "offside": ("offsides",),
    "tackle": ("tackles",),
    "foul": ("fouls",),
    "box_entry": ("box_entries",),
    "stat_tick": ("possession", "shots", "xg"),
    "lull": ("possession",),
}


def headline_keys(trigger: Trigger, facts: Sequence[Fact]) -> tuple[str, ...]:
    keys: list[str] = []
    for wanted in HEADLINE.get(trigger.kind, ()):
        for fact in facts:
            hit = fact.key.startswith(wanted) if wanted.endswith(":") else fact.metric == wanted
            if hit and not fact.derived and fact.key not in keys:
                keys.append(fact.key)
    return tuple(keys[:4])


# --------------------------------------------------------------------------- pack


def build_pack(
    trigger: Trigger,
    view: LogView,
    calls: Sequence[ToolCall],
    *,
    first_rules: Sequence[RuleId] = (),
) -> FactPack:
    """Facts of the trigger, then of the tool calls (in plan order), without duplicate keys.
    Results without a value (no data in the window) are left out: nothing to say about them."""
    meta = view.meta
    fallback = trigger.trigger_event_ids[-1]
    seen: dict[str, Fact] = {f.key: f for f in trigger.facts}
    numbers: set[float] = set()
    for call in calls:
        numbers |= window_numbers(call.result.window)
        if call.result.value is None:
            continue
        fact = stat_fact(meta, call.result, fallback)
        seen.setdefault(fact.key, fact)
    if trigger.kind == "momentum_shift":
        numbers.add(10.0)  # the moment's own momentum is on the last 10 minutes
    if "tick_minute" in trigger.details:
        numbers.add(float(trigger.details["tick_minute"]))  # "sau 15 phút"
    facts = tuple(seen.values())
    return FactPack(
        trigger=trigger,
        match=build_match_context(view),
        facts=facts,
        headline=headline_keys(trigger, facts),
        window_numbers=tuple(sorted(numbers)),
        first_rules=tuple(first_rules),
    )
