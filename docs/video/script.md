# Demo video: script (under 2 minutes)

> Synthetic data only. Draft of 24/10/2026, to record on Day 7 on the deployed app.

Rules for the video (Official Rules): under 2 minutes, public on YouTube or Vimeo, English
subtitles for every Vietnamese part ([subtitles.srt](subtitles.srt)), no copyrighted music, no
third-party logos or brands, the "Synthetic data" label visible. Record at 1920×1080 in the
studio theme; the match is the private demo match "Dominated but lost" (`dominant_loser-42`).

| # | Time | On screen | Voice-over (English) |
|---|---|---|---|
| 1 | 0:00–0:10 | Studio, the match playing at 20x; zoom on the scoreboard and the "Synthetic data" label | Live commentary moves too fast to check. Say one wrong number, and it is on air. Pundit Copilot checks it first. |
| 2 | 0:10–0:35 | Type "Kestrel took more shots than Harbor": FALSE, 7 to 21. Then the sample "Đội nhà áp đảo hiệp 2": TRUE, 61 % – 39 %, shots 15–1, xG 2.24–0.24. Open Why · Evidence (the shots on the pitch map) · Agent Trace (the Guardrail span) | A claim, in English or Vietnamese, becomes a verdict, with the numbers and the events behind them. Every number comes from deterministic stats tools, and a guardrail rejects any sentence it cannot back up. |
| 3 | 0:35–0:55 | A goal arrives: the talking point appears, approve and pin it; the overlay in OBS takes the lower-third on air, then clears | At every key moment the studio gets verified talking points, and an on-air graphic ready for OBS or vMix. |
| 4 | 0:55–1:15 | The compare page: Analyst (EN) and Casual (VI) side by side on the same moments | The same moment, for two audiences: dense numbers for the analyst, plain words for the casual fan. |
| 5 | 1:15–1:35 | Fan player: Casual · Tiếng Việt · Harbor · Beginner mode, voice on. The offside line with its explanation card, then the goal "VÀOOO!" read faster and higher | For fans everywhere, commentary in their own language. Here, Vietnamese, read aloud, with a beginner mode that explains offside the first time it happens. |
| 6 | 1:35–1:50 | The architecture diagram of the README, the Azure resource group, Copilot asking the match through MCP | Under the hood, agents on Microsoft Agent Framework share one match state and hand off typed messages; models run on Microsoft Foundry, voices on Azure AI Speech, all on Azure Container Apps. Thirteen injected failures, no crash; zero invented numbers. |
| 7 | 1:50–1:55 | Landing page, then the title card | Pundit Copilot: never wrong on air. Synthetic data only. |

Notes:
- Scene 6 says only what is true on the day of recording: change the Azure line if a service
  is not live yet.
- Keep the voice-over under the Vietnamese voice low in scene 5, so the subtitles carry it.
- After recording, retime [subtitles.srt](subtitles.srt) to the cut.
