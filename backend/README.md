# Pundit Copilot backend

Day 1: synthetic football match engine, ball-state machine, match state validation and stats
tools. Day 2: claim verifier (Microsoft Agent Framework workflow), Guardrail, moment detector,
REST + WebSocket API.
All data is synthetic: fictional teams and players, not affiliated with the Premier League.
Design: [../docs/day1-design.md](../docs/day1-design.md), [../docs/day2-design.md](../docs/day2-design.md).

## Setup

```powershell
python -m pip install --user uv     # once
cd backend
uv sync                              # Python 3.12 + dependencies from uv.lock
```

## Commands

```powershell
uv run pundit scenarios                                              # scenarios and metrics
uv run pundit generate --scenario dominant_loser --seed 42           # -> ../data/matches/
uv run pundit replay --scenario dominant_loser --seed 42 --speed 20  # live replay, status every 5'
uv run pundit replay --scenario comeback_var --seed 7 --compact --from-minute 30
uv run pundit stats --scenario dominant_loser --seed 42 --window H2  # FULL, H1, H2, 60-75, 75-
uv run pundit validate --file ../data/matches/dominant_loser-42.events.jsonl
uv run pundit schema                                                 # -> ../packages/schemas/
```

Day 2: verify a claim (Vietnamese or English), list moments, run the evaluation, serve the API:

```powershell
uv run pundit claim "Đội nhà áp đảo hiệp 2" --scenario dominant_loser --seed 42 --trace
uv run pundit claim "HAR áp đảo hiệp 2" --scenario dominant_loser --seed 42 --at "30'"   # future window
uv run pundit claim "Harbor had twice as many shots as Kestrel in the second half" --scenario dominant_loser --seed 42 --json
uv run pundit moments --scenario comeback_var --seed 7 --min-importance 2
uv run pundit eval                         # 20 labelled + 10 held-out claims, mock LLM
uv run pundit serve --port 8000            # http://127.0.0.1:8000/docs, ws://127.0.0.1:8000/ws/match/{id}
```

Guardrail demo (the explainer invents a number twice; the trace shows `rejected`, then the template):

```powershell
$env:MOCK_LLM_FAULTS = "explainer:hallucinate:2"; uv run pundit claim "Đội nhà áp đảo hiệp 2" --scenario dominant_loser --seed 42 --trace; Remove-Item Env:MOCK_LLM_FAULTS
```

## Configuration

The mock LLM is the default: no network, no secret. Settings come from environment variables and
an optional `backend/.env` (never committed; template in [.env.example](.env.example)).
Environment variables win over the file. A real LLM is one switch away.

Local model with [Ollama](https://ollama.com) (no account, no key; GitHub Models was retired on
2026-07-30):

```powershell
winget install Ollama.Ollama ; ollama pull qwen2.5:3b     # once, about 1.9 GB
# backend/.env: USE_MOCK_LLM=false and LLM_PROVIDER=ollama (model and URL have defaults)
uv run pundit claim "Đội nhà áp đảo hiệp 2" --scenario dominant_loser --seed 42 --trace
uv run pundit eval --provider ollama      # 30 claims, 20-40 min on a laptop CPU
uv run pytest -m live                     # LIVE_EVAL=1 also runs the 30-claim evaluation
```

On a CPU a claim takes 20-90 s (the first call loads the model), so `ollama` gets longer timeouts
(`LLM_TIMEOUT_S=90`, `CLAIM_BUDGET_S=200`). Set `USE_MOCK_LLM=true` again for fast runs. Any other
OpenAI-compatible server works with `LLM_PROVIDER=openai`, `LLM_BASE_URL`, `LLM_MODEL` and
`LLM_API_KEY`.

Microsoft Foundry: set `LLM_PROVIDER=foundry`, `FOUNDRY_ENDPOINT`, `FOUNDRY_MODEL_DEPLOYMENT`, and
sign in with `az login` (Entra ID, needs `uv add azure-identity`) or, for development only,
`FOUNDRY_API_KEY`.

## Quality gates

```powershell
uv run ruff check . ; uv run ruff format --check . ; uv run mypy
uv run pytest                      # everything except the slow sweep and live LLM tests (~2 min)
uv run pytest -m slow              # 100 seeds x 4 scenarios
uv run pytest -m perf              # time budgets (also run without coverage in CI)
uv run pytest -m "not slow and not perf and not live" --cov=pundit   # coverage run (CI)
uv run pytest -m live              # real LLM, opt-in: provider from backend/.env, else local Ollama
```

When the generator changes on purpose, refresh the golden files and review the summary diff:

```powershell
uv run pundit generate --scenario dominant_loser --seed 42 --update-golden
uv run pundit generate --scenario comeback_var --seed 7 --update-golden
```
