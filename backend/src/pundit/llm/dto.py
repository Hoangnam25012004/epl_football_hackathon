"""LLM-facing data shapes (docs/day2-design.md section 4.4).

OpenAI strict structured output does not accept the discriminated unions of the internal
contracts (`oneOf`), so the model fills these flat models instead; `dto_to_spec` converts the
answer to a ClaimSpec and reports every problem as feedback for the single retry.
Every field is required (strict mode): optional values are explicit nulls.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from pundit.agents.contracts import (
    MAX_ASSERTIONS,
    Assertion,
    ClaimContext,
    ClaimSpec,
    MatchContext,
    Subject,
)
from pundit.domain.matchtime import FULL, H1, H2, MinuteWindow, RollingWindow
from pundit.llm.ports import LlmOutputError


class Dto(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LlmWindow(Dto):
    type: Literal[
        "full",
        "first_half",
        "second_half",
        "last_minutes",
        "from_minute",
        "until_minute",
        "minute_range",
    ]
    minutes: float | None  # last_minutes
    start_minute: float | None  # from_minute, minute_range
    end_minute: float | None  # until_minute, minute_range


class LlmSubject(Dto):
    kind: Literal["team", "player", "both_teams"]
    id: str | None  # a team id or a player id from the context; null for both_teams


class LlmAssertion(Dto):
    kind: Literal["dominance", "compare", "value"]
    subject: LlmSubject
    other: LlmSubject | None
    metric: str | None
    op: Literal[">", ">=", "<", "<=", "==", "~"] | None
    value: float | None
    ratio: float | None
    window: LlmWindow
    negated: bool
    approx: bool
    text_span: str


class LlmClaimSpec(Dto):
    language: Literal["vi", "en"]
    assertions: list[LlmAssertion]
    unsupported_reason: Literal["unsupported_claim", "unknown_entity", "ambiguous_entity"] | None
    note: str


class LlmExplanation(Dto):
    text: str


def to_window(w: LlmWindow, as_of_ms: int, where: str) -> MinuteWindow | RollingWindow:
    def positive(value: float | None, name: str) -> float:
        if value is None or value <= 0:
            raise ValueError(f"{where}: window {w.type} needs {name} > 0")
        return value

    if w.type == "full":
        return FULL
    if w.type == "first_half":
        return H1
    if w.type == "second_half":
        return H2
    if w.type == "last_minutes":
        return RollingWindow.last(positive(w.minutes, "minutes"), as_of_ms)
    if w.type == "from_minute":
        return MinuteWindow(start_min=positive(w.start_minute, "start_minute"))
    if w.type == "until_minute":
        return MinuteWindow(end_min=positive(w.end_minute, "end_minute"))
    start, end = positive(w.start_minute, "start_minute"), positive(w.end_minute, "end_minute")
    if start >= end:
        raise ValueError(f"{where}: minute_range needs start_minute < end_minute")
    return MinuteWindow(start_min=start, end_min=end)


def _lookup(match: MatchContext) -> tuple[dict[str, str], dict[str, str]]:
    """Accepted spellings -> id. Ids are canonical; names and short names are forgiven."""
    teams: dict[str, str] = {}
    for team in match.teams:
        for name in (team.id, team.name, team.short_name):
            teams[name.casefold()] = team.id
    players: dict[str, str] = {}
    for player in match.players:
        for name in (player.id, player.name, player.short_name):
            players.setdefault(name.casefold(), player.id)
    return teams, players


def _subject(s: LlmSubject, match: MatchContext, where: str) -> Subject:
    teams, players = _lookup(match)
    if s.kind == "both_teams":
        return Subject(kind="both_teams")
    if s.id is None:
        raise ValueError(f"{where}: a {s.kind} subject needs an id")
    key = s.id.strip().casefold()
    if s.kind == "team":
        if key not in teams:
            raise ValueError(
                f"{where}: unknown team id {s.id!r}; use one of {sorted(set(teams.values()))}"
            )
        return Subject(kind="team", team=teams[key])
    if key not in players:
        raise ValueError(f"{where}: unknown player id {s.id!r}")
    return Subject(kind="player", player=players[key])


def _ratio(a: LlmAssertion) -> float | None:
    """Small models fill `ratio` even when the claim states none: 1 (or less than 0) means
    "no ratio". "Half as many" (0.5 with op "<") is the same claim as ratio 2 with op "<"."""
    ratio = a.ratio
    if ratio is None or ratio <= 0 or ratio == 1:
        return None
    if ratio < 1 and a.op in ("<", "<="):
        return 1 / ratio
    return ratio


def _assertion(a: LlmAssertion, ctx: ClaimContext, where: str) -> Assertion:
    match = ctx.match
    try:
        return Assertion(
            kind=a.kind,
            subject=_subject(a.subject, match, where),
            other=_subject(a.other, match, f"{where} (other)") if a.other is not None else None,
            metric=a.metric if a.kind != "dominance" else None,
            op=a.op if a.kind != "dominance" else None,
            value=a.value if a.kind == "value" else None,
            ratio=_ratio(a) if a.kind == "compare" else None,
            window=to_window(a.window, match.as_of_ms, where),
            negated=a.negated,
            approx=a.approx or a.op == "~",
            text_span=a.text_span[:300],
        )
    except ValidationError as exc:
        details = "; ".join(e["msg"] for e in exc.errors())
        raise ValueError(f"{where}: {details}") from None


def dto_to_spec(dto: LlmClaimSpec, ctx: ClaimContext) -> ClaimSpec:
    """Convert and check the model's answer; raise LlmOutputError listing every problem."""
    errors: list[str] = []
    assertions: list[Assertion] = []
    if len(dto.assertions) > MAX_ASSERTIONS:
        errors.append(f"at most {MAX_ASSERTIONS} assertions; split differently or refuse the claim")
    for index, item in enumerate(dto.assertions[:MAX_ASSERTIONS], start=1):
        try:
            assertions.append(_assertion(item, ctx, f"assertion {index}"))
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise LlmOutputError(
            "the spec does not fit the match", errors=errors, raw=dto.model_dump_json()
        )
    language = ctx.language  # the intake already decided (request or detection)
    notes = (f"llm: {dto.note[:200]}",) if dto.note else ()
    if not assertions:
        return ClaimSpec(
            language=language,
            assertions=(),
            unsupported_reason=dto.unsupported_reason or "unsupported_claim",
            notes=notes,
        )
    return ClaimSpec(language=language, assertions=tuple(assertions), notes=notes)
