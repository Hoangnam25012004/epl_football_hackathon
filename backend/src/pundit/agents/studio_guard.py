"""Guardrail of the studio (docs/day3-design.md section 12).

`check_studio` runs the Day 2 checks (numbers, comparisons, owners of numbers, unsafe words)
against the facts of a FactPack, then the studio rules:

- UNVERIFIED_NUMBER also when a metric is written next to a number ("4 shots", "momentum
  0.88") and no fact of that metric has the value: a real number given the wrong name;
- WRONG_ENTITY: every player named takes part in the moment, or owns one of its facts, and the
  goalkeeper of a shot is never the one who shoots;
- WRONG_OUTCOME: no "ghi bàn" for a shot that was saved, no red card words on a yellow card;
- FRAGMENT: the text stands alone;
- and for text a model wrote: WRONG_LANGUAGE, WRONG_REGISTER (casual: at most one number and no
  jargon; analyst: at least one number when the pack has some) and UNGROUNDED (a talking point
  cites a number in every line; elsewhere, a sentence that judges or compares cites one).

Templates are checked too (principle S4); the mock LLM answers with the templates, so every
template also passes the model rules.
"""

import re
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
from pundit.agents.glossary import (
    REASONS,
    SUMMARY_KINDS,
    assessment_terms,
    forbidden_categories,
    jargon_terms,
)
from pundit.agents.glossary import outcome_terms as _outcome_terms
from pundit.agents.guardrail import (
    GuardContext,
    _facts_for,
    _hint,
    _sentences,
    _stat_numbers,
    check,
    fragment_violations,
    rounding_match,
)
from pundit.agents.language import Hit, Term, Text, detect_language, find_terms, vi_mode
from pundit.agents.lexicon import lexicon
from pundit.agents.numbers import Num, extract_numbers
from pundit.stats.registry import REGISTRY

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
# Metric words next to a number: the claim lexicon plus words of studio texts
EXTRA_METRIC_WORDS: dict[str, tuple[str, ...]] = {
    "chaos_index": ("chỉ số hỗn loạn", "hỗn loạn", "chaos index", "chaos"),
    "box_entries": ("penalty-area entries", "entries into the area", "box entry"),
    "player_distance": ("quãng chạy", "distance covered"),
}
KEY_METRICS = {  # facts of moments and cues have no metric name: their key prefix tells it
    "spell_shots": "shots",
    "spell_box_entries": "box_entries",
    "shot_xg": "xg",
    "shot_speed": "shot_speed_max",
}
METRIC_FAMILY: dict[str, frozenset[str]] = {
    "cards": frozenset({"cards", "yellow_cards", "red_cards"}),
    "possession": frozenset({"possession", "possession_time"}),
}
# counts are whole numbers: "the xG for the shot was 0.36" does not name 0.36 a shot count
COUNT_METRICS = frozenset(
    {name for name, spec in REGISTRY.items() if spec.unit == "count"}
    | {"cards", "var_overturns", "score"}
)
MAX_GAP_WORDS = 3  # "possession ratio of 42%", "kiểm soát bóng HAR 57%"
_GAP_STOP = re.compile(r"[\d,.;:!?()]")
_KEEPER_SHOOTS = (
    re.compile(r"^\s*(?:'|\u2019)s\s+(?:[\w-]+\s+)?(?:shot|strike|effort|header|attempt)\b", re.I),
    re.compile(
        r"^\s+(?:also\s+|then\s+)?(?:shoots|shot|fires|fired|misses|missed|strikes|struck|"
        r"heads|headed)\b",
        re.I,
    ),
    re.compile(r"^\s+(?:cũng\s+)?(?:sút|dứt điểm|bỏ lỡ|đánh đầu|tung cú sút)(?!\w)", re.I),
)
_SCORES = (  # a player named right before these words is said to have scored
    re.compile(
        r"^\s+(?:also\s+|then\s+)?(?:scores|scored|nets|netted|finds the net|found the net|"
        r"heads in|headed in|equalises|equalizes|equalised|equalized)\b",
        re.I,
    ),
    re.compile(
        r"^\s+(?:đã\s+|cũng\s+)?(?:ghi bàn|lập công|ghi được bàn|tung lưới|gỡ hòa|gỡ hoà|"
        r"mở tỷ số|nâng tỷ số)(?!\w)",
        re.I,
    ),
)
ORDINAL_VALUES = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "đầu tiên": 1, "thứ nhất": 1, "thứ hai": 2, "thứ ba": 3, "thứ tư": 4, "thứ năm": 5,
}  # fmt: skip
_ORDINAL_GOAL_EN = re.compile(
    r"\b(?:(his|her)\s+|(their)\s+)?(first|second|third|fourth|fifth)\s+goal\b", re.I
)
_ORDINAL_GOAL_VI = re.compile(
    r"\bbàn(?:\s+thắng)?\s+(đầu tiên|thứ nhất|thứ hai|thứ ba|thứ tư|thứ năm)"
    r"(?:\s+(của|cho)\s+(anh ấy|anh|cô ấy)?)?",
    re.I,
)
# "một" is an article before these nouns ("một cơ hội ngon ăn" is "a big chance"), not a count
_ARTICLE_MOT = re.compile(
    r"(?<!\w)(một)(?=\s+(?:cơ hội|tình huống|pha tấn công|pha bóng|pha phối hợp|pha ép sân|"
    r"đợt|khoảnh khắc|trận đấu|trận cầu)(?!\w))",
    re.I,
)


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
def _metric_terms() -> list[Term[str]]:
    languages: tuple[Lang, ...] = ("vi", "en")
    terms = [
        Term(term.phrase, term.payload.metric, term.mode)
        for language in languages
        for term in lexicon(language)
        if term.payload.cat == "metric" and term.payload.metric is not None
    ]
    terms += [
        Term(word, metric, vi_mode(word))
        for metric, words in EXTRA_METRIC_WORDS.items()
        for word in words
    ]
    return terms


