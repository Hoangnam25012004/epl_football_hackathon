# Pundit Copilot: project pitch

> Synthetic data only: fictional teams, players and matches. Not affiliated with the Premier
> League or any club. Draft of 26/10/2026. It says only what runs: no Azure subscription was
> available, so the public demo is one container on a free Hugging Face Space.

## Short (about 60 words)

Pundit Copilot is a multi-agent co-pilot for live football coverage. It fact-checks what
commentators say before it airs, writes verified talking points and lower-thirds, and gives fans
live Vietnamese commentary, read aloud and explained for beginners. Every number comes from
deterministic statistics tools and passes a guardrail: in our evaluation, zero invented numbers
and zero wrong verdicts.

## Full (about 200 words)

**The problem.** Live commentary moves too fast to check. A pundit says "they dominated the
second half" and nobody can verify it before it airs; graphics are built by hand; and most fans
follow a match in a language other than English, often new to its rules.

**What we built.** Pundit Copilot turns a live event stream into explainable match
intelligence. In the studio, a claim typed or spoken in Vietnamese or English gets a verdict
from the rules in under a second, with the numbers and the event IDs behind it; an open model
then refines the explanation. Every key moment brings verified talking points and an on-air
lower-third. Fans get live commentary in Vietnamese or English, personalised by club, player,
register and a beginner mode that explains offside and VAR the first time they happen. The same
statistics are served to AI assistants as a read-only MCP server.

**How.** Agents on Microsoft Agent Framework share one match state and hand off typed messages;
the Agent Ops page shows them at work, and a judge can break the model of their own match to
watch the system recover. Models only read and write words: numbers come from tools, a
guardrail rejects any unsupported sentence, templates take over when a model fails. The whole
stack runs in one CPU container with an open model, so a broadcaster could run it on premises.

**Results.** 100 evaluation claims: 96–97 % accuracy, 0 wrong verdicts, 0 numbers without a
source; every injected model failure handled on time; a model told to lie had every wrong
number blocked.
