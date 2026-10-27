# Submission form: Pundit Copilot

> Text to copy into the Innovation Studio form. Synthetic data only: fictional teams, players and
> matches; not affiliated with the Premier League or any club. Fields marked **(fill in)** need
> a link that exists only once the video is uploaded.

## Project name

Pundit Copilot

## Tagline

An AI co-pilot for live football coverage that never airs an unchecked number.

## Short description (about 60 words)

Pundit Copilot is a multi-agent co-pilot for live football coverage. It fact-checks what
commentators say before it airs, writes verified talking points and lower-thirds, and gives fans
live Vietnamese commentary, read aloud and explained for beginners. Every number comes from
deterministic statistics tools and passes a guardrail: in our evaluation, zero invented numbers
and zero wrong verdicts.

## Pitch: what it does, which Microsoft technologies, which problem

**Problem.** Live commentary moves too fast to check. A pundit says "they dominated the second
half" and nobody can verify it before it airs; graphics are built by hand; and most fans follow a
match in a language other than English, often new to its rules.

**What it does.** A synthetic match engine streams realistic events. In the studio, a claim
typed or spoken in Vietnamese or English gets a verdict from the rules in under a second, with
the numbers and event IDs behind it; an open model then refines the explanation. Every key moment
brings verified talking points and an on-air lower-third (an OBS / vMix overlay). Fans get live
commentary in Vietnamese or English, read aloud, personalised by register (analyst or casual),
club, player, one metric, and a beginner mode that explains offside and VAR the first time they
happen. The same statistics are served to AI assistants through a read-only MCP server.

**Microsoft technologies.** Microsoft Agent Framework runs the claim and studio workflows
(typed messages, shared match state, fan-out / fan-in, recovery). Microsoft Foundry Local
(phi-4-mini) was used to evaluate the claim checker through the same OpenAI-compatible client as
Microsoft Foundry. GitHub Actions and GHCR test, scan, build and deploy the public demo, and a
GitHub Actions watchdog keeps it alive. MCP serves the statistics. Microsoft Foundry (cloud),
Azure AI Speech and Azure Container Apps are implemented and tested against mocks, with Bicep /
azd infrastructure, but **not deployed**: no Azure subscription was available.

**Results.** 100 evaluation claims: 96–97 % accuracy, 0 wrong verdicts, 0 numbers without a
source; 13 injected model failures all handled on time; a model told to lie had every wrong
number blocked before publication.

## Links

| Field | Value |
|---|---|
| Live demo (no login) | https://hnam2501-pundit-copilot.hf.space |
| Source code | https://github.com/Hoangnam25012004/epl_football_hackathon |
| Demo video (< 2 min, public) | **(fill in: the YouTube link)** |

## Categories

Grand Prize · **Best Multi-Agent System**

## Testing instructions

No account or login is needed.

1. Open https://hnam2501-pundit-copilot.hf.space and press **Start my demo match** (a private
   match, replayed at 20x; nobody else sees your changes).
2. Studio: type **Đội nhà áp đảo hiệp 2** ("the home side dominated the second half") and send.
   The rules' verdict arrives in under a second with its numbers and event IDs; the model's
   explanation follows (it can take up to a minute on the free CPU). Open **Why · Evidence ·
   Agent Trace** on the answer.
3. **Agent Ops** (link in the studio header): watch the agents run and hand off. Under **Chaos**,
   choose **The model lies**, send a claim again: the guardrail blocks the wrong numbers and the
   counter of published wrong numbers stays at 0. **Reset** when done.
4. **Fan**: choose Vietnamese, beginner mode, and press **Turn the voice on**. The commentary is
   read by the browser's voice: Edge on a desktop has natural Vietnamese voices; a phone may have
   one system voice or none, in which case the text still runs.
5. Optional, for AI assistants: the MCP server is at
   `https://hnam2501-pundit-copilot.hf.space/mcp` (Streamable HTTP, read-only, 9 tools).

If the page says the Space is starting, wait one or two minutes and reload: a watchdog restarts
it automatically. The demo stays up until 10 November 2026.

## Notes for the judges

- Synthetic data only: every team, player, referee and match is generated; a scanner checks the
  product for real names on every CI run, and every screen is labelled "Synthetic data".
- Built solo. Most of the code was written with Claude Code (Anthropic); this is stated in the
  README.
- Everything the README and this form claim was run; what was built but not deployed is marked
  "Azure-ready, not deployed".
