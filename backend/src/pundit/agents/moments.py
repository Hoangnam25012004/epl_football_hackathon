"""MomentDetector (docs/day2-design.md section 10): key moments from the event stream.

Stateful but deterministic: fed the same events in the same order it returns the same moments
with the same ids. `detect_all` (batch) runs the very same `feed`, and a test checks it against
the live session. Moments carry Facts only, so Day 3 can turn them into talking points that
still pass the Guardrail.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from pundit.agents.contracts import Fact, Moment, MomentKind
from pundit.agents.wording import fact_labels, format_value
from pundit.domain.events import (
    CardEvent,
    Event,
    GoalEvent,
    PassEvent,
    ShotEvent,
    SubstitutionEvent,
    VarCheckEvent,
)
from pundit.domain.log import EventLog, LogView
from pundit.domain.matchtime import MS_PER_MINUTE, RollingWindow, format_minute
from pundit.domain.meta import MatchMeta
from pundit.domain.thresholds import SHOT_SPEED_ALERT_KMH
from pundit.stats import MetricParams, StatResult, compute, display
from pundit.stats.team import is_box_entry

BIG_CHANCE_XG = 0.30
SPELL_SHOTS = 2
SPELL_BOX_ENTRIES = 3
SPELL_ENTRY_WINDOW_MS = 90_000
SPELL_COOLDOWN_MS = 3 * MS_PER_MINUTE
MOMENTUM_WINDOW_MIN = 10.0
MOMENTUM_SHIFT = 0.5
MOMENTUM_COOLDOWN_MS = 5 * MS_PER_MINUTE


def moment_id(match_id: str, n: int) -> str:
    return f"{match_id}-m{n:04d}"


@dataclass
class _Spell:
    team: str | None = None
    shots: list[str] = field(default_factory=list)
    entries: list[tuple[int, str]] = field(default_factory=list)  # (match_ms, pass id)
    fired: bool = False


class MomentDetector:
    def __init__(self, meta: MatchMeta) -> None:
        self.meta = meta
        self.home, self.away = meta.team_ids
        self._count = 0
        self._goal_moments: dict[str, str] = {}  # goal event id -> moment id
        self._spell = _Spell()
        self._spell_cooldown: dict[str, int] = {}  # team -> match_ms of its last spell moment
        self._minute = 0  # last minute boundary (played time) that was evaluated
        self._armed = {"up": False, "down": False}
        self._momentum_at: int | None = None  # match_ms of the last momentum_shift

    # ------------------------------------------------------------------ helpers

    def _moment(
        self,
        kind: MomentKind,
        importance: int,
        event: Event,
        *,
        team: str | None = None,
        player: str | None = None,
        triggers: Sequence[str] = (),
        facts: Sequence[Fact] = (),
        related: Sequence[str] = (),
    ) -> Moment:
        self._count += 1
        return Moment(
            id=moment_id(self.meta.match_id, self._count),
            match_id=self.meta.match_id,
            kind=kind,
            importance=importance,  # type: ignore[arg-type]
            team=team,
            player=player,
            period=event.period,
            clock_ms=event.clock_ms,
            display_minute=format_minute(event.period, event.clock_ms),
            trigger_event_ids=tuple(sorted(set(triggers or (event.id,)))),
            facts=tuple(facts),
            seq=event.seq,
            related_moment_ids=tuple(related),
        )

    def _stat_fact(self, result: StatResult, fallback_evidence: str) -> Fact:
        assert result.value is not None
        owner = result.player or result.team or "match"
        who = self.meta.team_info(result.team).short_name if result.team else "match"
        label = result.window.label()
        label_vi, label_en = fact_labels(result.metric, who, result.window)
        return Fact(
            key=f"{result.metric}:{owner}:{label}@{result.as_of_seq}",
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

    def _score_facts(self, view: LogView, trigger: str) -> list[Fact]:
        return [
            self._stat_fact(compute(view, "score", MetricParams(team=team)), trigger)
            for team in (self.home, self.away)
        ]

    def _shot_fact(self, shot: ShotEvent, kind: str) -> Fact:
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

    def _count_fact(
        self, key: str, label_vi: str, label_en: str, ids: list[str], team: str
    ) -> Fact:
        return Fact(
            key=key,
            label_vi=label_vi,
            label_en=label_en,
            value=float(len(ids)),
            unit="count",
            display=str(len(ids)),
            evidence_event_ids=tuple(sorted(ids)),
            team=team,
        )

    # ------------------------------------------------------------------ rules

    def _momentum(self, event: Event, view: LogView) -> list[Moment]:
        """Momentum (last 10', home view) at every played-minute boundary before `event`."""
        found: list[Moment] = []
        boundary = event.match_ms // MS_PER_MINUTE
        while self._minute < boundary:
            self._minute += 1
            end_ms = self._minute * MS_PER_MINUTE
            if self._minute < MOMENTUM_WINDOW_MIN:
                continue
            window = RollingWindow(
                start_ms=end_ms - round(MOMENTUM_WINDOW_MIN * MS_PER_MINUTE), end_ms=end_ms
            )
            value = compute(view, "momentum", MetricParams(team=self.home, window=window)).value
            assert value is not None
            direction: str | None = None
            if self._armed["up"] and value >= MOMENTUM_SHIFT:
                direction = "up"
            elif self._armed["down"] and value <= -MOMENTUM_SHIFT:
                direction = "down"
            if value <= 0:
                self._armed["up"] = True
            if value >= 0:
                self._armed["down"] = True
            if direction is None:
                continue
            self._armed[direction] = False
            if self._momentum_at is not None and end_ms - self._momentum_at < MOMENTUM_COOLDOWN_MS:
                continue
            self._momentum_at = end_ms
            team = self.home if direction == "up" else self.away
            result = compute(view, "momentum", MetricParams(team=team, window=window))
            fact = self._stat_fact(result, event.id)
            found.append(
                self._moment(
                    "momentum_shift",
                    2,
                    event,
                    team=team,
                    triggers=fact.evidence_event_ids[-3:],
                    facts=[fact],
                )
            )
        return found

    def _dangerous_spell(self, event: Event) -> list[Moment]:
        spell = self._spell
        if event.possession_team is not None and event.possession_team != spell.team:
            self._spell = spell = _Spell(team=event.possession_team)
        if spell.team is None or event.team != spell.team or spell.fired:
            return []
        if isinstance(event, ShotEvent):
            spell.shots.append(event.id)
        elif isinstance(event, PassEvent) and is_box_entry(event):
            spell.entries.append((event.match_ms, event.id))
        recent = [eid for ms, eid in spell.entries if event.match_ms - ms <= SPELL_ENTRY_WINDOW_MS]
        if len(spell.shots) < SPELL_SHOTS and len(recent) < SPELL_BOX_ENTRIES:
            return []
        last = self._spell_cooldown.get(spell.team)
        if last is not None and event.match_ms - last < SPELL_COOLDOWN_MS:
            return []
        spell.fired = True
        self._spell_cooldown[spell.team] = event.match_ms
        team = spell.team
        entries = [eid for _, eid in spell.entries]
        facts = []
        if spell.shots:
            facts.append(
                self._count_fact(
                    f"spell_shots:{team}:{event.id}",
                    "Số cú sút trong pha tấn công",
                    "Shots in the spell",
                    spell.shots,
                    team,
                )
            )
        if entries:
            facts.append(
                self._count_fact(
                    f"spell_box_entries:{team}:{event.id}",
                    "Số lần vào vòng cấm trong pha tấn công",
                    "Box entries in the spell",
                    entries,
                    team,
                )
            )
        triggers = spell.shots + entries
        return [
            self._moment("dangerous_spell", 2, event, team=team, triggers=triggers, facts=facts)
        ]

    def feed(self, event: Event, view: LogView) -> list[Moment]:
        """Moments triggered by `event`; `view` is the log right after applying it."""
        found = self._momentum(event, view)
        if isinstance(event, GoalEvent):
            shot = view.get(event.related_ids[0])
            assert isinstance(shot, ShotEvent)
            moment = self._moment(
                "goal",
                3,
                event,
                team=event.team,
                player=event.player,
                triggers=[*event.related_ids, event.id],
                facts=[*self._score_facts(view, event.id), self._shot_fact(shot, "xg")],
            )
            self._goal_moments[event.id] = moment.id
            found.append(moment)
        elif isinstance(event, VarCheckEvent):
            if event.check_type == "goal" and event.decision == "overturned":
                goal = view.get(event.related_ids[0])
                related = [self._goal_moments[goal.id]] if goal.id in self._goal_moments else []
                found.append(
                    self._moment(
                        "goal_overturned",
                        3,
                        event,
                        team=goal.team,
                        player=goal.player,
                        triggers=[event.id, goal.id],
                        facts=self._score_facts(view, event.id),
                        related=related,
                    )
                )
        elif isinstance(event, CardEvent):
            red = event.color in ("red", "second_yellow")
            metric = "red_cards" if red else "yellow_cards"
            assert event.team is not None
            fact = self._stat_fact(compute(view, metric, MetricParams(team=event.team)), event.id)
            kind: MomentKind = "red_card" if red else "yellow_card"
            found.append(
                self._moment(
                    kind, 3 if red else 1, event, team=event.team, player=event.player, facts=[fact]
                )
            )
        elif isinstance(event, ShotEvent):
            if event.outcome != "goal" and event.xg >= BIG_CHANCE_XG:
                found.append(
                    self._moment(
                        "big_chance",
                        2,
                        event,
                        team=event.team,
                        player=event.player,
                        facts=[self._shot_fact(event, "xg")],
                    )
                )
            if event.speed_kmh >= SHOT_SPEED_ALERT_KMH:
                found.append(
                    self._moment(
                        "rocket_shot",
                        1,
                        event,
                        team=event.team,
                        player=event.player,
                        facts=[self._shot_fact(event, "speed")],
                    )
                )
        elif isinstance(event, SubstitutionEvent):
            found.append(
                self._moment("substitution", 1, event, team=event.team, player=event.player_in)
            )
        elif event.type == "period_start":
            found.append(self._moment("period_start", 1, event))
        elif event.type == "period_end":
            kind = "period_end" if event.period == 1 else "full_time"
            found.append(self._moment(kind, 2, event, facts=self._score_facts(view, event.id)))
        found += self._dangerous_spell(event)
        return found


def detect_all(meta: MatchMeta, events: Sequence[Event]) -> list[Moment]:
    """Batch detection over a whole match; identical to feeding the events one by one."""
    detector = MomentDetector(meta)
    log = EventLog(meta)
    moments: list[Moment] = []
    for event in events:
        log.append(event)
        moments += detector.feed(event, log.view())
    return moments
