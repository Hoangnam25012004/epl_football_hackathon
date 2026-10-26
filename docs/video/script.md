# Demo video: script (under 2 minutes)

> Synthetic data only. Draft of 26/10/2026 (no Azure: the public demo is a Hugging Face Space),
> to record on Day 7 on the Space.

Rules for the video (Official Rules): under 2 minutes, public on YouTube or Vimeo, English
subtitles for every Vietnamese part ([subtitles.srt](subtitles.srt)), no copyrighted music, no
third-party logos or brands, the "Synthetic data" label visible. Record at 1920×1080 in the
studio theme; the match is the private demo match "Dominated but lost" (`dominant_loser-42`).
The voice-over is read by Clipchamp's text-to-speech from [voiceover.txt](voiceover.txt), one
line per scene.

| # | Time | On screen | Voice-over (English) |
|---|---|---|---|
| 1 | 0:00–0:08 | Studio, the match playing at 20x; zoom on the scoreboard and the "Synthetic data" label | Live commentary moves too fast to check. Say one wrong number, and it is on air. |
| 2 | 0:08–0:30 | Type "Kestrel took more shots than Harbor": FALSE at once, chip "Rules · 0.1 s". Then "Đội nhà áp đảo hiệp 2": TRUE, 61 % – 39 %, shots 15–1, xG 2.24–0.24; the strip "The model is checking…", then the model's explanation replaces the template, "The model read it as the rules did" | A claim, in English or Vietnamese, gets a verdict from the rules in under a second, with the numbers and the events behind it. Then an open model, running on the same small CPU, refines the explanation. Numbers never come from the model. |
| 3 | 0:30–0:48 | Agent Ops: the claim lane lights node by node with the typed handoffs; the shared state "MatchState @ seq · minute"; the studio lane busy with the match | Fourteen agents on Microsoft Agent Framework share one match state and hand off typed messages. Agent Ops shows every handoff, live. |
| 4 | 0:48–1:05 | Chaos panel: "The model lies". A claim: the Guardrail node turns "blocked", the log says "Guardrail rejected a draft (UNVERIFIED_NUMBER) → the template is used"; the counter "Wrong numbers that reached a screen: 0". Reset | Break the model yourself. Here it lies: valid answers with one number changed. The guardrail blocks every one, and nothing wrong reaches the screen. |
| 5 | 1:05–1:20 | A goal: the talking point appears, approve and pin it; the overlay in OBS takes the lower-third on air | At every key moment the studio gets verified talking points, and an on-air graphic ready for OBS or vMix. |
| 6 | 1:20–1:38 | Fan player: Casual · Tiếng Việt · Harbor · Beginner mode, voice on. The offside line with its explanation card, then the goal line | For fans everywhere, commentary in their own language: here Vietnamese, read aloud, with a beginner mode that explains offside the first time it happens. |
| 7 | 1:38–1:50 | The README's architecture, then GitHub Copilot asking the match through MCP | The same statistics are served over MCP to any AI assistant. The whole stack runs in one CPU container with an open model: no cloud account, the data never leaves the machine. |
| 8 | 1:50–1:55 | Landing page, then the title card | Pundit Copilot: never wrong on air. Synthetic data only. |

Notes:
- Scene 2: record on the Space; if the model pass takes longer than the scene, cut while the
  strip runs and resume when rev.2 lands (say nothing about its time on the voice-over).
- Scene 6 uses the browser's Vietnamese voice (spike S2: Piper missed its speed gate on the dev
  machine). Record in a browser that has a Vietnamese voice (Edge on Windows); keep the
  voice-over low here, so the subtitles carry it.
- Scene 7 shows Copilot only once docs/copilot.md has a real run.
- After recording, retime [subtitles.srt](subtitles.srt) to the cut.
