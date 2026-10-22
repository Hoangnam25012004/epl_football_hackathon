"""Clips made ahead of time (docs/day5-design.md section 9.5): `pundit tts warm` runs the studio
over a demo match and makes every clip a fan could hear (lines read aloud, both recaps, the
greeting) into the durable store, so the judges' first listen costs neither time nor quota.
`pundit recap --audio` uses the same path for one recap.
"""

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from pundit.agents.context import build_match_context
from pundit.agents.contracts import (
    CommentaryItem,
    Profile,
    Recap,
    RecapScope,
    Register,
    RuleId,
    SpeechCue,
    VoiceGender,
)
from pundit.agents.narration import SpeechScript, commentary_script, recap_moment_lines
from pundit.agents.personalize import render_commentary, render_recap
from pundit.agents.spoken import SpokenLexicon
from pundit.domain.log import LogView
from pundit.engine.match import GeneratedMatch
from pundit.orchestration.recap_service import RecapService
from pundit.orchestration.studio_eval import OfflineRun, run_offline
from pundit.orchestration.studio_service import StudioService
from pundit.speech.clips import ClipFile
from pundit.speech.narrator import ClipFailure, Narrator

ATTEMPTS = 10
WAIT_S = 120.0
Maker = Callable[[], SpeechCue | None]


@dataclass
class WarmReport:
    made: int = 0
    failed: int = 0
    notes: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0


async def studio_run(narrator: Narrator, match: GeneratedMatch) -> OfflineRun:
    settings = narrator.settings
    return await run_offline(match, StudioService(settings, offline=True), RecapService(settings))


async def ensure(narrator: Narrator, make: Callable[[], SpeechCue | None]) -> ClipFile | str:
    """Make one clip, waiting at the gate as long as needed (a warm-up has no deadline)."""
    for _ in range(ATTEMPTS):
        cue = make()
        if cue is None:
            return "no_voice"
        if cue.audio is None:
            return cue.note or "no_audio"
        extension = cue.audio.url.rsplit(".", 1)[1]
        found = await narrator.wait(cue.audio.clip_id, extension, WAIT_S)
        if isinstance(found, ClipFile):
            return found
        reason = found.reason if isinstance(found, ClipFailure) else "lost"
        if reason not in ("throttled", "timeout", "unavailable"):
            return reason
        await asyncio.sleep(60 / narrator.settings.rate_per_minute)
    return "gave_up"


async def warm_match(
    narrator: Narrator,
    match: GeneratedMatch,
    *,
    voices: Iterable[VoiceGender],
    modes: Iterable[Register],
    beginner: Iterable[bool] = (False,),
    clock: Callable[[], float] | None = None,
) -> WarmReport:
    loop = asyncio.get_running_loop()
    began = (clock or loop.time)()
    report = WarmReport()

    def count(result: ClipFile | str) -> None:
        if isinstance(result, ClipFile):
            report.made += 1
        else:
            report.failed += 1
            report.notes[result] = report.notes.get(result, 0) + 1

    run = await studio_run(narrator, match)
    context = build_match_context(LogView.from_events(match.meta, match.events[:1]))
    lexicon = SpokenLexicon.from_match(context)
    commentary: dict[str, CommentaryItem] = {o.commentary.id: o.commentary for o in run.outputs}
    match_id = match.meta.match_id

    def greeting(gender: VoiceGender) -> Callable[[], SpeechCue | None]:
        return lambda: narrator.greeting_cue("vi", gender)

    def line(script: SpeechScript, item: CommentaryItem, gender: VoiceGender) -> Maker:
        def make() -> SpeechCue | None:
            return narrator.cue(
                script, lexicon, gender=gender, item_id=item.id, priority=item.excitement,
                match_id=match_id,
            )  # fmt: skip

        return make

    def recap_maker(recap: Recap, mode: Register, gender: VoiceGender) -> Maker:
        recap_view = render_recap(recap, Profile(mode=mode, language="vi"))
        lines = recap_moment_lines(recap, run.moments, commentary, "vi", mode)

        def make() -> SpeechCue | None:
            return narrator.recap_cue(
                recap_view, lines, recap.as_of_seq, lexicon, gender=gender, match_id=match_id
            )

        return make

    genders, registers, beginners = tuple(voices), tuple(modes), tuple(beginner)
    for gender in genders:
        count(await ensure(narrator, greeting(gender)))
    for gender in genders:
        for mode in registers:
            for new in beginners:
                profile = Profile(mode=mode, language="vi", beginner=new, voice=gender)
                seen: set[RuleId] = set()
                for output in run.outputs:
                    view, shown = render_commentary(output.commentary, profile, context, seen=seen)
                    if shown is not None:
                        seen.add(shown)
                    script = commentary_script(view)
                    if script is not None:
                        count(await ensure(narrator, line(script, output.commentary, gender)))
            for scope_recaps in run.recaps.values():
                count(await ensure(narrator, recap_maker(scope_recaps[-1], mode, gender)))
    report.seconds = round((clock or loop.time)() - began, 1)
    return report


async def recap_clip(
    narrator: Narrator,
    match: GeneratedMatch,
    *,
    scope: RecapScope,
    mode: Register,
    gender: VoiceGender,
) -> ClipFile | str:
    """The audio of one recap of a match (the latest revision), made if needed."""
    run = await studio_run(narrator, match)
    recaps = run.recaps.get(scope)
    if not recaps:
        return "no_recap"
    recap = recaps[-1]
    context = build_match_context(LogView.from_events(match.meta, match.events[:1]))
    lexicon = SpokenLexicon.from_match(context)
    commentary = {o.commentary.id: o.commentary for o in run.outputs}
    view = render_recap(recap, Profile(mode=mode, language="vi"))
    lines = recap_moment_lines(recap, run.moments, commentary, "vi", mode)
    return await ensure(
        narrator,
        lambda: narrator.recap_cue(
            view, lines, recap.as_of_seq, lexicon, gender=gender, match_id=match.meta.match_id
        ),
    )
