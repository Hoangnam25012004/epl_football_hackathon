"""LLM ports and adapters (docs/day2-design.md section 8): mock -> GitHub Models -> Foundry.

The agents depend only on the ports. `mock.py` is the default (no network, no secret);
`maf_backend.py` is the only module that talks to a real model, through Microsoft Agent
Framework's OpenAI-compatible chat client.
"""
