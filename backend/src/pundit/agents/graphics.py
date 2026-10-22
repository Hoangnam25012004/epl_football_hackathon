"""Graphics agent (docs/day3-design.md section 7): lower-thirds as timed JSON.

The rule table picks a template and its stat rows for each trigger kind; a model (P1) may pick
among the same candidates and is validated against them. Titles and subtitles come from fixed
phrasings (nothing free-form goes on air) and still pass the Guardrail. Values are the displays
of facts; colours come from the team sheets.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pundit.agents.contracts import (
    Fact,
    FactPack,
    GraphicsChoice,
    GraphicsOptions,
    GraphicStat,
    LowerThird,
    LowerThirdTemplate,
    TeamBadge,
    Trigger,
)
from pundit.agents.phrasebank import pick
from pundit.agents.studio_text import find_fact, find_key, home_away
from pundit.agents.studio_values import pack_values
from pundit.domain.meta import DISCLAIMER, MatchMeta

GRAPHIC_KINDS = frozenset(
    {"goal", "goal_overturned", "red_card", "momentum_shift", "period_end", "full_time",
     "stat_tick", "big_chance", "rocket_shot"}
)  # fmt: skip
TICK_GRAPHICS = frozenset({"30", "75"})  # stat ticks with a graphic
DURATION_MS: dict[LowerThirdTemplate, int] = {
    "score_update": 10_000,
    "team_compare": 8_000,
    "player_card": 8_000,
    "momentum": 8_000,
}
SHORT_LABELS: dict[str, tuple[str, str]] = {
    "score": ("Tỷ số", "Score"),
    "possession": ("Kiểm soát bóng", "Possession"),
    "shots": ("Cú sút", "Shots"),
    "shots_on_target": ("Sút trúng đích", "On target"),
    "xg": ("xG", "xG"),
    "corners": ("Phạt góc", "Corners"),
    "momentum": ("Momentum", "Momentum"),
    "red_cards": ("Thẻ đỏ", "Red cards"),
    "box_entries": ("Vào vòng cấm", "Box entries"),
    "shot_xg": ("xG cú sút", "Shot xG"),
    "shot_speed": ("Tốc độ cú sút", "Shot speed"),
}
TITLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {  # kind -> (vi, en) phrasings
    "goal": (("BÀN THẮNG", "VÀO!"), ("GOAL", "GOAL!")),
    "goal_overturned": (("VAR: KHÔNG CÔNG NHẬN BÀN THẮNG",), ("VAR: GOAL RULED OUT",)),
    "red_card": (("THẺ ĐỎ",), ("RED CARD",)),
    "momentum_shift": (("THẾ TRẬN ĐỔI CHIỀU",), ("MOMENTUM SWING",)),
    "period_end": (("HẾT HIỆP 1",), ("HALF-TIME",)),
    "full_time": (("HẾT TRẬN",), ("FULL TIME",)),
    "stat_tick": (("SAU {tick} PHÚT",), ("AFTER {tick} MINUTES",)),
    "big_chance": (("CƠ HỘI LỚN",), ("BIG CHANCE",)),
    "rocket_shot": (("CÚ SÚT CỰC MẠNH",), ("ROCKET SHOT",)),
}
SCORE_SUBTITLE = "{home_s} {score_h}-{score_a} {away_s}"  # home team first, like the scoreboard
SUBTITLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "goal": (("{scorer} · {team}",), ("{scorer} · {team}",)),
    "goal_overturned": (("{scorer} · {team_s}",), ("{scorer} · {team_s}",)),
    "red_card": (("{carded} · {team}",), ("{carded} · {team}",)),
    "momentum_shift": (("{team} · {last10}",), ("{team} · {last10}",)),
    "period_end": ((SCORE_SUBTITLE,), (SCORE_SUBTITLE,)),
    "full_time": ((SCORE_SUBTITLE,), (SCORE_SUBTITLE,)),
    "stat_tick": ((SCORE_SUBTITLE,), (SCORE_SUBTITLE,)),
    "big_chance": (("{shooter} · {team_s}",), ("{shooter} · {team_s}",)),
    "rocket_shot": (("{shooter} · {team_s}",), ("{shooter} · {team_s}",)),
}
# kind -> (template, rows); a row is a metric (two teams) or a fact key prefix (one value)
RULES: dict[str, tuple[LowerThirdTemplate, tuple[str, ...]]] = {
    "goal": ("score_update", ("score", "shot_xg:")),
    "goal_overturned": ("score_update", ("score",)),
    "red_card": ("player_card", ("red_cards",)),
    "momentum_shift": ("momentum", ("momentum", "possession", "shots")),
    "period_end": ("team_compare", ("possession", "shots", "xg", "corners")),
    "full_time": ("team_compare", ("possession", "shots", "xg", "corners")),
    "stat_tick": ("team_compare", ("possession", "shots", "xg")),
    "big_chance": ("player_card", ("shot_xg:", "shots")),
    "rocket_shot": ("player_card", ("shot_speed:", "shots")),
}
TEMPLATES_FOR: dict[str, tuple[LowerThirdTemplate, ...]] = {
    kind: (rule[0], "team_compare") if rule[0] != "team_compare" else (rule[0],)
    for kind, rule in RULES.items()
}


@dataclass(frozen=True)
class Row:
    key: str
    stat: GraphicStat
    facts: tuple[Fact, ...]


def wants_graphic(trigger: Trigger) -> bool:
    if trigger.kind not in GRAPHIC_KINDS:
        return False
    if trigger.kind == "stat_tick":
        return trigger.details.get("tick_minute") in TICK_GRAPHICS
    return True


def _label(name: str) -> tuple[str, str]:
    return SHORT_LABELS.get(name.rstrip(":"), (name, name))


def rows_of(pack: FactPack) -> dict[str, Row]:
    """Every possible row of this pack, by key: two-team metrics and one-value facts."""
    home, away = home_away(pack.match)
    trigger_team = pack.trigger.team
    rows: dict[str, Row] = {}
    for metric in ("score", "possession", "shots", "shots_on_target", "xg", "corners",
                   "box_entries"):  # fmt: skip
        first = find_fact(pack.facts, metric, team=home)
        second = find_fact(pack.facts, metric, team=away)
        if first is None or second is None:
            continue
        label_vi, label_en = _label(metric)
        highlight: Literal["home", "away"] | None = None
        if first.value != second.value:
            highlight = "home" if first.value > second.value else "away"
        stat = GraphicStat(
            key=first.key,
            label_vi=label_vi,
            label_en=label_en,
            home=first.display,
            away=second.display,
            highlight=highlight,
        )
        rows[metric] = Row(metric, stat, (first, second))
    singles: list[tuple[str, Fact | None]] = [
        ("shot_xg:", find_key(pack.facts, "shot_xg:")),
        ("shot_speed:", find_key(pack.facts, "shot_speed:")),
    ]
    if trigger_team is not None:
        singles += [
            ("momentum", find_fact(pack.facts, "momentum", team=trigger_team)),
            ("red_cards", find_fact(pack.facts, "red_cards", team=trigger_team)),
        ]
        if "shots" not in rows:
            singles.append(("shots", find_fact(pack.facts, "shots", team=trigger_team)))
    for key, fact in singles:
        if fact is None:
            continue
        label_vi, label_en = _label(key)
        stat = GraphicStat(key=fact.key, label_vi=label_vi, label_en=label_en, value=fact.display)
        rows[key] = Row(key, stat, (fact,))
    return rows


def options(pack: FactPack) -> GraphicsOptions:
    """What a designer (rule or model) may choose from for this pack."""
    return GraphicsOptions(
        templates=TEMPLATES_FOR.get(pack.trigger.kind, ()), stat_keys=tuple(rows_of(pack))
    )


def rule_choice(pack: FactPack) -> GraphicsChoice | None:
    rule = RULES.get(pack.trigger.kind)
    if rule is None:
        return None
    template, wanted = rule
    rows = rows_of(pack)
    keys = tuple(k for k in wanted if k in rows)
    if not keys:
        keys = tuple(rows)[:1]
    return GraphicsChoice(template=template, stat_keys=keys) if keys else None


def validate_choice(choice: GraphicsChoice, opts: GraphicsOptions) -> list[str]:
    errors: list[str] = []
    if choice.template not in opts.templates:
        errors.append(f"template {choice.template!r} is not one of {list(opts.templates)}")
    unknown = [k for k in choice.stat_keys if k not in opts.stat_keys]
    if unknown:
        errors.append(f"stats {unknown} are not among the candidates {list(opts.stat_keys)}")
    if not 1 <= len(choice.stat_keys) <= 4 or len(set(choice.stat_keys)) != len(choice.stat_keys):
        errors.append("choose 1 to 4 different stats")
    return errors


def _badge(meta: MatchMeta, team_id: str) -> TeamBadge:
    info = meta.team_info(team_id)
    return TeamBadge(
        id=info.id,
        name=info.name,
        short_name=info.short_name,
        primary_color=info.primary_color,
        secondary_color=info.secondary_color,
    )


def texts(pack: FactPack) -> dict[str, str]:
    """title_vi, title_en, subtitle_vi, subtitle_en of the pack's graphic."""
    kind, key = pack.trigger.kind, pack.trigger.id
    values = {
        "vi": pack_values(pack, "vi") | _score_values(pack),
        "en": pack_values(pack, "en") | _score_values(pack),
    }
    out: dict[str, str] = {}
    for part, bank in (("title", TITLES), ("subtitle", SUBTITLES)):
        vi_variants, en_variants = bank.get(kind, ((), ()))
        out[f"{part}_vi"] = pick(vi_variants, f"{key}:{part}", values["vi"]) or ""
        out[f"{part}_en"] = pick(en_variants, f"{key}:{part}", values["en"]) or ""
    return out


