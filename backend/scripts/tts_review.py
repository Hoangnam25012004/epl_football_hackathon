"""Listening review of the Vietnamese voice (docs/day5-design.md section 13, V10).

Writes <out>/index.html with the clips beside it: every line the voice reads in one match
(casual beginner and analyst), both recaps, the greeting, the roll call of every player and the
nine presets on one sentence. Each row has the written text, the spoken script, the SSML, the
clip of the configured provider (the mock's tones without Azure, Azure's MP3 with
TTS_PROVIDER=azure and its settings) and a button that reads the script with the browser's
voice (Edge has HoaiMy and NamMinh online). Every mistake heard becomes a rule or an alias, with
its test.

    cd backend
    uv run python scripts/tts_review.py --scenario dominant_loser --seed 42 [--out review]
"""

import argparse
import asyncio
import html
import io
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pundit.agents.context import build_match_context
from pundit.agents.contracts import Profile, RecapView, Register, RuleId, SpeechCue, VoiceGender
from pundit.agents.narration import (
    MomentLine,
    ScriptPart,
    SpeechScript,
    commentary_script,
    greeting_script,
    recap_moment_lines,
)
from pundit.agents.personalize import render_commentary, render_recap
from pundit.agents.prosody import PRESETS
from pundit.agents.speech_cue import CueDraft, draft_cue, draft_recap
from pundit.agents.spoken import EMPTY_LEXICON, SpokenLexicon, normalize
from pundit.agents.voices import voice_for
from pundit.domain.log import LogView
from pundit.engine.generator import generate
from pundit.llm.settings import Settings
from pundit.speech.clips import ClipFile
from pundit.speech.narrator import Narrator, build_narrator
from pundit.speech.warm import Maker, ensure, studio_run

PRESET_SENTENCE = "Phút 27, M. Ribas ghi bàn, Harbor Lions FC 0-1 Kestrel Athletic."


@dataclass
class Row:
    section: str
    label: str
    written: str
    draft: CueDraft | None
    clip: str | None = None  # file name in <out>/clips
    note: str | None = None


async def clip_of(
    narrator: Narrator | None, out: Path, make: Maker
) -> tuple[str | None, str | None]:
    """Make the clip with the configured provider and copy it next to the page."""
    if narrator is None:
        return None, "no server voice (TTS_PROVIDER=off or browser)"
    found = await ensure(narrator, make)
    if not isinstance(found, ClipFile):
        return None, str(found)
    target = out / "clips" / found.path.name
    shutil.copyfile(found.path, target)
    return target.name, None


