"""Personalization agent (docs/day3-design.md section 11): one viewer, one rendering.

Personalization is a policy on verified content (principle S6): it picks the version of a text
for the viewer's language and register, adds a fixed framing line for fans of a club, explains a
rule to beginners the first time they meet it, and never writes a new number. The two exceptions
(player focus, one metric) read their numbers from the Stats Tools like every other agent.
"""

from collections.abc import Callable, Mapping
from collections.abc import Set as AbstractSet
from typing import Literal

from pundit.agents.contracts import (
    CommentaryItem,
    CommentaryView,
    Fact,
    FactView,
    GraphicStat,
    GraphicStatView,
    Lang,
    LowerThird,
    LowerThirdView,
    MatchContext,
    Profile,
    Recap,
    RecapView,
    RuleId,
    RuleView,
)
from pundit.agents.factpack import stat_fact
from pundit.agents.rules_library import REPEAT_SHORT, mentioned_rules, ordered, rule_view
from pundit.agents.studio_text import find_fact
from pundit.domain.log import LogView
from pundit.stats import MetricParams, compute
from pundit.stats.registry import REGISTRY

Emphasis = Literal["own_team", "opponent", "neutral", "focus_player"]
Relevance = Literal["high", "normal", "low"]
TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
FALSE_WORDS = frozenset({"0", "false", "no", "off"})
MAX_FACTS = 6
TRACKER_METRICS = ("passes", "shots", "xg", "player_distance", "player_top_speed")
LOW_FOR_RIVAL_FANS = frozenset({"shot", "corner", "offside", "lull", "substitution"})

# Fixed framing lines for fans of a club (casual only): (kind, side) -> (vi, en)
FRAMING: dict[tuple[str, str], tuple[str, str]] = {
    ("goal", "own"): ("Đội của bạn ghi bàn!", "Your team scores!"),
    ("goal", "rival"): ("Tiếc quá, đối thủ vừa có bàn.", "Bad news: the opponents score."),
    ("goal_overturned", "own"): (
        "Tiếc cho đội của bạn, bàn thắng không được công nhận.",
        "Heartbreak: your team's goal is ruled out.",
    ),
    ("goal_overturned", "rival"): (
        "May cho đội của bạn, bàn thắng của đối thủ bị huỷ!",
        "Reprieve: the opponents' goal is ruled out!",
    ),
    ("red_card", "own"): ("Đội của bạn phải chơi thiếu người.", "Your team is a player down."),
    ("red_card", "rival"): ("Đối thủ phải chơi thiếu người!", "The opponents are a player down!"),
    ("yellow_card", "own"): ("Cầu thủ đội bạn bị cảnh cáo.", "A booking for your team."),
    ("yellow_card", "rival"): ("Đối thủ nhận thẻ vàng.", "A booking for the opponents."),
    ("big_chance", "own"): ("Suýt nữa cho đội của bạn!", "So close for your team!"),
    ("big_chance", "rival"): ("Hú vía cho đội của bạn!", "A let-off for your team!"),
    ("dangerous_spell", "own"): ("Đội của bạn đang dồn lên!", "Your team is piling forward!"),
    ("dangerous_spell", "rival"): ("Đội của bạn đang chịu sức ép.", "Your team is under pressure."),
    ("momentum_shift", "own"): ("Đội của bạn đang lên!", "Your team is on the rise!"),
    ("momentum_shift", "rival"): ("Đối thủ đang vùng lên.", "The opponents are rising."),
    ("full_time", "win"): ("Chiến thắng cho đội của bạn!", "A win for your team!"),
    ("full_time", "loss"): ("Đội của bạn để thua trận này.", "A defeat for your team."),
    ("full_time", "draw"): ("Đội của bạn có một trận hoà.", "A draw for your team."),
}


class ProfileError(ValueError):
    """The profile in the query does not describe a viewer of this match."""


