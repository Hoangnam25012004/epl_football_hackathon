"""Versioned prompts of the studio writers (docs/day3-design.md section 13.2).

The static part (role, number rules, style, glossary) is the system message, the same for every
moment of every match, so a local model reuses its prompt cache. The user message is the
FactPack in short form: what happened, who took part, the score, the facts as label + display,
and the outcome words that would contradict what happened. Numbers come only from the facts;
traces record the prompt version and length, never the text.
"""

import json
from typing import Any

from pundit.agents.contracts import FactPack, GraphicsOptions, GuardrailViolation, Lang, Register
from pundit.agents.glossary import (
    GLOSSARY_VI,
    JARGON_EN,
    JARGON_VI,
    OUTCOME_EN,
    OUTCOME_VI,
    STYLE_VI,
    forbidden_categories,
)
from pundit.agents.graphics import SHORT_LABELS, rows_of
from pundit.agents.guardrail import feedback_lines
from pundit.agents.recap import RecapPack
from pundit.agents.studio_guard import MAX_CHARS
from pundit.agents.studio_text import player_name, score_line, team_name

TALKING_PROMPT_VERSION = "talking-v2"
COMMENTARY_PROMPT_VERSION = "commentary-v2"
GRAPHICS_PROMPT_VERSION = "graphics-v1"
RECAP_PROMPT_VERSION = "recap-v2"

KIND_TEXT: dict[str, str] = {
    "goal": "a goal",
    "goal_overturned": "a goal ruled out after a VAR review",
    "red_card": "a red card",
    "yellow_card": "a yellow card",
    "big_chance": "a big chance that did not go in",
    "rocket_shot": "a very powerful shot",
    "shot": "a shot",
    "dangerous_spell": "a spell of sustained pressure by one team",
    "momentum_shift": "the momentum of the match swinging to one team",
    "substitution": "a substitution",
    "period_start": "the start of a half",
    "period_end": "half-time",
    "full_time": "full time",
    "corner": "a corner kick",
    "offside": "an offside decision",
    "var_review": "a VAR review",
    "added_time": "added time being announced",
    "stat_tick": "a regular statistics update",
    "lull": "a quiet spell: describe the state of the match",
    "tackle": "a tackle that wins the ball back",
    "foul": "a foul, and a free kick to the other team",
    "box_entry": "a pass into the penalty area",
}
LANGUAGE_NAME: dict[Lang, str] = {"vi": "Vietnamese", "en": "English"}

COMMON_RULES = """\
Numbers
- Use only numbers that appear in `facts`, written exactly as their `display` (digits; a dot \
for decimals). You may also write the minute and the score given in the input. No other \
number: no totals, differences or percentages of your own.
- Write every number with digits, zero too: "Kestrel Athletic 0", never "no entries" or \
"không có lần nào".
- A number belongs to the team or player of its fact: never give it to the other side.
People and events
- Name only the players listed in `players`, spelled as given. Never invent a name.
- Describe what happened exactly as `outcome` says, and never use a word from \
`forbidden_words`: those words contradict what happened.
Tone
- Neutral and factual: no insults, no judgement of players, clubs or referees, no speculation.
- A word that judges or compares (dominate, better, áp đảo, tốt hơn...) needs a number from \
`facts` in the same sentence.
- Complete sentences that stand alone; never start a sentence with and / but / và / nhưng / còn \
and never end with "...".
- This is a synthetic match: the teams and players are fictional."""

TALKING_SYSTEM = f"""\
You write talking points for a TV football commentator: short lines to say on air about one \
moment, in English and in Vietnamese, with verified numbers.

Output: the JSON object {{"en": [...], "vi": [...]}} with 2 or 3 lines per language.
- Line 1: what happened (who, the minute, the score). Line 2: one statistic. Line 3 (optional): \
the story of the match in numbers.
- Every line cites at least one number from `facts` (the score counts on line 1).
- One sentence per line; English lines at most {MAX_CHARS["talking_point", "en"]} characters, \
Vietnamese lines at most {MAX_CHARS["talking_point", "vi"]}.
- The Vietnamese lines say the same as the English ones, with the same numbers, in natural \
Vietnamese football language.

{COMMON_RULES}"""


