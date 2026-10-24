# GitHub Copilot Agent Mode and Pundit Copilot

> Synthetic data only: fictional teams and players, no real Premier League data.

This page records how GitHub Copilot Agent Mode is used with Pundit Copilot, and only what was
actually done. Most of the project's code was written with Claude Code; the submission says so.

| Part | Status |
|---|---|
| 1. At run time: Copilot Agent Mode reads a match through the Pundit MCP server | Set up; the six runs below are filled in once done |
| 2. During development: work done with Copilot Agent Mode | Nothing yet |

## 1. At run time: Copilot as a client of the MCP server

Pundit Copilot serves its deterministic Stats Tools as a read-only MCP server (see
[mcp.md](mcp.md)). Copilot Agent Mode in VS Code connects to it and answers questions about a match
by calling those tools, so every number it quotes comes from the same tools the studio uses.

### Setup

1. Start the app with the UI: `uv run --project backend pundit serve --ui frontend/out`
   (MCP is on `http://127.0.0.1:8000/mcp`).
2. In VS Code, `.vscode/mcp.json`:

   ```json
   {
     "servers": {
       "pundit-live": { "type": "http", "url": "http://127.0.0.1:8000/mcp" }
     }
   }
   ```

   Over HTTP, Copilot also sees the live sessions of the running app, private ones included when
   it is given their id. Without a running app, the stdio form of [mcp.md](mcp.md) works too.
3. Open Copilot Chat, choose **Agent** mode, and check that the `pundit-live` tools are listed:
   `list_matches`, `list_metrics`, `list_players`, `get_scoreboard`, `get_stat`,
   `compare_stat`, `get_moments`, `get_events`, `verify_claim`.
4. On the landing page, **Start my demo match** gives a private live session; its id
   (`dominant_loser-42~xxxxxxxx`) is in the address bar of the studio.

### The six questions

Each run records the tools Copilot called and whether its answer matches the tools' numbers.
A run where Copilot did not call a tool, or quoted a number no tool returned, is written down as
such.

| # | Question to Copilot | Tools expected | The tools' answer | Copilot's run |
|---|---|---|---|---|
| 1 | "Using the pundit tools, which matches can you analyse?" | `list_matches` | 4 scenarios and the running sessions | not run yet |
| 2 | "In dominant_loser-42, who had more possession in the second half? Cite the evidence." | `compare_stat` (possession, second half) | Harbor Lions 61 % to 39 %, with event IDs | not run yet |
| 3 | "Fact-check for the commentator: 'Đội nhà áp đảo hiệp 2' in dominant_loser-42." | `verify_claim` | TRUE: possession 61 %, shots 15 to 1, xG 2.24 to 0.24 | not run yet |
| 4 | "Is it true that Kestrel took more shots than Harbor in that match?" | `verify_claim` or `compare_stat` | FALSE: Kestrel 7 shots, Harbor Lions 21 | not run yet |
| 5 | "What were the key moments of comeback_var-7, and what did VAR decide?" | `get_moments`, `get_events` | A Saltmarsh goal ruled out by VAR in the first half; Ironvale came back from 0–2 to win 3–2 | not run yet |
| 6 | "In my live match `<session id>`, what is the score and the xG right now?" | `get_scoreboard`, `compare_stat` (xg) | What the studio shows at that minute | not run yet |

### What Copilot got wrong

Nothing recorded yet.

## 2. During development

Only work actually done with Copilot Agent Mode is listed here, each with what was asked, what
Copilot did and what was changed by hand afterwards.

Nothing recorded yet.
