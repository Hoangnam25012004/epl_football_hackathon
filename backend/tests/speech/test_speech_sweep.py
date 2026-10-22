"""Every script a fan can hear passes SpeechGuard (docs/day5-design.md phase V3): all lines read
aloud and both recaps, for every profile, on dominant_loser-42 by default and on the 4 x 3
matrix with `-m slow`. Zero rejections, zero digits left, every number read back."""

from typing import Any

import pytest
from helpers import match_context
from studio_helpers import MATRIX, offline

from pundit.agents.contracts import Profile, RuleId
from pundit.agents.narration import commentary_script, recap_moment_lines
from pundit.agents.personalize import render_commentary, render_recap
from pundit.agents.speech_cue import draft_cue, draft_recap
from pundit.agents.spoken import SpokenLexicon
from pundit.agents.voices import voice_for

PROFILES = (
    {"mode": "casual", "beginner": True, "club": True},
    {"mode": "casual", "beginner": False, "club": False},
    {"mode": "analyst", "beginner": True, "club": False},
    {"mode": "analyst", "beginner": False, "club": True},
)


def sweep(scenario: str, seed: int) -> dict[str, Any]:
    run = offline(scenario, seed)
    context = match_context(scenario, seed)
    lexicon = SpokenLexicon.from_match(context)
    commentary = {o.commentary.id: o.commentary for o in run.outputs}
    cues, rejected, recap_ms = 0, [], []
    for shape in PROFILES:
        team = context.teams[1].id if shape["club"] else None
        profile = Profile(mode=shape["mode"], language="vi", team=team, beginner=shape["beginner"])
        seen: set[RuleId] = set()
        for output in run.outputs:
            view, shown = render_commentary(output.commentary, profile, context, seen=seen)
            if shown is not None:
                seen.add(shown)
            script = commentary_script(view)
            if script is None:
                continue
            draft = draft_cue(script, voice_for("vi", "female"), lexicon)
            cues += 1
            if not draft.report.passed:
                rejected.append((view.id, draft.report.problems))
            assert not any(ch.isdigit() for s in draft.segments for ch in s.text)
        for scope, recaps in run.recaps.items():
            recap = recaps[-1]
            recap_view = render_recap(recap, profile)
            lines = recap_moment_lines(recap, run.moments, commentary, "vi", profile.mode)
            draft = draft_recap(
                recap_view, lines, recap.as_of_seq, voice_for("vi", "male"), lexicon
            )
            cues += 1
            if not draft.report.passed:
                rejected.append((recap.id, draft.report.problems))
            recap_ms.append((scope, profile.mode, draft.estimate_ms))
    return {"cues": cues, "rejected": rejected, "recap_ms": recap_ms}


def check(result: dict[str, Any]) -> None:
    assert result["rejected"] == []
    for scope, mode, ms in result["recap_ms"]:
        assert ms <= 150_000, (scope, mode, ms)  # a recap read aloud stays under 2.5 minutes
        assert ms >= 35_000, (scope, mode, ms)
        if scope == "FULL" and mode == "analyst":
            assert ms >= 55_000  # about one to two minutes (checklist)


def test_every_cue_of_the_golden_match_passes() -> None:
    result = sweep("dominant_loser", 42)
    assert result["cues"] > 100
    check(result)


@pytest.mark.slow
@pytest.mark.parametrize(("scenario", "seed"), MATRIX)
def test_every_cue_of_the_matrix_passes(scenario: str, seed: int) -> None:
    check(sweep(scenario, seed))
