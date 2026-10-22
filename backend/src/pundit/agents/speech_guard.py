"""SpeechGuard: the last check before anything is read aloud (docs/day5-design.md section 7).

G1 sources, G2 numbers (read back with an independent reader, in order, no digit left), G3 the
language of the voice, G4 lengths, G5 SSML, G6 banned words, G7 the moments of a recap belong to
its scope. A cue that fails is never read, not even by the browser (principle V1).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from pundit.agents.contracts import MAX_SPEECH_SEGMENTS, SpeechSegment
from pundit.agents.guardrail import UNSAFE_TERMS
from pundit.agents.language import Text, detect_language, find_terms
from pundit.agents.narration import ALLOWED_SOURCES, ScriptKind, SpeechScript
from pundit.agents.numbers import extract_numbers
from pundit.agents.prosody import PRESETS
from pundit.agents.spoken import Spoken
from pundit.agents.spoken_numbers import parse_vi_number, parse_vi_ordinal
from pundit.agents.ssml import check_ssml
from pundit.agents.voices import VoiceSpec

SpeechCode = Literal["SOURCE", "NUMBERS", "LANGUAGE", "LENGTH", "SSML", "UNSAFE", "SCOPE"]
MAX_SPOKEN_CHARS: dict[ScriptKind, int] = {"commentary": 600, "recap": 3000, "greeting": 200}
_DIGIT = re.compile(r"\d")


@dataclass(frozen=True)
class SpeechProblem:
    code: SpeechCode
    detail: str


@dataclass(frozen=True)
class SpeechGuardReport:
    problems: tuple[SpeechProblem, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.problems

    def codes(self) -> list[str]:
        return sorted({p.code for p in self.problems})


def _written_values(text: str) -> list[Decimal]:
    """The numbers of a written text, signed, as the Guardrail finds them."""
    values = []
    for num in extract_numbers(Text.of(text)):
        if num.word:
            value = Decimal(int(num.value))
        else:
            raw = Text.of(text).raw[num.start : num.end]
            digits = re.match(r"\d+(?:[.,]\d+)*", raw)
            if digits is None:
                raise ValueError(f"no digits in {raw!r}")
            token = digits.group(0)
            if re.fullmatch(r"\d{1,3}(?:,\d{3})+", token):
                value = Decimal(token.replace(",", ""))
            else:
                value = Decimal(token.replace(",", "."))
        values.append(-value if num.negative else value)
    return values


def _number_problems(written: str, spoken: Spoken) -> list[str]:
    problems = []
    if _DIGIT.search(spoken.text):
        problems.append(f"a digit is left in {spoken.text!r}")
    expected = _written_values(written)
    if len(expected) != len(spoken.numbers):
        problems.append(f"{len(expected)} numbers written, {len(spoken.numbers)} spoken")
        return problems
    for want, number in zip(expected, spoken.numbers, strict=True):
        words = spoken.text[number.start : number.end]
        try:
            got = (
                Decimal(parse_vi_ordinal(words.lower()))
                if number.kind == "ordinal"
                else parse_vi_number(words)
            )
        except (ValueError, InvalidOperation) as exc:
            problems.append(f"{words!r} does not read back: {exc}")
            continue
        if got != want:
            problems.append(f"{number.written!r} written, {words!r} reads {got}")
    return problems


def check_speech(
    script: SpeechScript,
    spokens: Sequence[Spoken],
    segments: Sequence[SpeechSegment],
    ssml: str,
    voice: VoiceSpec,
) -> SpeechGuardReport:
    found: list[SpeechProblem] = []

    def add(code: SpeechCode, detail: str) -> None:
        found.append(SpeechProblem(code, detail))

    allowed = ALLOWED_SOURCES[script.kind]
    for part in script.parts:  # G1
        if part.source not in allowed:
            add("SOURCE", f"{part.source} is not a source of a {script.kind} script")
    if len(spokens) != len(script.parts):
        add("NUMBERS", "every part must be normalized")
    for part, spoken in zip(script.parts, spokens, strict=False):  # G2
        for detail in _number_problems(part.text, spoken):
            add("NUMBERS", detail)
    for segment in segments:  # the prosody of a segment is the one of its preset
        preset = PRESETS[segment.preset]
        if (segment.rate, segment.pitch, segment.volume) != (
            preset.rate,
            preset.pitch,
            preset.volume,
        ):
            add("SSML", f"segment prosody differs from preset {segment.preset}")
    joined, masked = _joined(spokens)
    if joined and detect_language(Text(joined), masked) != voice.language:  # G3
        add("LANGUAGE", f"the script is not in {voice.language}")
    if not segments:  # G4
        add("LENGTH", "nothing to read")
    if len(segments) > MAX_SPEECH_SEGMENTS:
        add("LENGTH", f"{len(segments)} segments")
    if len(joined) > MAX_SPOKEN_CHARS[script.kind]:
        add("LENGTH", f"{len(joined)} characters for a {script.kind}")
    for detail in check_ssml(ssml, voice):  # G5
        add("SSML", detail)
    for hit in find_terms(Text.of(joined), UNSAFE_TERMS) if joined else []:  # G6
        add("UNSAFE", f"banned term {hit.payload!r}")
    if script.kind == "recap":  # G7
        if script.as_of_seq is None or script.scope is None:
            add("SCOPE", "a recap script needs its scope")
        else:
            for line in script.moments:
                if line.seq > script.as_of_seq or (script.scope == "H1" and line.period != 1):
                    add("SCOPE", f"moment {line.moment_id} is outside {script.scope}")
    return SpeechGuardReport(tuple(found))


def _joined(spokens: Sequence[Spoken]) -> tuple[str, list[tuple[int, int]]]:
    """The whole spoken script and the spans of its names (masked by the language check)."""
    text: list[str] = []
    masked: list[tuple[int, int]] = []
    length = 0
    for spoken in spokens:
        if not spoken.text:
            continue
        if text:
            text.append(" ")
            length += 1
        masked += [(length + a, length + b) for a, b in spoken.names]
        text.append(spoken.text)
        length += len(spoken.text)
    return "".join(text), masked
