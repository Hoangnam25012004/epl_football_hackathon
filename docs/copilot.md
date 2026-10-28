# GitHub Copilot Agent Mode and Pundit Copilot

> Synthetic data only: fictional teams and players, no real Premier League data.

This page records how GitHub Copilot Agent Mode is used with Pundit Copilot, and only what was
actually done. Most of the project's code was written with Claude Code; the submission says so.

| Part | Status |
|---|---|
| 1. At run time: Copilot Agent Mode reads a match through the Pundit MCP server | Done on 28/10/2026: six questions, all answered from tool calls (see below) |
| 2. During development: work done with Copilot Agent Mode | One audit done on 28/10/2026: synthetic-data compliance (see below) |

## 1. At run time: Copilot as a client of the MCP server

Pundit Copilot serves its deterministic Stats Tools as a read-only MCP server (see
[mcp.md](mcp.md)). Copilot Agent Mode in VS Code connects to it and answers questions about a match
by calling those tools, so every number it quotes comes from the same tools the studio uses.

### Setup

1. The public demo serves MCP at `https://hnam2501-pundit-copilot.hf.space/mcp`; a local app
   (`uv run --project backend pundit serve --ui frontend/out`) serves it at
   `http://127.0.0.1:8000/mcp`.
2. In VS Code, `.vscode/mcp.json`:

   ```json
   {
     "servers": {
       "pundit-space": { "type": "http", "url": "https://hnam2501-pundit-copilot.hf.space/mcp" },
       "pundit-live": { "type": "http", "url": "http://127.0.0.1:8000/mcp" }
     }
   }
   ```

   Over HTTP, Copilot also sees the live sessions of the running app, private ones included when
   it is given their id. Without a running app, the stdio form of [mcp.md](mcp.md) works too.
3. Open Copilot Chat, choose **Agent** mode, start the server, and check that its tools are
   listed: `list_matches`, `list_metrics`, `list_players`, `get_scoreboard`, `get_stat`,
   `compare_stat`, `get_moments`, `get_events`, `verify_claim`.
4. On the landing page, **Start my demo match** gives a private live session; its id
   (`dominant_loser-42~xxxxxxxx`) is in the address bar of the studio.

### The six questions

Run on 28/10/2026 in VS Code 1.143.1, Copilot Chat in Agent mode, model "Auto" (shown in the chat
as `gpt-5.6-luna`), against the public Space. The "tools' answer" column was read from the server
independently of Copilot, with direct MCP calls, before the run. Each row records the tools
Copilot called and whether its answer matches the tools' numbers.

| # | Question to Copilot | Tools Copilot called | Tools' answer | Copilot's answer |
|---|---|---|---|---|
| 1 | "Using the pundit tools, which matches can you analyse?" | `list_matches` | 4 scenarios | Listed the 4 matches with teams and storylines. Matches. |
| 2 | "In dominant_loser-42, who had more possession in the second half? Cite the evidence." | `get_stat` twice (home, away; second half) | Harbor Lions 61 %, Kestrel 39 % | Harbor Lions 61 % (986,496 / 1,629,932 ms), Kestrel 39 %, with event IDs. Matches. |
| 3 | "Fact-check for the commentator: 'Đội nhà áp đảo hiệp 2' in dominant_loser-42." | `verify_claim` | TRUE: possession 61–39 %, shots 15–1, xG 2.24–0.24, momentum +0.91 / −0.91 | TRUE with exactly those numbers and event IDs (66 / 59 / 112 events). Matches. |
| 4 | "Is it true that Kestrel took more shots than Harbor in that match?" | `verify_claim` | FALSE: Kestrel 7, Harbor Lions 21 | FALSE: 7 against 21, with event IDs. Matches. |
| 5 | "What were the key moments of comeback_var-7, and what did VAR decide?" | `get_moments` | Goals on 18', 35' (overturned by VAR on 36'), 52', 68', 81', 90+2'; Ironvale win 3–2 | All eight moments with players, minutes, score and event IDs; the 35' goal ruled out for offside; 51' momentum 0.64. Matches. |
| 6 | "In my live match `dominant_loser-42~7cmlu7ny`, what is the score and the xG right now?" | `get_scoreboard`, `compare_stat` (xg) | Read from the live session at the same time: source `live`, minute 54', 0–1, xG 0.51–0.72 | Third attempt: 1–1 at 72', xG 1.85–0.72, event IDs. The numbers come from the tools (the studio had moved on). See below for the first two attempts. |

### What Copilot got wrong

- **Question 6, attempts 1 and 2.** Asked with a placeholder id (`~xxxxxxxx`) and then with the
  real id, Copilot called only `list_matches`, saw `live: []` and answered that no live session
  existed. That is the tool working as designed: `list_matches` lists public sessions, and a
  private session is reachable only by its id. The session did exist: a direct MCP call returned
  it (`source: live`, 0–1, xG 0.51–0.72 at 54'). Once the prompt told Copilot to call
  `get_scoreboard` and `compare_stat` with the id directly, it answered correctly (attempt 3).
  Lesson for users: give Copilot the id and name the tools; do not rely on the list.
- Nothing else: for questions 1 to 5 every number Copilot quoted was returned by a tool, and none
  was invented. On the long answers (questions 3 and 5) Copilot read the tool's output from the
  temporary file the tool result was saved to, then summarised it.

## 2. During development

Only work actually done with Copilot Agent Mode is listed here, each with what was asked, what
Copilot did and what was changed by hand afterwards.

Only one task was done this way. Everything else in the repository was written with Claude Code,
including the keepalive watchdog (`tools/ops/watchdog.py`), which the plan had set aside for
Copilot and which was not done in time.

### Synthetic-data compliance audit (28/10/2026)

- **Asked** (Agent mode, VS Code 1.143.1, model "Auto"): to search the repository for any real
  Premier League club, player, stadium, sponsor or league name or logo in code, data, docs and the
  frontend, list each hit with file and line, and change nothing.
- **What Copilot did:** ran regex searches over the repository (club, stadium, player and sponsor
  names, logo and crest URLs), read the project's own scanner `tools/security/scan_compliance.py`
  and its blocklist, ran the scanner, listed the frontend assets, and sorted every hit into
  product content or rule text. It changed no file.
- **Result:** no real club, player, stadium, sponsor, logo or logo URL in `backend/src`, `data`,
  `frontend/src`, `frontend/public` or `packages/schemas` (the scanner: 288 files, 0 errors,
  1 warning). The only frontend image is the project's own icon. The league's name appears only in
  disclaimers, the hackathon's description, the scanner's blocklist and the scanner's test
  fixtures, in 19 files, each listed with its lines.
- **What was checked by hand afterwards:** the one warning is the fictional defender "Dominic
  Shaw-Reid", whose surname matches a real player's. The design (day6-design §6) treats a shared
  surname as a warning for a human, not an error, and a hyphenated fictional name is kept, because
  renaming it would change the seeded matches and their golden hashes. The scanner already runs
  in CI on every commit (the `security` job), so Copilot's run repeated a check that was green.
- **Limits:** Copilot found nothing the project's own scanner had not found; the value of the run
  is an independent second pass with a different tool, not a new finding.