def fact_metric(fact: Fact) -> str | None:
    if fact.metric is not None:
        return fact.metric
    return KEY_METRICS.get(fact.key.split(":", 1)[0])


def named_metric(written: Text, number: Num, mentions: list[Hit[str]]) -> str | None:
    """The metric written next to a number ("4 shots", "momentum 0.88", "cầm bóng 57%"), if
    any: the closest metric word with at most 3 words, no digit and no punctuation between."""
    raw = written.raw
    best: tuple[float, str] | None = None
    for hit in mentions:
        if number.decimals and hit.payload in COUNT_METRICS:
            continue
        if hit.start >= number.end:
            gap, rank_bonus = raw[number.end : hit.start], 0.0
        elif hit.end <= number.start:
            gap, rank_bonus = raw[hit.end : number.start], 0.5  # a word after wins a tie
        else:
            continue
        words = len(gap.split())
        if _GAP_STOP.search(gap) or words > MAX_GAP_WORDS:
            continue
        rank = words + rank_bonus
        if best is None or rank < best[0]:
            best = (rank, hit.payload)
    return best[1] if best is not None else None


def _metric_violations(
    written: Text, numbers: list[Num], facts: tuple[Fact, ...]
) -> list[GuardrailViolation]:
    """A real number under the wrong name ("4 shots" when 4 is the box entries)."""
    mentions = find_terms(written, _metric_terms())
    if not mentions:
        return []
    found: list[GuardrailViolation] = []
    for number in _stat_numbers(numbers):
        if _hint(written, number).units == frozenset({"kmh"}):
            continue  # "a 105.9 km/h shot": the unit says what the number is
        metric = named_metric(written, number, mentions)
        if metric is None:
            continue
        allowed = METRIC_FAMILY.get(metric, frozenset({metric}))
        same = [f for f in facts if fact_metric(f) in allowed and not f.derived]
        if any(rounding_match(number.signed, number.decimals, f.value) for f in same):
            continue
        shown = written.raw[number.start : number.end]
        known = ", ".join(sorted({f.display for f in same})[:4])
        detail = (
            f"{shown} is not a {metric} figure of this moment ({known})"
            if same
            else f"{shown} is given as {metric}, but no {metric} figure was verified"
        )
        found.append(GuardrailViolation(code="UNVERIFIED_NUMBER", detail=detail, span=shown))
    return found


