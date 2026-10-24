# Pundit Copilot: project pitch

> Synthetic data only: fictional teams, players and matches. Not affiliated with the Premier
> League or any club. Draft of 24/10/2026; the Azure line is confirmed at submission.

## Short (about 60 words)

Pundit Copilot is a multi-agent co-pilot for live football coverage. It fact-checks what
commentators say before it airs, writes verified talking points and lower-thirds, and gives fans
live Vietnamese commentary with a voice, explained for beginners. Every number comes from
deterministic statistics tools and passes a guardrail: in our evaluation, zero invented numbers
and zero wrong verdicts.

## Full (about 200 words)

**The problem.** Live commentary moves too fast to check. A pundit says "they dominated the
second half" and nobody can verify it before it airs; graphics are built by hand; and most fans
follow a match in a language other than English, often new to its rules.

**What we built.** Pundit Copilot turns a live event stream into explainable match
intelligence. In the studio, a claim typed or spoken in Vietnamese or English gets a verdict
with the numbers and the event IDs behind it; every key moment brings verified talking points
and an on-air lower-third. Fans get live commentary in Vietnamese or English, read aloud,
personalised by club, player, register and a beginner mode that explains offside and VAR the
first time they happen. The same statistics are served to AI assistants as a read-only MCP
server.

**How.** Agents built on Microsoft Agent Framework (a claim analyst, an evidence collector, a
judge, an explainer, talking-point, graphics and commentary writers, a narrator) share one
match state and hand off typed messages. Models (Microsoft Foundry) only read and write words:
numbers come from deterministic tools, a guardrail rejects any unsupported sentence, and
templates take over when a model fails. Voices: Azure AI Speech.

**Results.** 100 evaluation claims: 96–97 % accuracy, 0 wrong verdicts, 0 numbers without a
source; 13 of 13 injected model failures handled on time.
