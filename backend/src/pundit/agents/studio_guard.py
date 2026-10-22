"""Guardrail of the studio (docs/day3-design.md section 12).

`check_studio` runs the Day 2 checks (numbers, comparisons, owners of numbers, unsafe words)
against the facts of a FactPack, then the studio rules:

- WRONG_ENTITY: every player named takes part in the moment, or owns one of its facts;
- WRONG_OUTCOME: no "ghi bàn" for a shot that was saved, no red card words on a yellow card;
- FRAGMENT: the text stands alone;
- and for text a model wrote: WRONG_LANGUAGE, WRONG_REGISTER (casual: at most one number and no
  jargon; analyst: at least one number when the pack has some) and UNGROUNDED (a talking point
  cites a number in every line; elsewhere, a sentence that judges or compares cites one).

Templates are checked too (principle S4); the mock LLM answers with the templates, so every
template also passes the model rules.
"""

from dataclasses import dataclass
from functools import cache
from typing import Literal

from pundit.agents.contracts import (
    Fact,
    FactPack,
    GuardrailReport,
    GuardrailViolation,
    Lang,
    MatchContext,
    Register,
    Trigger,
)
from pundit.agents.entities import Mention, find_entities
from pundit.agents.glossary import assessment_terms, forbidden_categories, jargon_terms
from pundit.agents.glossary import outcome_terms as _outcome_terms
from pundit.agents.guardrail import (
    GuardContext,
    _facts_for,
    _sentences,
    _stat_numbers,
    check,
    fragment_violations,
)
from pundit.agents.language import Hit, Term, Text, detect_language, find_terms
from pundit.agents.numbers import Num, extract_numbers

StudioProduct = Literal["talking_point", "commentary", "recap", "graphic"]
MAX_CHARS: dict[tuple[StudioProduct, str], int] = {
    ("talking_point", "en"): 140,
    ("talking_point", "vi"): 160,
    ("commentary", "analyst"): 220,
    ("commentary", "casual"): 160,
    ("recap", "paragraph"): 300,
    ("graphic", "title"): 32,
    ("graphic", "subtitle"): 48,
}
COMPARATIVE = ("hơn", "than", "more", "fewer", "less")
MIN_WORDS_FOR_LANGUAGE = 3


@dataclass(frozen=True)
class StudioScope:
    """What a studio text may talk about."""

    facts: tuple[Fact, ...]
    match: MatchContext
    players: frozenset[str]  # players who may be named
    trigger: Trigger | None  # None for a recap
    window_numbers: frozenset[float]
    clock_display: str
    has_numbers: bool  # there is something to cite (analyst register)


def scope_of(pack: FactPack) -> StudioScope:
    trigger = pack.trigger
    players = set(trigger.involved_players) | {f.player for f in pack.facts if f.player}
    if trigger.player is not None:
        players.add(trigger.player)
    return StudioScope(
        facts=pack.facts,
        match=pack.match,
        players=frozenset(players),
        trigger=trigger,
        window_numbers=frozenset(pack.window_numbers),
        clock_display=trigger.display_minute,
        has_numbers=bool(pack.headline),
    )


@cache
def _terms() -> tuple[list[Term[str]], list[Term[str]], list[Term[str]], list[Term[str]]]:
    comparative = [Term(w, w, "lower") for w in COMPARATIVE]
    return _outcome_terms(), jargon_terms(), assessment_terms(), comparative


def stat_number_count(numbers: list[Num]) -> int:
    """Numbers of statistics in a text; an "a-b" pair counts once, minutes and halves never."""
    count = 0
    for index, number in enumerate(numbers):
        if number.minute or number.period:
            continue
        if number.pair is not None and number.pair < index:
            continue
        count += 1
    return count


def cited_keys(text: str, facts: tuple[Fact, ...]) -> tuple[str, ...]:
    """Keys of the facts the numbers of `text` match (for TalkingLine.fact_keys)."""
    written = Text.of(text)
    keys: list[str] = []
    for number in _stat_numbers(extract_numbers(written)):
        for fact in _facts_for(written, number, facts):
            if fact.key not in keys:
                keys.append(fact.key)
    return tuple(keys)