def parse_profile(params: Mapping[str, str], match: MatchContext) -> Profile:
    """A profile from query parameters (`mode`, `lang`, `club`, `player`, `beginner`, `metric`)."""
    errors: list[str] = []
    mode = (params.get("mode") or "analyst").strip().lower()
    if mode not in ("analyst", "casual"):
        errors.append(f"mode must be analyst or casual, got {mode!r}")
    language = (params.get("lang") or "vi").strip().lower()
    if language not in ("vi", "en"):
        errors.append(f"lang must be vi or en, got {language!r}")
    team: str | None = None
    club = (params.get("club") or "").strip()
    if club:
        for team_ref in match.teams:
            names = (team_ref.id, team_ref.short_name, team_ref.name)
            if club.casefold() in (name.casefold() for name in names):
                team = team_ref.id
        if team is None:
            errors.append(f"club {club!r} does not play in this match")
    player: str | None = None
    wanted = (params.get("player") or "").strip()
    if wanted:
        for player_ref in match.players:
            if wanted.casefold() in (player_ref.id.casefold(), player_ref.short_name.casefold()):
                player = player_ref.id
        if player is None:
            errors.append(f"player {wanted!r} does not play in this match")
    beginner = False
    flag = (params.get("beginner") or "").strip().lower()
    if flag in TRUE_WORDS:
        beginner = True
    elif flag and flag not in FALSE_WORDS:
        errors.append(f"beginner must be 1 or 0, got {flag!r}")
    metric = (params.get("metric") or "").strip() or None
    if metric is not None:
        spec = REGISTRY.get(metric)
        if spec is None or spec.scope == "match":
            errors.append(f"metric {metric!r} is not a team metric of the catalog")
    if errors:
        raise ProfileError("; ".join(errors))
    return Profile(
        mode=mode,  # type: ignore[arg-type]
        language=language,  # type: ignore[arg-type]
        team=team,
        player=player,
        beginner=beginner,
        metric=metric,
    )


# --------------------------------------------------------------------------- commentary


def emphasis_of(item: CommentaryItem, profile: Profile) -> Emphasis:
    if profile.player is not None and (
        profile.player == item.player or profile.player in item.involved_players
    ):
        return "focus_player"
    if profile.team is not None and item.team is not None:
        return "own_team" if item.team == profile.team else "opponent"
    return "neutral"


def relevance_of(item: CommentaryItem, profile: Profile, emphasis: Emphasis) -> Relevance:
    if emphasis == "focus_player" or item.importance == 3:
        return "high"
    if (
        profile.mode == "casual"
        and emphasis == "opponent"
        and item.importance == 1
        and item.trigger_kind in LOW_FOR_RIVAL_FANS
    ):
        return "low"
    return "normal"


def _result_side(item: CommentaryItem, team: str, match: MatchContext) -> str | None:
    """win / loss / draw of `team`, from the score facts of a full-time item."""
    own = find_fact(item.facts, "score", team=team)
    rival = find_fact(item.facts, "score", team=match.opponent(team))
    if own is None or rival is None:
        return None
    return "win" if own.value > rival.value else "loss" if own.value < rival.value else "draw"


def framing_of(item: CommentaryItem, profile: Profile, match: MatchContext) -> str | None:
    if profile.mode != "casual" or profile.team is None:
        return None
    if item.trigger_kind == "full_time":
        side = _result_side(item, profile.team, match)
    elif item.team is None:
        return None
    else:
        side = "own" if item.team == profile.team else "rival"
    line = FRAMING.get((item.trigger_kind, side or ""))
    if line is None:
        return None
    return line[0] if profile.language == "vi" else line[1]


def explanation_of(
    item: CommentaryItem, profile: Profile, seen: AbstractSet[RuleId]
) -> tuple[RuleView | None, RuleId | None]:
    """The first rule this viewer has not seen yet (full text); else a short reminder for the
    rules that matter every time (red card, penalty). Returns the view and the rule now seen.
    A statistics rule waits for a line of the viewer's own version that names the metric."""
    if not profile.beginner:
        return None, None
    text = item.text(profile.language, profile.mode).text
    rules = ordered(list(mentioned_rules(item.rules, [text])))
    for rule in rules:
        if rule not in seen:
            return rule_view(rule, profile.language, "full"), rule
    for rule in rules:
        if rule in REPEAT_SHORT:
            return rule_view(rule, profile.language, "short"), None
    return None, None


def fact_views(facts: tuple[Fact, ...], language: Lang) -> tuple[FactView, ...]:
    return tuple(
        FactView(label=f.label_vi if language == "vi" else f.label_en, display=f.display)
        for f in facts
        if not f.derived
    )[:MAX_FACTS]


FactsSource = Callable[[], tuple[FactView, ...] | None]