def page(rows: list[Row], title: str, provider: str) -> str:
    def cell(row: Row) -> str:
        if row.draft is None:
            return "<td></td><td></td>"
        spoken = " ‖ ".join(html.escape(s.text) for s in row.draft.segments)
        problems = ", ".join(p.code for p in row.draft.report.problems)
        segments = json.dumps([s.model_dump(mode="json") for s in row.draft.segments])
        audio = (
            f'<audio controls preload="none" src="clips/{html.escape(row.clip)}"></audio>'
            if row.clip
            else f'<span class="muted">{html.escape(row.note or "")}</span>'
        )
        return (
            f"<td><div>{spoken}</div><div class='muted'>{row.draft.preset} · "
            f"{row.draft.estimate_ms / 1000:.1f} s{' · GUARD: ' + problems if problems else ''}"
            f"</div><details><summary>SSML</summary><pre>{html.escape(row.draft.ssml)}</pre>"
            f"</details></td><td>{audio}<button data-segments='{html.escape(segments)}'>"
            "▶ trình duyệt</button></td>"
        )

    body = []
    section = None
    for row in rows:
        if row.section != section:
            section = row.section
            body.append(f"<tr><th colspan='4'>{html.escape(section)}</th></tr>")
        body.append(
            f"<tr><td class='muted'>{html.escape(row.label)}</td>"
            f"<td>{html.escape(row.written)}</td>{cell(row)}</tr>"
        )
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Voice review</title>
<style>
body {{ font: 14px system-ui, sans-serif; margin: 16px; color: #111; background: #fff; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ border-top: 1px solid #ddd; padding: 6px; vertical-align: top; text-align: left; }}
th {{ background: #f3f3f3; }} .muted {{ color: #555; font-size: 12px; }}
pre {{ white-space: pre-wrap; font-size: 11px; }}
.badge {{ background: #ffe08a; padding: 2px 6px; }}
</style></head><body>
<h1>{html.escape(title)} <span class="badge">Synthetic data</span></h1>
<p class="muted">Provider: {html.escape(provider)} · the browser button uses HoaiMy / NamMinh
when this browser has them. Note every mistake: wrong number, name, stress or speed.</p>
<table><tr><th>#</th><th>Written</th><th>Spoken (segments) · preset · SSML</th>
<th>Listen</th></tr>{"".join(body)}</table>
<script>
function voice(gender) {{
  const all = speechSynthesis.getVoices().filter((v) => v.lang.toLowerCase().startsWith("vi"));
  const name = gender === "male" ? "NamMinh" : "HoaiMy";
  return all.find((v) => v.name.includes(name)) || all[0] || null;
}}
document.addEventListener("click", (event) => {{
  const button = event.target.closest("button[data-segments]");
  if (!button) return;
  speechSynthesis.cancel();
  const chosen = voice(document.body.dataset.gender || "female");
  if (!chosen) {{ alert("This browser has no Vietnamese voice"); return; }}
  for (const segment of JSON.parse(button.dataset.segments)) {{
    const u = new SpeechSynthesisUtterance(segment.text);
    Object.assign(u, {{ voice: chosen, lang: "vi-VN", rate: segment.rate, pitch: segment.pitch,
                       volume: segment.volume }});
    speechSynthesis.speak(u);
  }}
}});
</script></body></html>
"""


async def main(scenario: str, seed: int, out: Path, gender: VoiceGender) -> int:
    (out / "clips").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as cache:
        settings = Settings.from_env().model_copy(
            update={"audio_cache_dir": cache, "audio_store": "none"}
        )
        narrator = build_narrator(settings) if settings.tts_provider in ("mock", "azure") else None
        match = generate(scenario, seed)
        run = await studio_run(narrator, match) if narrator else None
        if run is None:
            from pundit.orchestration.recap_service import RecapService
            from pundit.orchestration.studio_eval import run_offline
            from pundit.orchestration.studio_service import StudioService

            run = await run_offline(
                match, StudioService(settings, offline=True), RecapService(settings)
            )
        context = build_match_context(LogView.from_events(match.meta, match.events[:1]))
        lexicon = SpokenLexicon.from_match(context)
        voice = voice_for("vi", gender)
        rows: list[Row] = []
        commentary = {o.commentary.id: o.commentary for o in run.outputs}

        async def add(section: str, label: str, written: str, draft: CueDraft, make: Maker) -> None:
            clip, note = await clip_of(narrator, out, make)
            rows.append(Row(section, label, written, draft, clip, note))

        greeting = greeting_script("vi")
        await add(
            "Lời chào",
            "greeting",
            greeting.parts[0].text,
            draft_cue(greeting, voice, EMPTY_LEXICON),
            lambda: narrator.greeting_cue("vi", gender) if narrator else None,
        )
        registers: tuple[tuple[Register, bool], ...] = (("casual", True), ("analyst", False))
        for mode, beginner in registers:
            profile = Profile(mode=mode, language="vi", beginner=beginner, voice=gender)
            seen: set[RuleId] = set()
            for output in run.outputs:
                view, shown = render_commentary(output.commentary, profile, context, seen=seen)
                if shown is not None:
                    seen.add(shown)
                script = commentary_script(view)
                if script is None:
                    continue
                item = output.commentary

                def make(
                    s: SpeechScript = script, i: str = item.id, p: int = item.excitement
                ) -> SpeechCue | None:
                    return (
                        narrator.cue(s, lexicon, gender=gender, item_id=i, priority=p)
                        if narrator
                        else None
                    )

                written = " / ".join(part.text for part in script.parts)
                await add(
                    f"Bình luận · {mode}{' · người mới xem' if beginner else ''}",
                    f"{view.display_minute} {view.kind}",
                    written,
                    draft_cue(script, voice, lexicon),
                    make,
                )
            for scope, recaps in run.recaps.items():
                recap = recaps[-1]
                recap_view = render_recap(recap, Profile(mode=mode, language="vi"))
                lines = recap_moment_lines(recap, run.moments, commentary, "vi", mode)
                draft = draft_recap(recap_view, lines, recap.as_of_seq, voice, lexicon)

                def make_recap(
                    v: RecapView = recap_view,
                    ls: tuple[MomentLine, ...] = tuple(lines),
                    a: int = recap.as_of_seq,
                ) -> SpeechCue | None:
                    if narrator is None:
                        return None
                    return narrator.recap_cue(v, ls, a, lexicon, gender=gender)

                await add(
                    f"Tóm tắt · {mode}",
                    str(scope),
                    " ".join(recap_view.paragraphs),
                    draft,
                    make_recap,
                )
        for player in context.players:
            spoken = normalize(player.name, "vi", lexicon)
            rows.append(
                Row(
                    "Điểm danh cầu thủ",
                    f"{player.team} #{player.shirt}",
                    f"{player.name} → {spoken.text}",
                    None,
                )
            )
        for team in context.teams:
            spoken = normalize(team.name, "vi", lexicon)
            rows.append(Row("Điểm danh đội", team.short_name, f"{team.name} → {spoken.text}", None))
        for preset in PRESETS:
            script = SpeechScript(
                kind="commentary",
                language="vi",
                parts=(ScriptPart(source="commentary", text=PRESET_SENTENCE, preset=preset),),
            )
            draft = draft_cue(script, voice, lexicon)

            def make_preset(s: SpeechScript = script, name: str = preset) -> SpeechCue | None:
                if narrator is None:
                    return None
                return narrator.cue(s, lexicon, gender=gender, item_id=f"preset-{name}", priority=1)

            await add("9 preset, cùng một câu", preset, PRESET_SENTENCE, draft, make_preset)
        if narrator is not None:
            await narrator.aclose()
    provider = settings.tts_provider if narrator else f"{settings.tts_provider} (browser only)"
    title = f"Voice review · {match.meta.match_id} · {gender}"
    (out / "index.html").write_text(page(rows, title, provider), encoding="utf-8")
    problems = [r for r in rows if r.draft is not None and r.draft.report.problems]
    print(
        f"wrote {out / 'index.html'}: {len(rows)} rows, {sum(1 for r in rows if r.clip)} clips, "
        f"{len(problems)} refused by SpeechGuard"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", default="dominant_loser")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="review")
    parser.add_argument("--voice", choices=("female", "male"), default="female")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.scenario, args.seed, Path(args.out), args.voice)))
