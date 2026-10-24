# Pundit Copilot MCP server

Pundit Copilot exposes its deterministic match statistics and its claim checker as a
**read-only [Model Context Protocol](https://modelcontextprotocol.io) server**. An external AI
client, such as GitHub Copilot Agent Mode, Claude or a broadcaster's own assistant, can ask about a
synthetic match and get **numbers computed by the server, each with the ids of the events it
comes from**. The client's model only phrases the answer. It never computes a number itself.

The agents inside the app use the same catalog. The in-process function tools of the Microsoft
Agent Framework agents and the MCP tools `get_stat` / `compare_stat` share one name, one
description and one argument schema (`pundit.orchestration.tools.catalog()`), and they compute
with the same registry of 33 metrics.

> **Synthetic data.** Every team, player and match is generated. No real league, club or
> player data is used. Every answer carries `"synthetic": true` and a disclaimer.

## Transports

| | stdio | Streamable HTTP |
|---|---|---|
| Start | `pundit mcp` | `pundit serve` (the API), endpoint `/mcp` (and `/mcp/`) |
| Matches | Generated on demand: `scenario-seed`, e.g. `dominant_loser-42` | The API's **live sessions** first (as far as each has been played), then generated matches |
| Use | A developer machine, no server needed, fully deterministic | The deployed demo; reads the match the studio is showing |

`pundit mcp --http --port 8001` serves the HTTP endpoint on its own, with generated matches only.

**Private sessions.** Each viewer of the demo can start a private session of their own, with an id
such as `dominant_loser-42~k3v9x2ma`. `list_matches` never lists them, but every tool reads one
when it is given its id (from the studio's address bar), so an assistant can follow the very match
its user is watching.

The HTTP transport is **stateless with JSON responses**. The tools never send notifications,
so the server keeps no MCP session in memory, and any replica can answer any request. Only `POST`
is served: `GET` and `DELETE` get `405 Allow: POST`, the protocol's answer for "no event stream
here".

## Tools

Every tool is annotated `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`
and `openWorldHint: false`.

| Tool | Arguments | Returns |
|---|---|---|
| `list_matches` | none | Live shared sessions (id, scenario, seed, status, minute, score) and the 4 scenarios with their default seed |
| `list_metrics` | `scope?` (`team`, `match`, `player`) | The 33 metrics: unit, scope, player filter, definition, EN / VI description |
| `get_stat` | `match_id`, `metric`, `team?`, `player?`, `window?`, `as_of?` | One value: display text, unit, numerator / denominator, status, definition, up to 25 evidence event ids and their total |
| `compare_stat` | `match_id`, `metric`, `window?`, `as_of?` | The metric for both teams (home first), `diff`, `ratio` |
| `get_scoreboard` | `match_id`, `as_of?` | Score, period, finished, both teams |
| `get_events` | `match_id`, `ids?` (≤ 50) or `last?` (≤ 50), `types?`, `as_of?` | Events: id, displayed minute, type, team, player name, coordinates, details |
| `get_moments` | `match_id`, `as_of?`, `min_importance?` (1–3), `limit?` (≤ 50) | Goals, cards, VAR decisions, big chances and momentum shifts, with their facts and trigger events |
| `verify_claim` | `match_id`, `text` (3–300 characters, Vietnamese or English), `as_of?` | `TRUE`, `PARTLY_TRUE`, `FALSE` or `INSUFFICIENT_DATA`, each part of the claim with its numbers and events, a template explanation in the claim's language |
| `list_players` | `match_id`, `team?` | Both fictional squads: id, name, short name, team, shirt number |

- **`verify_claim` uses rules only.** It runs the app's claim verifier with the rule analyst and
  the template explainer, and never calls a language model, so the same claim always gets the
  same verdict. It **publishes nothing**: no claim is added to a live session's history and no
  message reaches the studio.
- **Resources:** `pundit://about` (what the server is) and `pundit://metrics` (the metric
  catalog as JSON).
- **Prompts:** `fact_check(claim, match_id)` and `match_briefing(match_id, language)`. Both
  tell the model to quote numbers exactly as the tools display them and to add none of its own.

### Arguments a model can write

- **Teams:** an id (`harbor_lions`), a short name (`HAR`), a name with or without `FC`
  (`Harbor Lions`), `home` / `away`, or `đội nhà` / `đội khách`. Case and accents do not
  matter.
- **Players:** an id (`kestrel_athletic-11`), a full name, an initial and surname (`J. Ellery`),
  or a surname alone. A name shared by two players lists the candidates.
- **Windows:** `{"type": "second_half"}`, `{"type": "last_minutes", "minutes": 15}`,
  `{"type": "minute_range", "start_minute": 15, "end_minute": 30}`, plus `full`, `first_half`,
  `from_minute` and `until_minute`.
- **`as_of`:** a displayed minute (`"63'"`, `"45+2'"`), `"HT"` or `"FT"`; empty means the
  latest event. A live match **never answers about a minute not yet played**:
  `70' has not been played yet: the match is at 52'`.

A wrong argument comes back as a tool error that says how to fix it, for example
`unknown team 'Kestrel FC': this match has harbor_lions (HAR, Harbor Lions FC); kestrel_athletic
(KES, Kestrel Athletic)`. An unexpected failure answers only `Error executing tool <name>`. The
details stay in the server log.

### Consistency

A call pins the match at one event (`as_of.seq`) for its whole duration. The replay of a live
session keeps going, but the answer does not move. Every output says where it comes from
(`"source": "live"` or `"replay"`) and when (`"as_of": {"seq": 2064, "minute": "90+6'"}`).

## Connect a client

**VS Code (GitHub Copilot Agent Mode), HTTP**, with the API running (`pundit serve`). Put this in
`.vscode/mcp.json`:

```json
{
  "servers": {
    "pundit-live": { "type": "http", "url": "http://127.0.0.1:8000/mcp" }
  }
}
```

**stdio**, which needs no running API (use `uv run` from the repository root):

```json
{
  "servers": {
    "pundit": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--project", "backend", "pundit", "mcp"]
    }
  }
}
```

**Claude Code and other clients** that read the generic `mcpServers` format (`.mcp.json`):

```json
{
  "mcpServers": {
    "pundit": { "command": "uv", "args": ["run", "--project", "backend", "pundit", "mcp"] }
  }
}
```

Example questions:

- "Using the pundit tools, which matches can you analyse?"
- "In dominant_loser-42, who had more possession in the second half? Cite the evidence."
- "Fact-check for the commentator: 'Đội nhà áp đảo hiệp 2' in dominant_loser-42."
- "What were the key moments of comeback_var-7, and what did VAR decide?"

## Security and limits

| Concern | Measure |
|---|---|
| Writes | None: every tool reads. No tool starts, stops or changes a match |
| Cost | No tool calls a language model |
| DNS rebinding | Only `MCP_ALLOWED_HOSTS` are served. Another `Host` gets `421`; a page of another `Origin` gets `403`. Clients without an `Origin` header (Copilot, Claude, curl) are not affected |
| Abuse of the public endpoint | A token bucket per client IP (`MCP_RATE_PER_MINUTE`, `verify_claim` costs 3), with at most 1,024 clients tracked. Behind Azure Container Apps, `TRUST_PROXY=true` takes the client from the last `X-Forwarded-For` hop, the one the ingress added |
| Large requests and answers | Request bodies up to 64 KB; a claim up to 300 characters; up to 50 events, 50 moments and 25 evidence ids per value (with the total), so no answer exceeds 64 KB |
| Leaks | Unexpected errors return no message, path or configuration. A call is logged as one line (tool, match, duration, status), never with the words of a claim or the client's IP |
| Authentication | None: a public demo of read-only synthetic data |

If the MCP SDK cannot be imported, the app still starts without `/mcp`, and `/healthz` reports
`"mcp": {"status": "unavailable"}`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MCP_ENABLED` | `true` | `false` removes `/mcp` |
| `MCP_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Host names served on `/mcp`. On Azure, add the app's host name |
| `MCP_RATE_PER_MINUTE` | `60` | Tool calls per minute and client |
| `TRUST_PROXY` | `false` | `true` behind a trusted reverse proxy |

`/healthz` reports the endpoint and its counters:

```json
"mcp": {"enabled": true, "status": "ok", "transport": "streamable-http", "path": "/mcp",
        "calls": 42, "errors": 0, "refused": 3, "rate_limited": 1,
        "by_tool": {"get_stat": 30, "verify_claim": 12}}
```

## Tests

- `backend/tests/mcp/` covers several layers:
  - **Arguments:** every way of writing a team, player, window or minute.
  - **Every tool:** what it answers, how it refuses, and its size limits.
  - **An oracle.** Every `get_stat` / `compare_stat` equals the registry's `compute()` /
    `compare()`, for every metric, team and three windows on three matches, at full time, at
    half-time and at 63'. A property test adds random windows and minutes.
  - **`verify_claim` equals the claim verifier** for every claim of the evaluation set.
  - **The SDK contract:** a golden `tools/list`, annotations, output schemas, errors and
    Vietnamese text.
  - **Live sessions:** pinned views, the future refused, nothing published, 20 calls during a
    fast replay.
  - **The transports:**
    - stdio in a child process, where every stdout line must be JSON-RPC;
    - `/mcp` and `/mcp/`, the UI still at `/`;
    - `421`, `403`, `405`, `413` and `503` outside the app's run;
    - the rate limit, with and without a trusted proxy;
    - `/healthz`;
    - the SDK missing.
- `backend/scripts/mcp_smoke.py` runs stdio and a real HTTP server. A shared session replays
  at 1000x while 20 calls are checked against the registry, followed by the host, origin, method
  and rate-limit checks, and a clean shutdown.