def check_studio(
    text: str,
    scope: StudioScope,
    *,
    language: Lang,
    product: StudioProduct,
    mode: Register | None = None,
    part: str | None = None,
    model_text: bool = False,
) -> GuardrailReport:
    match = scope.match
    home, away = match.teams[0].id, match.teams[1].id
    context = GuardContext(
        verdict=None,
        clock_display=scope.clock_display,
        score=(match.score.get(home, 0), match.score.get(away, 0)),
        window_numbers=scope.window_numbers,
        match=match,
    )
    base = check(text, scope.facts, context)
    violations = [v for v in base.violations if v.code != "TOO_LONG"]
    if not text.strip():
        return GuardrailReport(passed=False, violations=tuple(violations))
    limit = MAX_CHARS.get((product, part or mode or language))
    if limit is not None and len(text) > limit:
        violations.append(
            GuardrailViolation(
                code="TOO_LONG", detail=f"{len(text)} characters; the limit is {limit}"
            )
        )
    if product != "graphic":
        violations += fragment_violations(text)
    written = Text.of(text)
    mentions = find_entities(written, match)
    violations += _entity_violations(mentions, scope)
    outcome_words, jargon, assessment, comparative = _terms()
    if scope.trigger is not None:
        forbidden = forbidden_categories(scope.trigger)
        for hit in find_terms(written, outcome_words):
            if hit.payload in forbidden:
                word = written.raw[hit.start : hit.end]
                violations.append(
                    GuardrailViolation(
                        code="WRONG_OUTCOME",
                        detail=f"{word!r} does not fit what happened ({scope.trigger.outcome})",
                        span=word,
                    )
                )
    if model_text:
        numbers = extract_numbers(written)
        jargon_hits = find_terms(written, jargon)
        masked = [(m.start, m.end) for m in mentions] + [(h.start, h.end) for h in jargon_hits]
        words = [w for w in written.raw.split() if any(ch.isalpha() for ch in w)]
        if len(words) >= MIN_WORDS_FOR_LANGUAGE and detect_language(written, masked) != language:
            violations.append(
                GuardrailViolation(
                    code="WRONG_LANGUAGE",
                    detail=f"the text is not in {'Vietnamese' if language == 'vi' else 'English'}",
                )
            )
        violations += _register_violations(numbers, jargon_hits, written, scope, mode, product)
        violations += _ungrounded_violations(written, numbers, product, assessment, comparative)
    return GuardrailReport(passed=not violations, violations=tuple(violations))


def _entity_violations(mentions: list[Mention], scope: StudioScope) -> list[GuardrailViolation]:
    found: list[GuardrailViolation] = []
    for mention in mentions:
        if mention.kind != "player":
            continue
        ids = {i for i in (*mention.candidates, mention.player) if i}
        if ids & scope.players:
            continue
        if mention.player is not None:
            name = scope.match.player(mention.player).name
        else:
            name = "/".join(sorted(ids))
        found.append(
            GuardrailViolation(
                code="WRONG_ENTITY",
                detail=f"{name} is not part of this moment",
                span=name,
            )
        )
    return found


def _register_violations(
    numbers: list[Num],
    jargon_hits: list[Hit[str]],
    written: Text,
    scope: StudioScope,
    mode: Register | None,
    product: StudioProduct,
) -> list[GuardrailViolation]:
    if mode is None:
        return []
    count = stat_number_count(numbers)
    if mode == "casual":
        problems = []
        if count > 1:
            problems.append(f"{count} numbers (casual allows one)")
        if jargon_hits:
            words = sorted({written.raw[h.start : h.end] for h in jargon_hits})
            problems.append(f"jargon {', '.join(words)}")
        if problems:
            return [GuardrailViolation(code="WRONG_REGISTER", detail="; ".join(problems))]
        return []
    if product == "commentary" and scope.has_numbers and count == 0:
        return [
            GuardrailViolation(
                code="WRONG_REGISTER", detail="an analyst line cites at least one number"
            )
        ]
    return []


def _ungrounded_violations(
    written: Text,
    numbers: list[Num],
    product: StudioProduct,
    assessment: list[Term[str]],
    comparative: list[Term[str]],
) -> list[GuardrailViolation]:
    if product == "graphic":
        return []
    grounding = [n.start for n in numbers if not n.minute and not n.period]
    judging = [h.start for h in find_terms(written, assessment)]
    judging += [h.start for h in find_terms(written, comparative)]
    found: list[GuardrailViolation] = []
    # a talking-point line is one statement: ";" inside it does not end a sentence
    spans = (
        _sentences(Text(written.raw.replace(";", ",")))
        if product == "talking_point"
        else (_sentences(written))
    )
    for start, end in spans:
        cited = any(start <= g < end for g in grounding)
        if cited:
            continue
        if product == "talking_point" or any(start <= j < end for j in judging):
            sentence = written.raw[start:end].strip()
            found.append(
                GuardrailViolation(
                    code="UNGROUNDED",
                    detail=f"{sentence[:80]!r} has no number from the facts; cite one or drop it",
                    span=sentence[:120],
                )
            )
    return found