def _glossary_vi() -> str:
    terms = "\n".join(f"- {term}: {meaning}" for term, meaning in GLOSSARY_VI)
    style = "\n".join(f"- {rule}" for rule in STYLE_VI)
    return f"Văn phong\n{style}\nThuật ngữ (dùng đúng nghĩa)\n{terms}"


def _commentary_system(language: Lang) -> str:
    name = LANGUAGE_NAME[language]
    jargon = ", ".join(JARGON_VI if language == "vi" else JARGON_EN)
    analyst_max = MAX_CHARS["commentary", "analyst"]
    casual_max = MAX_CHARS["commentary", "casual"]
    text = f"""\
You are a football commentator writing live commentary in {name} for one moment of a match. \
Write two versions of the same line.

Output: the JSON object {{"analyst": "...", "casual": "..."}}, both in {name}.
- analyst: for experts. One or two sentences, at most {analyst_max} characters, citing at least \
one number from `facts`.
- casual: for a fan new to football. One short sentence, at most {casual_max} characters, \
plain words, at most one number besides the minute (the score counts as one number). Never use \
these terms in casual: {jargon}.

{COMMON_RULES}"""
    return f"{text}\n\n{_glossary_vi()}" if language == "vi" else text


GRAPHICS_SYSTEM = """\
You choose the statistics shown on a TV lower-third graphic for one moment of a synthetic \
football match.
Pick one template from `templates` and 1 to 4 keys from `stats`, the most telling first. Copy \
the keys exactly; never invent one.
Output: the JSON object {"template": "...", "stat_keys": ["...", "..."]}."""


def _recap_system(language: Lang, mode: Register) -> str:
    name = LANGUAGE_NAME[language]
    paragraph_max = MAX_CHARS["recap", "paragraph"]
    if mode == "analyst":
        voice = (
            "for analysts and studio guests: 120 to 200 words in all, with the key numbers "
            "(possession, shots, xG, half by half when given)"
        )
    else:
        jargon = ", ".join(JARGON_VI if language == "vi" else JARGON_EN)
        voice = (
            "for fans new to football: 60 to 120 words in all, plain words, at most one "
            f"number per paragraph besides minutes and the score, none of these terms: {jargon}"
        )
    text = f"""\
You write the {name} recap of a football match {voice}.

Output: the JSON object {{"title": "...", "paragraphs": ["...", ...]}} with 3 to 5 paragraphs \
of at most {paragraph_max} characters each, in this order: the result; the goals (minute and \
scorer, from `goals` only); the story in numbers; cards and VAR decisions; the key moments.
- Goals in `goals_ruled_out` did not count: never count them as goals.

{COMMON_RULES}"""
    return f"{text}\n\n{_glossary_vi()}" if language == "vi" else text


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _forbidden(pack: FactPack, languages: tuple[Lang, ...]) -> list[str]:
    words: list[str] = []
    for category in sorted(forbidden_categories(pack.trigger)):
        if "vi" in languages:
            words += OUTCOME_VI.get(category, ())
        if "en" in languages:
            words += OUTCOME_EN.get(category, ())
    return words


def _moment(pack: FactPack) -> dict[str, Any]:
    trigger, match = pack.trigger, pack.match
    moment: dict[str, Any] = {
        "what": KIND_TEXT.get(trigger.kind, trigger.kind.replace("_", " ")),
        "minute": trigger.display_minute,
    }
    if trigger.team is not None:
        moment["team"] = team_name(match, trigger.team)
    if trigger.outcome is not None:
        moment["outcome"] = trigger.outcome
    if trigger.details:
        moment["details"] = dict(sorted(trigger.details.items()))
    return moment