def _score_values(pack: FactPack) -> dict[str, str]:
    home, away = home_away(pack.match)
    return {
        "score_h": str(pack.match.score.get(home, 0)),
        "score_a": str(pack.match.score.get(away, 0)),
    }


def build_lower_third(
    pack: FactPack,
    meta: MatchMeta,
    choice: GraphicsChoice,
    *,
    source: str = "rule",
    supersedes: Sequence[str] = (),
) -> LowerThird:
    trigger = pack.trigger
    rows = rows_of(pack)
    chosen = [rows[k] for k in choice.stat_keys if k in rows]
    facts = tuple(dict.fromkeys(f for row in chosen for f in row.facts))
    evidence = tuple(sorted({e for f in facts for e in f.evidence_event_ids}))
    home, away = meta.team_ids
    words = texts(pack)
    return LowerThird(
        id=f"{trigger.id}-lt",
        match_id=trigger.match_id,
        trigger_id=trigger.id,
        template=choice.template,
        title_vi=words["title_vi"],
        title_en=words["title_en"],
        subtitle_vi=words["subtitle_vi"],
        subtitle_en=words["subtitle_en"],
        stats=tuple(row.stat for row in chosen),
        home=_badge(meta, home),
        away=_badge(meta, away),
        player=trigger.player,
        period=trigger.period,
        clock_ms=trigger.clock_ms,
        match_clock=trigger.display_minute,
        show_at_match_ms=trigger.match_ms,
        duration_ms=DURATION_MS[choice.template] + (2_000 if trigger.kind == "full_time" else 0),
        priority=trigger.importance,
        supersedes=tuple(supersedes),
        facts=facts,
        evidence_event_ids=evidence,
        source="llm" if source == "llm" else "rule",
        disclaimer=DISCLAIMER,
    )