def render_commentary(
    item: CommentaryItem,
    profile: Profile,
    match: MatchContext,
    *,
    seen: AbstractSet[RuleId] = frozenset(),
    tracker: FactsSource | None = None,
    metric_facts: FactsSource | None = None,
) -> tuple[CommentaryView, RuleId | None]:
    """The item as this viewer gets it, and the rule it explained in full (if any)."""
    version = item.text(profile.language, profile.mode)
    emphasis = emphasis_of(item, profile)
    relevance = relevance_of(item, profile, emphasis)
    explanation, shown = explanation_of(item, profile, seen)
    facts = fact_views(item.facts, profile.language) if profile.mode == "analyst" else ()
    player_tracker = None
    if item.trigger_kind == "stat_tick":
        if profile.metric is not None and metric_facts is not None:
            facts = metric_facts() or facts
        if profile.player is not None and tracker is not None:
            player_tracker = tracker()
    view = CommentaryView(
        id=item.id,
        trigger_id=item.trigger_id,
        kind=item.trigger_kind,
        period=item.period,
        clock_ms=item.clock_ms,
        display_minute=item.display_minute,
        importance=item.importance,
        language=profile.language,
        mode=profile.mode,
        text=version.text,
        framing=framing_of(item, profile, match),
        emphasis=emphasis,
        relevance=relevance,
        facts=facts,
        explanation=explanation,
        player_tracker=player_tracker,
        speak=item.speak and relevance != "low",
        excitement=item.excitement,
        source=version.source,
    )
    return view, shown


def render_stateless(item: CommentaryItem, profile: Profile, match: MatchContext) -> CommentaryView:
    """REST rendering (no per-client memory): a rule is explained in full where it first occurs
    in the match."""
    seen = {r for r in item.rules if r not in item.first_rules}
    return render_commentary(item, profile, match, seen=seen)[0]


def tracker_facts(view: LogView, player: str, language: Lang) -> tuple[FactView, ...]:
    """Player focus at a stat tick: the player's numbers so far, from the Stats Tools."""
    meta = view.meta
    team = meta.players[player].team_id
    out: list[FactView] = []
    for metric in TRACKER_METRICS:
        result = compute(view, metric, MetricParams(team=team, player=player))
        if result.value is None:
            continue
        fact = stat_fact(meta, result, view[len(view) - 1].id)
        out.append(FactView(label=fact.label_vi if language == "vi" else fact.label_en,
                            display=fact.display))  # fmt: skip
    return tuple(out)


def metric_facts(view: LogView, metric: str, language: Lang) -> tuple[FactView, ...]:
    """'One metric only' at a stat tick: that metric for both teams, from the Stats Tools."""
    meta = view.meta
    out: list[FactView] = []
    for team in meta.team_ids:
        result = compute(view, metric, MetricParams(team=team))
        if result.value is None:
            continue
        fact = stat_fact(meta, result, view[len(view) - 1].id)
        out.append(FactView(label=fact.label_vi if language == "vi" else fact.label_en,
                            display=fact.display))  # fmt: skip
    return tuple(out)


# --------------------------------------------------------------------------- graphics, recaps


def _stat_view(stat: GraphicStat, language: Lang) -> GraphicStatView:
    return GraphicStatView(
        label=stat.label_vi if language == "vi" else stat.label_en,
        home=stat.home,
        away=stat.away,
        value=stat.value,
        highlight=stat.highlight,
    )


def render_lower_third(item: LowerThird, profile: Profile) -> LowerThirdView:
    vi = profile.language == "vi"
    return LowerThirdView(
        id=item.id,
        trigger_id=item.trigger_id,
        template=item.template,
        language=profile.language,
        title=item.title_vi if vi else item.title_en,
        subtitle=item.subtitle_vi if vi else item.subtitle_en,
        stats=tuple(_stat_view(s, profile.language) for s in item.stats),
        home=item.home,
        away=item.away,
        player=item.player,
        period=item.period,
        clock_ms=item.clock_ms,
        match_clock=item.match_clock,
        show_at_match_ms=item.show_at_match_ms,
        duration_ms=item.duration_ms,
        priority=item.priority,
        supersedes=item.supersedes,
        evidence_event_ids=item.evidence_event_ids,
        source=item.source,
        disclaimer=item.disclaimer,
    )


def render_recap(recap: Recap, profile: Profile) -> RecapView:
    text = recap.text(profile.language, profile.mode)
    return RecapView(
        id=recap.id,
        scope=recap.scope,
        language=profile.language,
        mode=profile.mode,
        title=text.title,
        paragraphs=text.paragraphs,
        key_moment_ids=recap.key_moment_ids,
        stats=tuple(_stat_view(s, profile.language) for s in recap.stats),
        revision=recap.revision,
        source=text.source,
        degraded=recap.degraded,
    )
