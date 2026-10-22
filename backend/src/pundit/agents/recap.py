"""Recaps at half-time and full time (docs/day3-design.md section 10).

`build_recap_pack` gathers the verified material of a scope (H1 or FULL) on the view pinned at
the whistle: the score, the goals that stand (a goal overturned by VAR is never listed as a
goal), the cards, the stats of both teams, the halves compared and the key moments. The
templates write four versions (vi / en x analyst / casual), short enough to be read aloud in one
or two minutes on Day 5.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from pundit.agents.context import build_match_context
from pundit.agents.contracts import (
    Degraded,
    Fact,
    GraphicStat,
    Lang,
    MatchContext,
    Moment,
    Recap,
    RecapScope,
    RecapText,
    Register,
)
from pundit.agents.factpack import stat_fact
from pundit.agents.phrasebank import capitalize_sentences
from pundit.agents.studio_guard import StudioScope
from pundit.agents.studio_text import find_fact, player_name, score_line, team_name, team_short
from pundit.domain.events import CardEvent, GoalEvent, VarCheckEvent
from pundit.domain.log import LogView
from pundit.domain.matchtime import FULL, H1, H2, MinuteWindow, format_minute
from pundit.stats import MetricParams, compute

STAT_METRICS = ("possession", "shots", "shots_on_target", "xg", "corners", "fouls")
HALF_METRICS = ("possession", "shots", "xg")
MAX_KEY_MOMENTS = 3
RECAP_SCOPES: dict[str, RecapScope] = {"period_end": "H1", "full_time": "FULL"}  # trigger kind


@dataclass(frozen=True)
class GoalLine:
    minute: str  # "27'"
    team: str
    scorer: str | None
    assist: str | None
    event_id: str


@dataclass(frozen=True)
class CardLine:
    minute: str
    team: str
    player: str
    color: str
    event_id: str


@dataclass(frozen=True)
class RecapPack:
    scope: RecapScope
    match: MatchContext
    facts: tuple[Fact, ...]
    goals: tuple[GoalLine, ...]
    overturned: tuple[GoalLine, ...]
    cards: tuple[CardLine, ...]
    key_moments: tuple[Moment, ...]
    window_numbers: frozenset[float] = field(default_factory=frozenset)

    @property
    def players(self) -> frozenset[str]:
        found = {g.scorer for g in self.goals + self.overturned if g.scorer}
        found |= {g.assist for g in self.goals if g.assist}
        found |= {c.player for c in self.cards}
        found |= {m.player for m in self.key_moments if m.player}
        return frozenset(found)


def _in_scope(scope: RecapScope, period: int) -> bool:
    return scope == "FULL" or period == 1


def build_recap_pack(scope: RecapScope, view: LogView, moments: Sequence[Moment]) -> RecapPack:
    meta = view.meta
    home, away = meta.team_ids
    events = list(view)
    overturned_ids = {
        e.related_ids[0]
        for e in events
        if isinstance(e, VarCheckEvent) and e.check_type == "goal" and e.decision == "overturned"
    }
    goals: list[GoalLine] = []
    overturned: list[GoalLine] = []
    cards: list[CardLine] = []
    for event in events:
        if not _in_scope(scope, event.period):
            continue
        minute = format_minute(event.period, event.clock_ms)
        if isinstance(event, GoalEvent) and event.team is not None:
            line = GoalLine(minute, event.team, event.player, event.assist, event.id)
            (overturned if event.id in overturned_ids else goals).append(line)
        elif isinstance(event, CardEvent) and event.team is not None and event.player is not None:
            cards.append(CardLine(minute, event.team, event.player, event.color, event.id))
    window = H1 if scope == "H1" else FULL
    facts: dict[str, Fact] = {}
    last = events[-1].id

    def add(metric: str, team: str | None, w: MinuteWindow) -> None:
        result = compute(view, metric, MetricParams(team=team, window=w))
        if result.value is not None:
            fact = stat_fact(meta, result, last)
            facts.setdefault(fact.key, fact)

    for team in (home, away):
        add("score", team, FULL)
    for metric in (*STAT_METRICS, "yellow_cards", "red_cards"):
        for team in (home, away):
            add(metric, team, window)
    if scope == "FULL":
        for half in (H1, H2):
            for metric in HALF_METRICS:
                for team in (home, away):
                    add(metric, team, half)
    chosen = [m for m in moments if _in_scope(scope, m.period) and m.importance == 3]
    chosen += [
        m for m in moments
        if _in_scope(scope, m.period) and m.importance == 2
        and m.kind not in ("period_end", "full_time")
    ]  # fmt: skip
    key = tuple(m for m in chosen if m.kind not in ("goal", "goal_overturned"))[:MAX_KEY_MOMENTS]
    shown = [g.minute for g in (*goals, *overturned)] + [c.minute for c in cards]
    minutes = {float(n) for display in shown for n in _numbers(display)}
    minutes |= {float(n) for m in key for n in _numbers(m.display_minute)}
    minutes |= {1.0, 2.0}
    return RecapPack(
        scope=scope,
        match=build_match_context(view),
        facts=tuple(facts.values()),
        goals=tuple(goals),
        overturned=tuple(overturned),
        cards=tuple(cards),
        key_moments=key,
        window_numbers=frozenset(minutes),
    )


def _numbers(display_minute: str) -> list[int]:
    """ "45+2'" -> [45, 2]."""
    return [int(part) for part in display_minute.rstrip("'").split("+")]


def recap_scope(pack: RecapPack) -> StudioScope:
    return StudioScope(
        facts=pack.facts,
        match=pack.match,
        players=pack.players,
        trigger=None,
        window_numbers=pack.window_numbers,
        clock_display=pack.match.clock_display,
        has_numbers=True,
    )


# --------------------------------------------------------------------------- templates


def _fact(pack: RecapPack, metric: str, team: str, window: str) -> str | None:
    fact = find_fact(pack.facts, metric, team=team, window=window)
    return fact.display if fact is not None else None


def _minute(display: str, lang: Lang) -> str:
    return f"phút {display.rstrip(chr(39))}" if lang == "vi" else display


def _goal_list(pack: RecapPack, lang: Lang, with_minutes: bool) -> str | None:
    if not pack.goals:
        return None
    items = []
    for goal in pack.goals:
        who = (
            player_name(pack.match, goal.scorer)
            if goal.scorer
            else team_name(pack.match, goal.team)
        )
        team = team_short(pack.match, goal.team)
        items.append(f"{who} ({team}, {_minute(goal.minute, lang)})" if with_minutes else who)
    return "; ".join(items) if with_minutes else ", ".join(dict.fromkeys(items))


def _stats_sentence(pack: RecapPack, team: str, window: str, lang: Lang) -> str | None:
    poss = _fact(pack, "possession", team, window)
    shots = _fact(pack, "shots", team, window)
    sot = _fact(pack, "shots_on_target", team, window)
    xg = _fact(pack, "xg", team, window)
    if None in (poss, shots, sot, xg):
        return None
    name = team_name(pack.match, team)
    if lang == "vi":
        return f"{name} kiểm soát bóng {poss}, dứt điểm {shots} lần ({sot} trúng đích), xG {xg}."
    return f"{name} had {poss} possession and {shots} shots ({sot} on target), xG {xg}."


def _halves(pack: RecapPack, lang: Lang) -> str | None:
    home, away = pack.match.teams[0].id, pack.match.teams[1].id
    parts = []
    for half, label_vi, label_en in (
        ("H1", "Hiệp 1", "First half"),
        ("H2", "hiệp 2", "second half"),
    ):
        h = _fact(pack, "possession", home, half)
        a = _fact(pack, "possession", away, half)
        if h is None or a is None:
            return None
        hs, as_ = team_short(pack.match, home), team_short(pack.match, away)
        parts.append(
            f"{label_vi}: kiểm soát bóng {hs} {h}, {as_} {a}"
            if lang == "vi"
            else f"{label_en}: possession {hs} {h}, {as_} {a}"
        )
    return "; ".join(parts) + "."


def _cards(pack: RecapPack, lang: Lang) -> str | None:
    home, away = pack.match.teams[0].id, pack.match.teams[1].id
    window = "H1" if pack.scope == "H1" else "FULL"
    sentences = []
    for team in (home, away):
        yellow = _fact(pack, "yellow_cards", team, window)
        red = _fact(pack, "red_cards", team, window)
        name = team_name(pack.match, team)
        if yellow is not None and yellow != "0":
            sentences.append(
                f"{name} nhận {yellow} thẻ vàng."
                if lang == "vi"
                else f"Yellow cards for {name}: {yellow}."
            )
        if red is not None and red != "0":
            sentences.append(
                f"{name} nhận {red} thẻ đỏ." if lang == "vi" else f"Red cards for {name}: {red}."
            )
    return " ".join(sentences) or None


def _overturned(pack: RecapPack, lang: Lang, with_minutes: bool) -> str | None:
    if not pack.overturned:
        return None
    items = []
    for goal in pack.overturned:
        who = (
            player_name(pack.match, goal.scorer)
            if goal.scorer
            else team_name(pack.match, goal.team)
        )
        when = f" ({_minute(goal.minute, lang)})" if with_minutes else ""
        items.append(f"{who}{when}")
    joined = ", ".join(items)
    return (
        f"VAR không công nhận bàn thắng của {joined}."
        if lang == "vi"
        else f"VAR ruled out the goal by {joined}."
    )


KEY_VI = {
    "red_card": "thẻ đỏ của {player}",
    "big_chance": "cơ hội lớn của {player}",
    "momentum_shift": "thế trận đổi chiều về {team}",
    "dangerous_spell": "sức ép từ {team}",
    "yellow_card": "thẻ vàng của {player}",
}
KEY_EN = {
    "red_card": "the red card for {player}",
    "big_chance": "the big chance for {player}",
    "momentum_shift": "the swing towards {team}",
    "dangerous_spell": "the pressure from {team}",
    "yellow_card": "the booking of {player}",
}


def _key_moments(pack: RecapPack, lang: Lang, with_minutes: bool = True) -> str | None:
    bank = KEY_VI if lang == "vi" else KEY_EN
    items = []
    for moment in pack.key_moments:
        phrase = bank.get(moment.kind)
        if phrase is None:
            continue
        player = player_name(pack.match, moment.player) if moment.player else ""
        team = team_name(pack.match, moment.team) if moment.team else ""
        text = phrase.format(player=player, team=team)
        when = f" ({_minute(moment.display_minute, lang)})" if with_minutes else ""
        items.append(f"{text}{when}")
    if not items:
        return None
    lead = "Khoảnh khắc đáng chú ý: " if lang == "vi" else "Key moments: "
    return lead + "; ".join(items) + "."


def render_recap(pack: RecapPack, language: Lang, mode: Register) -> RecapText:
    vi = language == "vi"
    score = score_line(pack.match, language)
    home, away = pack.match.teams[0].id, pack.match.teams[1].id
    window = "H1" if pack.scope == "H1" else "FULL"
    if pack.scope == "H1":
        title = f"Tóm tắt hiệp 1: {score}" if vi else f"Half-time recap: {score}"
        opening = f"Hiệp 1 khép lại với tỷ số {score}." if vi else f"Half-time: {score}."
    else:
        title = f"Tóm tắt trận đấu: {score}" if vi else f"Match recap: {score}"
        opening = f"Trận đấu kết thúc với tỷ số {score}." if vi else f"Full time: {score}."
    paragraphs: list[str] = [opening]
    analyst = mode == "analyst"
    goals = _goal_list(pack, language, with_minutes=analyst)
    if goals:
        paragraphs.append(("Các bàn thắng: " if vi else "Goals: ") + goals + ".")
    else:
        paragraphs.append("Chưa có bàn thắng nào." if vi else "No goals so far.")
    overturned = _overturned(pack, language, with_minutes=analyst)
    if overturned:
        paragraphs.append(overturned)
    if analyst:
        stats = [s for s in (_stats_sentence(pack, t, window, language) for t in (home, away)) if s]
        if stats:
            paragraphs.append(" ".join(stats))
        if pack.scope == "FULL":
            halves = _halves(pack, language)
            if halves:
                paragraphs.append(halves)
        cards = _cards(pack, language)
        if cards:
            paragraphs.append(cards)
        key = _key_moments(pack, language)
        if key:
            paragraphs.append(key)
    else:
        poss = _fact(pack, "possession", home, window)
        if poss is not None:
            name = team_name(pack.match, home)
            paragraphs.append(
                f"{name} cầm bóng {poss} thời gian." if vi else f"{name} had {poss} of the ball."
            )
        reds = [t for t in (home, away) if _fact(pack, "red_cards", t, window) not in (None, "0")]
        for team in reds:
            name = team_name(pack.match, team)
            paragraphs.append(
                f"{name} phải chơi thiếu người vì thẻ đỏ."
                if vi
                else f"{name} had a player sent off."
            )
        key = _key_moments(pack, language, with_minutes=False)
        if key:
            paragraphs.append(key)
    return RecapText(
        language=language,
        mode=mode,
        title=capitalize_sentences(title),
        paragraphs=tuple(capitalize_sentences(p) for p in paragraphs[:6]),
        source="template",
    )


def recap_stats(pack: RecapPack) -> tuple[GraphicStat, ...]:
    home, away = pack.match.teams[0].id, pack.match.teams[1].id
    window = "H1" if pack.scope == "H1" else "FULL"
    rows: list[GraphicStat] = []
    labels = {
        "possession": ("Kiểm soát bóng", "Possession"),
        "shots": ("Cú sút", "Shots"),
        "shots_on_target": ("Sút trúng đích", "On target"),
        "xg": ("xG", "xG"),
        "corners": ("Phạt góc", "Corners"),
        "fouls": ("Phạm lỗi", "Fouls"),
    }
    for metric in STAT_METRICS:
        first = find_fact(pack.facts, metric, team=home, window=window)
        second = find_fact(pack.facts, metric, team=away, window=window)
        if first is None or second is None:
            continue
        label_vi, label_en = labels[metric]
        rows.append(
            GraphicStat(
                key=first.key, label_vi=label_vi, label_en=label_en, home=first.display,
                away=second.display,
            )
        )  # fmt: skip
    return tuple(rows)


def build_recap(
    pack: RecapPack,
    texts: Sequence[RecapText],
    *,
    revision: int = 1,
    degraded: Sequence[Degraded] = (),
) -> Recap:
    evidence = tuple(sorted({e for f in pack.facts for e in f.evidence_event_ids}))
    return Recap(
        id=f"{pack.match.match_id}-recap-{pack.scope}",
        match_id=pack.match.match_id,
        scope=pack.scope,
        as_of_seq=pack.match.as_of_seq,
        texts=tuple(texts),
        key_moment_ids=tuple(m.id for m in pack.key_moments),
        stats=recap_stats(pack),
        facts=pack.facts,
        evidence_event_ids=evidence,
        revision=revision,
        degraded=tuple(degraded),
    )


def template_recap(pack: RecapPack) -> Recap:
    texts = [
        render_recap(pack, language, mode)
        for language in ("vi", "en")
        for mode in ("analyst", "casual")
    ]
    return build_recap(pack, texts)
