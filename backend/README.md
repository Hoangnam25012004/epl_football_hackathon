# Pundit Copilot backend (Day 1: data engine and stats)

Synthetic football match engine, ball-state machine, match state validation and stats tools.
All data is synthetic: fictional teams and players, not affiliated with the Premier League.
Design: [../docs/day1-design.md](../docs/day1-design.md).

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

## Quality gates

```powershell
uv run ruff check . ; uv run ruff format --check . ; uv run mypy
uv run pytest                      # everything except the slow sweep (~40 s)
uv run pytest -m slow              # 100 seeds x 4 scenarios
uv run pytest -m "not slow and not perf" --cov=pundit   # coverage run (CI)
```

When the generator changes on purpose, refresh the golden files and review the summary diff:

```powershell
uv run pundit generate --scenario dominant_loser --seed 42 --update-golden
uv run pundit generate --scenario comeback_var --seed 7 --update-golden
```
