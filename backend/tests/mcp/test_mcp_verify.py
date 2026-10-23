"""verify_claim through MCP is the claim verifier with the rules (docs/day6-design.md 3.3, 10.2):
the same verdict, numbers and explanation as ClaimService for every eval claim, never a
language model, and nothing published."""

from pathlib import Path

import pytest
from helpers import generated, run, source
from mcp_helpers import DL, static

from pundit.agents.contracts import MAX_CLAIM_CHARS, ClaimRequest, ClaimResult
from pundit.llm.settings import Settings
from pundit.mcp_server import tools
from pundit.mcp_server.errors import ToolInputError
from pundit.orchestration.evaluation import EvalCase, EvalMatch, load_eval, seq_at

EVAL_DIR = Path(__file__).resolve().parents[2] / "evals"
CASES = [
    (eval_file.match, case)
    for path in sorted(EVAL_DIR.glob("*.json"))
    for eval_file in [load_eval(path)]
    for case in eval_file.claims
]
SERVICE = tools.rules_only()


def direct(match: EvalMatch, text: str, at: str | None) -> ClaimResult:
    generated_match = generated(match.scenario, match.seed)
    request = ClaimRequest(
        match_id=generated_match.meta.match_id,
        text=text,
        as_of_seq=seq_at(generated_match.events, at),
    )
    return run(SERVICE.verify(request, source(match.scenario, match.seed), claim_id="test-c0001"))


def test_every_eval_claim_is_checked() -> None:
    assert len(CASES) >= 30


@pytest.mark.parametrize(("match", "case"), CASES, ids=[f"{m.scenario}-{c.id}" for m, c in CASES])
def test_verify_claim_is_the_rule_verifier(match: EvalMatch, case: EvalCase) -> None:
    out = run(
        tools.verify_claim(
            static(),
            SERVICE,
            match_id=f"{match.scenario}-{match.seed}",
            text=case.text,
            as_of=case.at,
        )
    )
    expected = direct(match, case.text, case.at)
    assert (out.verdict, out.reason_code, out.language) == (
        expected.verdict,
        expected.reason_code,
        expected.language,
    )
    assert out.explanation == expected.explanation
    assert [f.display for f in out.headline] == [f.display for f in expected.headline]
    assert [(a.outcome, a.reason_code) for a in out.assertions] == [
        (a.outcome, a.reason_code) for a in expected.assertions
    ]
    assert out.evidence_total == len(expected.evidence_event_ids)
    assert out.evidence_event_ids == list(expected.evidence_event_ids[: tools.MAX_CLAIM_EVIDENCE])
    assert (out.as_of.seq, out.as_of.minute) == (expected.as_of_seq, expected.as_of_display)
    assert (out.analyst, out.synthetic) == ("rules", True)


def test_an_assertion_says_which_words_it_checks() -> None:
    out = run(tools.verify_claim(static(), SERVICE, match_id=DL, text="Đội nhà áp đảo hiệp 2"))
    assert out.verdict == "TRUE"
    assert [f.display for f in out.headline][:3] == ["61%", "15", "2.24"]
    part = out.assertions[0]
    assert (part.kind, part.window, part.outcome) == ("dominance", "H2", "SUPPORTED")
    assert part.said and part.said in out.claim


def test_the_rules_service_never_reaches_a_language_model() -> None:
    risky = Settings(
        use_mock_llm=False,
        llm_provider="ollama",
        mock_llm_faults="analyst:error,explainer:timeout",
        mock_llm_latency_ms=500.0,
    )
    service = tools.rules_only(risky)
    assert service.settings.use_mock_llm is True
    assert service.settings.llm_provider is None
    assert (service.settings.mock_llm_faults, service.settings.mock_llm_latency_ms) == ("", 0.0)
    out = run(tools.verify_claim(static(), service, match_id=DL, text="Harbor dominated H2"))
    again = run(tools.verify_claim(static(), SERVICE, match_id=DL, text="Harbor dominated H2"))
    assert out == again
    result = direct(EvalMatch(scenario="dominant_loser", seed=42), "Harbor dominated H2", None)
    assert result.model == "mock" and result.degraded == ()


@pytest.mark.parametrize("text", ["", "ok", "  a  ", "x" * (MAX_CLAIM_CHARS + 1)])
def test_a_claim_has_three_to_three_hundred_characters(text: str) -> None:
    with pytest.raises(ToolInputError, match="3 to 300 characters"):
        run(tools.verify_claim(static(), SERVICE, match_id=DL, text=text))


def test_a_claim_at_a_minute() -> None:
    out = run(
        tools.verify_claim(
            static(), SERVICE, match_id="comeback_var-7", text="Saltmarsh lead", as_of="HT"
        )
    )
    assert out.as_of.minute == "45+4'"
    assert out.source == "replay"
