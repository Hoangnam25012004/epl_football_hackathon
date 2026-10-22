"""From a spoken script to what the Narrator needs: segments, SSML, guard report, estimate
(docs/day5-design.md sections 4 to 7). Pure: no I/O, the same input gives the same draft."""

from collections.abc import Sequence
from dataclasses import dataclass

from pundit.agents.contracts import ProsodyPreset, RecapView, SpeechSegment
from pundit.agents.narration import MomentLine, SpeechScript, recap_script
from pundit.agents.prosody import PRESETS
from pundit.agents.speech_guard import SpeechGuardReport, SpeechProblem, check_speech
from pundit.agents.spoken import Spoken, SpokenLexicon, normalize
from pundit.agents.ssml import billable_chars, build_ssml, estimate_ms
from pundit.agents.voices import VoiceSpec


@dataclass(frozen=True)
class CueDraft:
    voice: VoiceSpec
    preset: ProsodyPreset
    segments: tuple[SpeechSegment, ...]
    ssml: str
    report: SpeechGuardReport
    estimate_ms: int
    billable_chars: int
    segment_offsets_ms: tuple[int, ...]  # where each segment starts (the mock's tones)


def draft_cue(script: SpeechScript, voice: VoiceSpec, lexicon: SpokenLexicon) -> CueDraft:
    spokens: list[Spoken] = []
    failed: list[SpeechProblem] = []
    for part in script.parts:
        try:
            spokens.append(normalize(part.text, script.language, lexicon))
        except ValueError as exc:  # a text the normalizer cannot read is never spoken
            failed.append(SpeechProblem("NUMBERS", f"cannot normalize {part.text!r}: {exc}"))
            spokens.append(Spoken("", (), ()))
    segments: list[SpeechSegment] = []
    for part, spoken in zip(script.parts, spokens, strict=True):
        if not spoken.text:
            continue
        prosody = PRESETS[part.preset]
        segments.append(
            SpeechSegment(
                text=spoken.text,
                preset=part.preset,
                rate=prosody.rate,
                pitch=prosody.pitch,
                volume=prosody.volume,
                pause_before_ms=part.pause_before_ms if segments else 0,
            )
        )
    ssml = build_ssml(segments, voice)
    report = check_speech(script, spokens, segments, ssml, voice)
    if failed:
        report = SpeechGuardReport((*failed, *report.problems))
    offsets, at = [], 0
    for segment in segments:
        at += segment.pause_before_ms
        offsets.append(at)
        at += estimate_ms([segment.model_copy(update={"pause_before_ms": 0})], script.language)
    return CueDraft(
        voice=voice,
        preset=script.main_preset,
        segments=tuple(segments),
        ssml=ssml,
        report=report,
        estimate_ms=estimate_ms(segments, script.language),
        billable_chars=billable_chars(ssml),
        segment_offsets_ms=tuple(offsets),
    )


RECAP_TARGET_MS = 120_000  # "1-2 minutes" (checklist): the recap read aloud stays under two


def fit_recap(
    view: RecapView,
    lines: Sequence[MomentLine],
    as_of_seq: int,
    voice: VoiceSpec,
    lexicon: SpokenLexicon,
    *,
    target_ms: int = RECAP_TARGET_MS,
) -> tuple[CueDraft, tuple[MomentLine, ...]]:
    """The recap read aloud and the moments it keeps: they are dropped one by one (minor
    moments first, the latest first) while the estimate is over `target_ms`."""
    kept = list(lines)
    while True:
        draft = draft_cue(recap_script(view, kept, as_of_seq), voice, lexicon)
        if draft.estimate_ms <= target_ms or not kept:
            return draft, tuple(kept)
        minor = [line for line in kept if not line.major]
        kept.remove((minor or kept)[-1])


def draft_recap(
    view: RecapView,
    lines: Sequence[MomentLine],
    as_of_seq: int,
    voice: VoiceSpec,
    lexicon: SpokenLexicon,
    *,
    target_ms: int = RECAP_TARGET_MS,
) -> CueDraft:
    return fit_recap(view, lines, as_of_seq, voice, lexicon, target_ms=target_ms)[0]
