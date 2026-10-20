"""Semantic checks of a ClaimSpec, shared by the rule analyst and the LLM (section 4.3).

`validate_spec` returns readable errors (empty = valid). The LLM analyst gets them back as
feedback for its single retry. Windows in the future are not an error here: the Judge answers
them with INSUFFICIENT_DATA / `future_window`.
"""

import math

from pundit.agents.contracts import Assertion, ClaimSpec, MatchContext, Subject
from pundit.agents.entities import Mention, find_entities
from pundit.agents.language import Text
from pundit.agents.numbers import extract_numbers
from pundit.agents.timescope import find_time_scopes
from pundit.domain.matchtime import (
    FULL,
    H1,
    H2,
    MS_PER_MINUTE,
    MinuteWindow,
    RollingWindow,
    minute_exact,
)
from pundit.stats.registry import REGISTRY

INTEGER_UNITS = frozenset({"count"})
NATURAL_MINUTES = (0.0, 45.0, 90.0)  # kick-off, the break, full time (exact-minute axis)


def _subject_errors(subject: Subject, ctx: MatchContext, where: str) -> list[str]:
    team_ids = {team.id for team in ctx.teams}
    errors: list[str] = []
    if subject.team is not None and subject.team not in team_ids:
        errors.append(f"{where}: team {subject.team!r} is not in this match {sorted(team_ids)}")
    if subject.player is not None:
        try:
            ctx.player(subject.player)
        except KeyError:
            errors.append(f"{where}: player {subject.player!r} is not in this match")
    return errors


def _metric_errors(a: Assertion, where: str) -> list[str]:
    if a.metric is None:
        return []
    spec = REGISTRY.get(a.metric)
    if spec is None:
        return [f"{where}: unknown metric {a.metric!r}; use a name from the catalog"]
    errors: list[str] = []
    subjects = [a.subject] + ([a.other] if a.other is not None else [])
    for subject in subjects:
        if spec.scope == "match":
            errors.append(f"{where}: {a.metric} is a match-level metric, not a team claim")
        elif subject.kind == "player" and not spec.supports_player:
            errors.append(f"{where}: {a.metric} cannot be measured for a player")
        elif subject.kind == "team" and spec.scope == "player":
            errors.append(f"{where}: {a.metric} is a player metric")
    if a.value is not None:
        if not math.isfinite(a.value) or a.value < 0:
            errors.append(f"{where}: value must be a finite number >= 0")
        elif spec.unit == "percent" and a.value > 100:
            errors.append(f"{where}: a percentage cannot exceed 100")
        elif spec.unit in INTEGER_UNITS and a.value != int(a.value):
            errors.append(f"{where}: {a.metric} is a count; the value must be a whole number")
    return errors


def _shape_errors(a: Assertion, ctx: MatchContext, where: str) -> list[str]:
    errors: list[str] = []
    if a.kind == "dominance" and a.subject.kind != "team":
        errors.append(f"{where}: dominance is only for a team")
    if a.kind == "value" and a.subject.kind == "both_teams":
        errors.append(f"{where}: a value claim needs one team or one player")
    if a.kind == "compare":
        if a.subject.kind == "both_teams":
            if a.op not in ("==", "~") or a.other is not None:
                errors.append(f"{where}: 'both teams' only works with an equality (==)")
        elif a.other is None:
            if a.subject.kind != "team":
                errors.append(f"{where}: comparing a player needs `other`")
        else:
            if a.other == a.subject:
                errors.append(f"{where}: subject and other must differ")
            if a.other.kind != a.subject.kind:
                errors.append(f"{where}: compare a team with a team or a player with a player")
        if a.ratio is not None and not (math.isfinite(a.ratio) and a.ratio > 1):
            errors.append(f"{where}: ratio must be > 1")
        if a.ratio is not None and a.op not in (">", "<"):
            errors.append(f"{where}: a ratio claim needs op '>' or '<'")
    if a.op in ("~",) and not a.approx:
        errors.append(f"{where}: op '~' needs approx=true")
    return errors