def _players(pack: FactPack) -> list[dict[str, str]]:
    trigger, match = pack.trigger, pack.match
    found: dict[str, str] = {}
    for role, player in sorted(trigger.roles.items()):
        found.setdefault(player, role)
    if trigger.player is not None:
        found.setdefault(trigger.player, "player")
    for fact in pack.facts:
        if fact.player is not None:
            found.setdefault(fact.player, "statistic")
    return [{"name": player_name(match, pid), "role": role} for pid, role in found.items()]


def _facts(pack: FactPack, language: Lang) -> list[dict[str, str]]:
    first = {key: index for index, key in enumerate(pack.headline)}
    ordered = sorted(pack.facts, key=lambda f: first.get(f.key, len(first)))
    return [
        {"label": f.label_vi if language == "vi" else f.label_en, "display": f.display}
        for f in ordered
    ]


def _feedback(user: dict[str, Any], feedback: list[GuardrailViolation] | None) -> None:
    if feedback:
        user["your_previous_answer_was_rejected"] = feedback_lines(feedback)


def talking_messages(
    pack: FactPack, feedback: list[GuardrailViolation] | None = None
) -> tuple[str, str]:
    user: dict[str, Any] = {
        "moment": _moment(pack),
        "score": score_line(pack.match, "en"),
        "players": _players(pack),
        "facts_en": _facts(pack, "en"),
        "facts_vi": _facts(pack, "vi"),
        "forbidden_words": _forbidden(pack, ("en", "vi")),
    }
    _feedback(user, feedback)
    return TALKING_SYSTEM, _compact(user)


def commentary_messages(
    pack: FactPack, language: Lang, feedback: list[GuardrailViolation] | None = None
) -> tuple[str, str]:
    user: dict[str, Any] = {
        "language": language,
        "moment": _moment(pack),
        "score": score_line(pack.match, language),
        "players": _players(pack),
        "facts": _facts(pack, language),
        "forbidden_words": _forbidden(pack, (language,)),
    }
    _feedback(user, feedback)
    return _commentary_system(language), _compact(user)


def graphics_messages(pack: FactPack, options: GraphicsOptions) -> tuple[str, str]:
    rows = rows_of(pack)
    stats: list[dict[str, str]] = []
    for key in options.stat_keys:
        stat = rows[key].stat
        entry = {"key": key, "label": SHORT_LABELS.get(key.rstrip(":"), (key, key))[1]}
        if stat.value is not None:
            entry["value"] = stat.value
        else:
            entry["home"], entry["away"] = stat.home or "", stat.away or ""
        stats.append(entry)
    user = {
        "moment": _moment(pack),
        "templates": list(options.templates),
        "stats": stats,
    }
    return GRAPHICS_SYSTEM, _compact(user)


def recap_messages(
    pack: RecapPack,
    language: Lang,
    mode: Register,
    feedback: list[GuardrailViolation] | None = None,
) -> tuple[str, str]:
    match = pack.match

    def goal(line: Any) -> dict[str, str]:
        entry = {"minute": line.minute, "team": team_name(match, line.team)}
        if line.scorer:
            entry["scorer"] = player_name(match, line.scorer)
        return entry

    user: dict[str, Any] = {
        "language": language,
        "scope": "first half" if pack.scope == "H1" else "full match",
        "score": score_line(match, language),
        "goals": [goal(g) for g in pack.goals],
        "goals_ruled_out": [goal(g) for g in pack.overturned],
        "cards": [
            {"minute": c.minute, "player": player_name(match, c.player), "card": c.color}
            for c in pack.cards
        ],
        "key_moments": [
            {
                "minute": m.display_minute,
                "what": KIND_TEXT.get(m.kind, m.kind),
                **({"team": team_name(match, m.team)} if m.team else {}),
            }
            for m in pack.key_moments
        ],
        "players": sorted(player_name(match, p) for p in pack.players),
        "facts": [
            {"label": f.label_vi if language == "vi" else f.label_en, "display": f.display}
            for f in pack.facts
        ],
    }
    _feedback(user, feedback)
    return _recap_system(language, mode), _compact(user)