def _role_violations(
    written: Text, mentions: list[Mention], scope: StudioScope
) -> list[GuardrailViolation]:
    """Roles swapped: the goalkeeper named as the one who shoots or scores ("Brandvold's
    shot"), or anyone but the scorer said to have scored the goal of this moment."""
    trigger = scope.trigger
    if trigger is None:
        return []
    keeper = trigger.roles.get("goalkeeper")
    scorer = trigger.roles.get("scorer")
    found: list[GuardrailViolation] = []
    for mention in mentions:
        if mention.kind != "player" or mention.player is None:
            continue
        after = written.raw[mention.end : mention.end + 60]
        name = scope.match.player(mention.player).name
        shoots = any(pattern.search(after) for pattern in _KEEPER_SHOOTS)
        scores = any(pattern.search(after) for pattern in _SCORES)
        if mention.player == keeper and (shoots or scores):
            did = "scored" if scores else "shot"
            found.append(
                GuardrailViolation(
                    code="WRONG_ENTITY",
                    detail=f"{name} is the goalkeeper of this moment, not the one who {did}",
                    span=name,
                )
            )
        elif scores and scorer is not None and mention.player != scorer:
            right = scope.match.player(scorer).name
            found.append(
                GuardrailViolation(
                    code="WRONG_ENTITY",
                    detail=f"{name} did not score this goal; {right} did",
                    span=name,
                )
            )
    return found


def _owner_of_goal_count(
    start: int, pronoun: str | None, scope: StudioScope, mentions: list[Mention]
) -> tuple[str, str] | None:
    """("player", id) or ("team", id) of "his second goal" / "bàn thắng thứ hai cho X"."""
    trigger = scope.trigger
    if pronoun is not None and trigger is not None and "scorer" in trigger.roles:
        return "player", trigger.roles["scorer"]
    before = [m for m in mentions if m.end <= start and m.player is not None]
    if pronoun is not None and before:
        return "player", before[-1].player or ""
    return None


def _ordinal_violations(
    written: Text, scope: StudioScope, mentions: list[Mention]
) -> list[GuardrailViolation]:
    """ "His second goal" is a number too: it must be the scorer's (or a team's) goal count."""
    found: list[GuardrailViolation] = []
    score_facts = [f for f in scope.facts if f.metric == "score" and not f.derived]
    hits: list[tuple[int, int, int, str | None]] = []
    for match in _ORDINAL_GOAL_EN.finditer(written.raw):
        pronoun = match.group(1)
        hits.append((match.start(), match.end(), ORDINAL_VALUES[match.group(3).lower()], pronoun))
    for match in _ORDINAL_GOAL_VI.finditer(written.raw):
        pronoun = match.group(3) if match.group(2) == "của" else None
        end = match.end() if match.group(3) else match.end(1)
        hits.append((match.start(), end, ORDINAL_VALUES[match.group(1).lower()], pronoun))
    for start, end, value, pronoun in hits:
        owner = _owner_of_goal_count(start, pronoun, scope, mentions)
        if owner is not None:
            kind, owner_id = owner
            candidates = [
                f for f in score_facts if (f.player if kind == "player" else f.team) == owner_id
            ]
        else:
            candidates = score_facts
        if any(f.value == value for f in candidates):
            continue
        phrase = written.raw[start:end]
        known = ", ".join(sorted({f.display for f in candidates})) or "none verified"
        found.append(
            GuardrailViolation(
                code="UNVERIFIED_NUMBER",
                detail=f"{phrase!r} does not match the verified goal count ({known})",
                span=phrase,
            )
        )
    return found


def mask_articles(text: str, language: Lang) -> str:
    """Vietnamese "một" as an article ("một cơ hội ngon ăn") is not the number 1. The word is
    replaced by "mot" (same length) in the copy the checks read."""
    if language != "vi":
        return text
    return _ARTICLE_MOT.sub(lambda m: "mot" if m.group(1).islower() else "Mot", text)


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
    text = mask_articles(text, language)
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
    violations += _role_violations(written, mentions, scope)
    violations += _metric_violations(written, extract_numbers(written), scope.facts)
    violations += _ordinal_violations(written, scope, mentions)
    outcome_words, jargon, assessment, comparative = _terms()
    trigger = scope.trigger
    if trigger is not None:
        forbidden = forbidden_categories(trigger, model_text=model_text)
        for hit in find_terms(written, outcome_words):
            if hit.payload in forbidden:
                word = written.raw[hit.start : hit.end]
                reason = REASONS.get(hit.payload)
                if reason is None and hit.payload == "goal" and trigger.kind in SUMMARY_KINDS:
                    reason = "a summary line gives the score; the recap tells the goals"
                violations.append(
                    GuardrailViolation(
                        code="WRONG_OUTCOME",
                        detail=f"{word!r} does not fit what happened "
                        f"({reason or trigger.outcome or trigger.kind})",
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