def _grounding_errors(spec: ClaimSpec, ctx: MatchContext, mentions: list[Mention]) -> list[str]:
    """Players must be named in the claim; a team claim must name a team (or 'home', 'both')."""
    named_players = {m.player for m in mentions if m.player is not None}
    named_any = any(m.kind in ("team", "both_teams") or m.player for m in mentions)
    errors: list[str] = []
    for index, a in enumerate(spec.assertions, start=1):
        for subject in (a.subject, a.other):
            if subject is None:
                continue
            if subject.player is not None and subject.player not in named_players:
                errors.append(
                    f"assertion {index}: player {subject.player} is not named in the claim"
                )
            if subject.kind in ("team", "both_teams") and not named_any:
                errors.append(f"assertion {index}: the claim does not name a team")
    return errors


def _window_minutes(window: MinuteWindow | RollingWindow) -> list[float]:
    """The minute numbers a window was built from (its bounds, or the length of "last N")."""
    if isinstance(window, RollingWindow):
        if window.start_ms == 0:  # clamped at kick-off: the length is no longer the one asked
            return []
        return [(window.end_ms - 1 - window.start_ms) / MS_PER_MINUTE]
    return [b for b in (window.start_min, window.end_min) if b is not None]


def _window_errors(spec: ClaimSpec, ctx: MatchContext, claim: Text) -> list[str]:
    """A window comes from the claim. When the rules read its time words ("hiệp 2", "sau giờ
    nghỉ", "15 phút cuối"), it is one of those windows, the whole match, or built from minutes
    the claim writes. Otherwise its minutes are written in the claim, a half boundary or the
    current minute, and a half needs time words. qwen2.5:3b read "sau giờ nghỉ" as "from 84'",
    then as "from 45'", which takes in the added time of the first half."""
    named = [scope.window for scope in find_time_scopes(claim, ctx.as_of_ms)]
    written = [n.value for n in extract_numbers(claim)]
    loose = [*written, *NATURAL_MINUTES]
    if ctx.period > 0:
        now = minute_exact(ctx.period, ctx.as_of_clock_ms)
        loose += [math.floor(now), math.ceil(now), math.floor(now) + 1]
    allowed = written if named else loose
    if named:
        said = " or ".join(dict.fromkeys(_as_llm_window(w) for w in named))
        advice = f"the claim's time words mean {said}: use exactly that window"
    else:
        advice = "the claim names no such time: use its own minutes or the whole match (full)"
    # the whole match fits a claim without time words, or a clause beside one that has them
    # ("HAR áp đảo hiệp 2 và thắng trận"); qwen2.5:3b also tried it for "sau giờ nghỉ" alone
    full_ok = not named or (
        len(spec.assertions) > 1 and any(a.window in named for a in spec.assertions)
    )
    errors: list[str] = []
    for index, a in enumerate(spec.assertions, start=1):
        if a.window in named or (a.window == FULL and full_ok):
            continue
        minutes = _window_minutes(a.window)
        if minutes and all(any(abs(m - ok) <= 0.01 for ok in allowed) for m in minutes):
            continue
        errors.append(
            f"assertion {index}: the window {_as_llm_window(a.window)} is not what the claim "
            f"says; {advice}"
        )
    return errors


def _as_llm_window(window: MinuteWindow | RollingWindow) -> str:
    """A window in the words of the analyst's answer format (feedback for its retry)."""
    if window == FULL:
        return "full"
    if window == H1:
        return "first_half"
    if window == H2:
        return "second_half"
    if isinstance(window, RollingWindow):
        minutes = _window_minutes(window)
        return f"last_minutes {minutes[0]:g}" if minutes else "the whole match so far (full)"
    start, end = window.start_min, window.end_min
    if start is not None and end is not None:
        return f"minute_range {start:g}-{end:g}"
    if start is not None:
        return f"from_minute {start:g}"
    return f"until_minute {end:g}" if end is not None else window.label()


def validate_spec(
    spec: ClaimSpec,
    ctx: MatchContext,
    *,
    claim: Text | None = None,
    mentions: list[Mention] | None = None,
) -> list[str]:
    errors: list[str] = []
    for index, a in enumerate(spec.assertions, start=1):
        where = f"assertion {index}"
        errors += _subject_errors(a.subject, ctx, where)
        if a.other is not None:
            errors += _subject_errors(a.other, ctx, f"{where} (other)")
        errors += _metric_errors(a, where)
        errors += _shape_errors(a, ctx, where)
    if claim is not None and spec.assertions and not errors:
        found = mentions if mentions is not None else find_entities(claim, ctx)
        errors += _grounding_errors(spec, ctx, found)
        errors += _window_errors(spec, ctx, claim)
    return list(dict.fromkeys(errors))
