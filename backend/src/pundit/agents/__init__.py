"""Agent logic of the claim verifier and moment detector (docs/day2-design.md).

Nothing here imports Microsoft Agent Framework, FastAPI or HTTP clients: agents are plain
functions and classes over typed messages, so they run the same in tests, in the workflow and in
the deterministic fallback path.
"""
