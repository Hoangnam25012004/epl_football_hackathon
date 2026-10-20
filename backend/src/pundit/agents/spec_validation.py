"""Semantic checks of a ClaimSpec, shared by the rule analyst and the LLM (section 4.3).

`validate_spec` returns readable errors (empty = valid). The LLM analyst gets them back as
feedback for its single retry. Windows in the future are not an error here: the Judge answers
them with INSUFFICIENT_DATA / `future_window`.
"""

import math

from pundit.agents.contracts import Assertion, ClaimSpec, MatchContext, Subject
from pundit.agents.entities import Mention, find_entities
from pundit.agents.language import Text
from pundit.stats.registry import REGISTRY

INTEGER_UNITS = frozenset({"count"})


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
    return list(dict.fromkeys(errors))
