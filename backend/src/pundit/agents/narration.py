"""Spoken scripts: what is read aloud for a commentary line, a recap and the greeting
(docs/day5-design.md section 4).

A script is a list of parts. Each part comes from a typed source that is already verified: the
commentary text (Guardrail), the club framing and the short rule texts (static, tested), the recap
paragraphs (Guardrail) and the fixed narration phrases below (static, tested). There is no way to
put a free string into a script (principle V1).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pundit.agents.contracts import (
    CommentaryItem,
    CommentaryView,
    Lang,
    Moment,
    ProsodyPreset,
    Recap,
    RecapScope,
    RecapView,
    Register,
)
from pundit.agents.prosody import (
    BURSTING,
    MAX_BURST_WORDS,
    PAUSE_AFTER_BURST_MS,
    PAUSE_AFTER_FRAMING_MS,
    PAUSE_BEFORE_EXPLAINER_MS,
    PAUSE_BETWEEN_MOMENTS_MS,
    PAUSE_BETWEEN_PARAGRAPHS_MS,
    calmer,
    preset_for,
)
from pundit.agents.rules_library import rule_view

ScriptKind = Literal["commentary", "recap", "greeting"]
ScriptSource = Literal["framing", "commentary", "rule", "recap", "moment", "phrase"]
ALLOWED_SOURCES: dict[ScriptKind, frozenset[ScriptSource]] = {
    "commentary": frozenset({"framing", "commentary", "rule"}),
    "recap": frozenset({"phrase", "recap", "moment"}),
    "greeting": frozenset({"phrase"}),
}
MOMENT_LIMITS: dict[RecapScope, int] = {"H1": 6, "FULL": 8}
# told by the recap paragraphs already, or not a moment of the story
NOT_RETOLD = frozenset(
    {"period_start", "period_end", "full_time", "stat_tick", "added_time", "lull", "substitution"}
)
_FIRST_SENTENCE = re.compile(r"^\s*([^.!?]+!)\s+(\S.*)$", re.DOTALL)


@dataclass(frozen=True)
class NarrationPhrases:
    greeting: str
    intro_h1: str
    intro_full: str
    disclosure: str
    moments: str
    outro: str
    minute: str  # "Phút {minute}:" before a moment of a recap


NARRATION: dict[Lang, NarrationPhrases] = {
    "vi": NarrationPhrases(
        greeting=(
            "Xin chào, đây là giọng đọc do AI tạo của Pundit Copilot. "
            "Trận đấu và số liệu đều là dữ liệu tổng hợp."
        ),
        intro_h1="Tóm tắt hiệp một.",
        intro_full="Tóm tắt trận đấu.",
        disclosure="Giọng đọc do AI tạo, trên dữ liệu tổng hợp.",
        moments="Những khoảnh khắc đáng chú ý.",
        outro="Cảm ơn bạn đã theo dõi cùng Pundit Copilot.",
        minute="Phút {minute}:",
    ),
    "en": NarrationPhrases(
        greeting=(
            "Hello, this is the AI-generated voice of Pundit Copilot. "
            "The match and its numbers are synthetic data."
        ),
        intro_h1="Half-time recap.",
        intro_full="Match recap.",
        disclosure="An AI-generated voice, reading synthetic data.",
        moments="The key moments.",
        outro="Thank you for following the match with Pundit Copilot.",
        minute="Minute {minute}:",
    ),
}


@dataclass(frozen=True)
class ScriptPart:
    source: ScriptSource
    text: str
    preset: ProsodyPreset
    pause_before_ms: int = 0


@dataclass(frozen=True)
class MomentLine:
    moment_id: str
    seq: int
    period: int
    display_minute: str
    text: str
    preset: ProsodyPreset
    major: bool  # importance 3 (goal, VAR, red card): the last to go when a recap is too long


@dataclass(frozen=True)
class SpeechScript:
    kind: ScriptKind
    language: Lang
    parts: tuple[ScriptPart, ...]
    scope: RecapScope | None = None  # recap: what the moments must belong to (SpeechGuard G7)
    as_of_seq: int | None = None
    moments: tuple[MomentLine, ...] = ()

    @property
    def main_preset(self) -> ProsodyPreset:
        for part in self.parts:
            if part.source == "commentary" and part.preset != "burst":
                return part.preset
        return self.parts[0].preset if self.parts else "calm"


def split_burst(text: str) -> tuple[str | None, str]:
    """ "VÀOOO! J. Ellery ghi bàn…" -> ("VÀOOO!", "J. Ellery ghi bàn…"): a short opening
    exclamation is read on its own, faster and higher (section 6.4)."""
    match = _FIRST_SENTENCE.match(text)
    if match is None or len(match.group(1).split()) > MAX_BURST_WORDS:
        return None, text
    return match.group(1).strip(), match.group(2).strip()


def commentary_script(view: CommentaryView) -> SpeechScript | None:
    """Framing, the line (with its burst), then the short rule for a beginner."""
    if not view.speak:
        return None
    preset = preset_for(view.kind, view.excitement)
    parts: list[ScriptPart] = []
    if view.framing:
        parts.append(ScriptPart("framing", view.framing, preset))
    burst, rest = split_burst(view.text) if preset in BURSTING else (None, view.text)
    if burst:
        pause = PAUSE_AFTER_FRAMING_MS if parts else 0
        parts.append(ScriptPart("commentary", burst, "burst", pause))
    if rest:
        pause = PAUSE_AFTER_BURST_MS if burst else PAUSE_AFTER_FRAMING_MS if parts else 0
        parts.append(ScriptPart("commentary", rest, preset, pause))
    if view.explanation is not None:  # always the short text: the full one takes ~30 s
        short = rule_view(view.explanation.rule, view.language, "short").text
        parts.append(ScriptPart("rule", short, "explainer", PAUSE_BEFORE_EXPLAINER_MS))
    return SpeechScript("commentary", view.language, tuple(parts))


def recap_moment_lines(
    recap: Recap,
    moments: Sequence[Moment],
    commentary: Mapping[str, CommentaryItem],
    language: Lang,
    mode: Register,
) -> tuple[MomentLine, ...]:
    """The moments a recap retells: importance 3 and the recap's key moments, in match order,
    never a goal that VAR overturned (section 4.2)."""
    overturned = {
        goal for m in moments if m.kind == "goal_overturned" for goal in m.related_moment_ids
    }
    in_scope = [
        m for m in moments if m.seq <= recap.as_of_seq and (recap.scope == "FULL" or m.period == 1)
    ]
    key = set(recap.key_moment_ids)
    major: list[MomentLine] = []
    minor: list[MomentLine] = []
    for moment in in_scope:
        if moment.id in overturned or moment.kind in NOT_RETOLD:
            continue
        if moment.importance != 3 and moment.id not in key:
            continue
        item = commentary.get(f"{moment.id}-c")
        if item is None:
            continue
        line = MomentLine(
            moment_id=moment.id,
            seq=moment.seq,
            period=moment.period,
            display_minute=moment.display_minute,
            text=item.text(language, mode).text,
            preset=calmer(preset_for(moment.kind, item.excitement)),
            major=moment.importance == 3,
        )
        (major if moment.importance == 3 else minor).append(line)
    limit = MOMENT_LIMITS[recap.scope]
    chosen = (major + minor)[:limit]
    return tuple(sorted(chosen, key=lambda line: line.seq))


def recap_script(view: RecapView, lines: Sequence[MomentLine], as_of_seq: int) -> SpeechScript:
    """Opening (AI voice, synthetic data), the recap, the key moments, closing."""
    phrases = NARRATION[view.language]
    intro = phrases.intro_h1 if view.scope == "H1" else phrases.intro_full
    parts = [ScriptPart("phrase", f"{intro} {phrases.disclosure}", "recap")]
    parts += [
        ScriptPart("recap", paragraph, "recap", PAUSE_BETWEEN_PARAGRAPHS_MS)
        for paragraph in view.paragraphs
    ]
    if lines:
        parts.append(ScriptPart("phrase", phrases.moments, "recap", PAUSE_BETWEEN_PARAGRAPHS_MS))
        parts += [
            ScriptPart(
                "moment",
                f"{phrases.minute.format(minute=line.display_minute)} {line.text}",
                line.preset,
                PAUSE_BETWEEN_MOMENTS_MS,
            )
            for line in lines
        ]
    parts.append(ScriptPart("phrase", phrases.outro, "recap", PAUSE_BETWEEN_PARAGRAPHS_MS))
    return SpeechScript(
        "recap", view.language, tuple(parts), scope=view.scope, as_of_seq=as_of_seq,
        moments=tuple(lines),
    )  # fmt: skip


def greeting_script(language: Lang) -> SpeechScript:
    return SpeechScript(
        "greeting", language, (ScriptPart("phrase", NARRATION[language].greeting, "greeting"),)
    )
