"""Prompt contracts (design sections 4.4 and 6.2): small enough for a local CPU model and stable
across the claims of a match, so the provider can reuse its prompt cache."""

from helpers import claim_context, match_context

from pundit.llm.prompts import ANALYST_PROMPT_VERSION, analyst_messages, catalog_text

# ~3k tokens at most: Ollama's default context is 4096 tokens and the answer needs room.
MAX_ANALYST_PROMPT_CHARS = 9_000


def test_analyst_prompt_is_compact() -> None:
    system, user = analyst_messages(claim_context("Đội nhà áp đảo hiệp 2"))
    assert len(system) + len(user) < MAX_ANALYST_PROMPT_CHARS
    assert ANALYST_PROMPT_VERSION == "analyst-v2"


def test_the_system_part_only_depends_on_the_match() -> None:
    first = analyst_messages(claim_context("Đội nhà áp đảo hiệp 2"))
    second = analyst_messages(claim_context("Kestrel won despite lower xG", at="70'"))
    assert first[0] == second[0]  # same prefix: cached by the provider
    assert "Kestrel won despite lower xG" in second[1] and "70'" in second[1]


def test_catalog_lists_every_player_and_team_metric() -> None:
    match = match_context()
    text = catalog_text(match)
    assert all(p.id in text for p in match.players)
    assert all(m.name in text for m in match.metrics if m.scope != "match")
    assert "ball_in_play_time" not in text  # a match-level metric is never a claim subject
    assert "ppda | ratio | team" in text and "LOWER = stronger" in text


def test_feedback_is_appended_to_the_user_part() -> None:
    _, user = analyst_messages(claim_context("Đội nhà áp đảo hiệp 2"), ["assertion 1: bad metric"])
    assert user.endswith("Your previous answer was rejected: assertion 1: bad metric")
