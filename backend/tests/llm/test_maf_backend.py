"""The real-LLM adapter, fully offline (design sections 8.3 and 13.7).

An `httpx.MockTransport` plays the OpenAI-compatible endpoint, so the requests MAF builds
(structured output, token, URL) and every error mapping are tested without network or secret.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from helpers import claim_context, run, source

from pundit.agents.contracts import ClaimRequest
from pundit.llm.maf_backend import (
    LlmClaimAnalyst,
    LlmExplainer,
    MafChatBackend,
    build_backend,
    foundry_base_url,
)
from pundit.llm.ports import ContentFiltered, LlmError, LlmOutputError
from pundit.llm.settings import ConfigError, Settings
from pundit.orchestration.service import ClaimService

TOKEN = "sk-offline-test-key-123"
BASE_URL = "https://llm.example.test/v1"
COMPATIBLE = Settings.from_env(
    {
        "USE_MOCK_LLM": "false",
        "LLM_PROVIDER": "openai",
        "LLM_BASE_URL": BASE_URL,
        "LLM_MODEL": "test-model-mini",
        "LLM_API_KEY": TOKEN,
    }
)
W_H2 = {"type": "second_half", "minutes": None, "start_minute": None, "end_minute": None}

DOMINANCE_SPEC = {
    "language": "vi",
    "assertions": [
        {
            "kind": "dominance",
            "subject": {"kind": "team", "id": "harbor_lions"},
            "other": None,
            "metric": None,
            "op": None,
            "value": None,
            "ratio": None,
            "window": W_H2,
            "negated": False,
            "approx": False,
            "text_span": "Đội nhà áp đảo hiệp 2",
        }
    ],
    "unsupported_reason": None,
    "note": "home side",
}


def completion(content: str, finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "openai/gpt-4.1-mini",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class FakeEndpoint:
    """Answers chat completions from a queue; records every request."""

    def __init__(
        self, *answers: httpx.Response | Callable[[dict[str, Any]], httpx.Response]
    ) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if callable(answer):
            return answer(json.loads(request.content))
        return answer

    def body(self, index: int = 0) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(self.requests[index].content)
        return loaded

    def backend(self, settings: Settings = COMPATIBLE) -> MafChatBackend:
        return build_backend(
            settings, http_client=httpx.AsyncClient(transport=httpx.MockTransport(self))
        )


def ok(content: dict[str, Any] | str) -> httpx.Response:
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return httpx.Response(200, json=completion(text))


def test_analyst_request_and_answer() -> None:
    endpoint = FakeEndpoint(ok(DOMINANCE_SPEC))
    analyst = LlmClaimAnalyst(endpoint.backend())
    spec = run(analyst.analyze(claim_context("Đội nhà áp đảo hiệp 2")))
    assert [(a.kind, a.subject.team, a.window.label()) for a in spec.assertions] == [
        ("dominance", "harbor_lions", "H2")
    ]
    request = endpoint.requests[0]
    assert str(request.url) == f"{BASE_URL}/chat/completions"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    body = endpoint.body()
    assert body["model"] == "test-model-mini" and body["temperature"] == 0
    schema = body["response_format"]
    assert schema["type"] == "json_schema" and schema["json_schema"]["strict"] is True
    assert schema["json_schema"]["name"] == "LlmClaimSpec"
    roles = [m["role"] for m in body["messages"]]
    assert roles[0] == "system" and roles[-1] == "user"
    assert "Đội nhà áp đảo hiệp 2" in body["messages"][-1]["content"]


def test_feedback_reaches_the_model() -> None:
    endpoint = FakeEndpoint(ok(DOMINANCE_SPEC))
    analyst = LlmClaimAnalyst(endpoint.backend())
    run(analyst.analyze(claim_context("Đội nhà áp đảo hiệp 2"), ["assertion 1: unknown metric"]))
    assert "unknown metric" in endpoint.body()["messages"][-1]["content"]


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (ok("not json at all"), LlmOutputError),
        (ok({"language": "vi"}), LlmOutputError),
        (
            ok(
                {
                    **DOMINANCE_SPEC,
                    "assertions": [
                        {
                            **DOMINANCE_SPEC["assertions"][0],
                            "subject": {"kind": "team", "id": "ironvale_united"},
                        }
                    ],
                }
            ),  # type: ignore[dict-item]
            LlmOutputError,
        ),
        (
            httpx.Response(
                400,
                json={
                    "error": {
                        "code": "content_filter",
                        "message": "filtered by the content management policy",
                    }
                },
            ),
            ContentFiltered,
        ),
        (
            httpx.Response(200, json=completion("{}", finish_reason="content_filter")),
            ContentFiltered,
        ),
        (httpx.Response(500, json={"error": {"message": "boom"}}), LlmError),
        (httpx.Response(401, json={"error": {"message": "bad token"}}), LlmError),
    ],
)
def test_errors_are_mapped(answer: httpx.Response, error: type[Exception]) -> None:
    analyst = LlmClaimAnalyst(FakeEndpoint(answer).backend())
    with pytest.raises(error):
        run(analyst.analyze(claim_context("Đội nhà áp đảo hiệp 2")))


def test_unknown_team_errors_are_listed_for_the_retry() -> None:
    bad = {
        **DOMINANCE_SPEC,
        "assertions": [
            {**DOMINANCE_SPEC["assertions"][0], "subject": {"kind": "team", "id": "nope"}}
        ],
    }  # type: ignore[dict-item]
    analyst = LlmClaimAnalyst(FakeEndpoint(ok(bad)).backend())
    with pytest.raises(LlmOutputError) as caught:
        run(analyst.analyze(claim_context("Đội nhà áp đảo hiệp 2")))
    assert any("unknown team id 'nope'" in e for e in caught.value.errors)


def test_code_fences_are_tolerated() -> None:
    fenced = "```json\n" + json.dumps(DOMINANCE_SPEC, ensure_ascii=False) + "\n```"
    analyst = LlmClaimAnalyst(FakeEndpoint(ok(fenced)).backend())
    spec = run(analyst.analyze(claim_context("Đội nhà áp đảo hiệp 2")))
    assert spec.assertions[0].kind == "dominance"


def explainer_answer(text: str) -> httpx.Response:
    return ok({"text": text})


def verify_with(endpoint: FakeEndpoint, text: str) -> Any:
    backend = endpoint.backend()
    service = ClaimService(
        COMPATIBLE, ports=lambda: (LlmClaimAnalyst(backend), LlmExplainer(backend))
    )
    request = ClaimRequest(match_id="dominant_loser-42", text=text, include_trace=True)
    return run(service.verify(request, source(), claim_id="dominant_loser-42-c0001"))


def by_schema(
    answers: dict[str, list[httpx.Response]],
) -> Callable[[dict[str, Any]], httpx.Response]:
    def answer(body: dict[str, Any]) -> httpx.Response:
        name = body["response_format"]["json_schema"]["name"]
        queue = answers[name]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return answer


def test_end_to_end_with_the_llm_path() -> None:
    explanation = (
        "Trong hiệp 2, Harbor Lions FC kiểm soát bóng 61%, "
        "sút 15 lần so với 1 của Kestrel Athletic."
    )
    endpoint = FakeEndpoint(
        by_schema(
            {
                "LlmClaimSpec": [ok(DOMINANCE_SPEC)],
                "LlmExplanation": [explainer_answer(explanation)],
            }
        )
    )
    result = verify_with(endpoint, "Đội nhà áp đảo hiệp 2")
    assert result.verdict == "TRUE" and result.degraded == ()
    assert (result.analyst_source, result.explanation_source) == ("llm", "llm")
    assert result.explanation == explanation and result.model == "test-model-mini"
    assert TOKEN not in result.model_dump_json()  # never in the result or the trace


def test_hallucinated_explanation_is_retried_with_feedback() -> None:
    wrong = "Trong hiệp 2, Harbor Lions FC kiểm soát bóng 65%."
    right = "Trong hiệp 2, Harbor Lions FC kiểm soát bóng 61%."
    endpoint = FakeEndpoint(
        by_schema(
            {
                "LlmClaimSpec": [ok(DOMINANCE_SPEC)],
                "LlmExplanation": [explainer_answer(wrong), explainer_answer(right)],
            }
        )
    )
    result = verify_with(endpoint, "Đội nhà áp đảo hiệp 2")
    assert result.explanation == right and result.explanation_source == "llm"
    retry_body = endpoint.body(len(endpoint.requests) - 1)
    assert "your_previous_text_was_rejected" in retry_body["messages"][-1]["content"]
    statuses = [s.status for s in result.trace if s.agent == "Guardrail"]
    assert statuses == ["rejected", "ok"]


def test_disagreeing_llm_spec_is_overruled_by_the_rules() -> None:
    swapped = json.loads(json.dumps(DOMINANCE_SPEC).replace("harbor_lions", "kestrel_athletic"))
    endpoint = FakeEndpoint(
        by_schema(
            {
                "LlmClaimSpec": [ok(swapped)],
                "LlmExplanation": [explainer_answer("Harbor Lions FC kiểm soát bóng 61%.")],
            }
        )
    )
    result = verify_with(endpoint, "Đội nhà áp đảo hiệp 2")
    assert result.verdict == "TRUE" and "analyst_disagreement" in result.degraded
    assert result.analyst_source == "rule"


def test_foundry_urls_and_credentials() -> None:
    assert (
        foundry_base_url("https://res.openai.azure.com")
        == "https://res.openai.azure.com/openai/v1/"
    )
    assert (
        foundry_base_url("https://res.openai.azure.com/openai/v1/")
        == "https://res.openai.azure.com/openai/v1/"
    )
    settings = Settings.from_env(
        {
            "USE_MOCK_LLM": "false",
            "LLM_PROVIDER": "foundry",
            "FOUNDRY_ENDPOINT": "https://res.openai.azure.com",
            "FOUNDRY_MODEL_DEPLOYMENT": "gpt-4.1-mini",
            "FOUNDRY_API_KEY": "dev-key",
        }
    )
    endpoint = FakeEndpoint(ok(DOMINANCE_SPEC))
    run(LlmClaimAnalyst(endpoint.backend(settings)).analyze(claim_context("Đội nhà áp đảo hiệp 2")))
    assert (
        str(endpoint.requests[0].url) == "https://res.openai.azure.com/openai/v1/chat/completions"
    )
    assert endpoint.body()["model"] == "gpt-4.1-mini"


def test_foundry_without_key_needs_azure_identity() -> None:
    pytest.importorskip("pytest")
    try:
        import azure.identity  # noqa: F401
    except ImportError:
        settings = Settings.from_env(
            {
                "USE_MOCK_LLM": "false",
                "LLM_PROVIDER": "foundry",
                "FOUNDRY_ENDPOINT": "https://res.openai.azure.com",
                "FOUNDRY_MODEL_DEPLOYMENT": "gpt-4.1-mini",
            }
        )
        with pytest.raises(ConfigError, match="azure-identity"):
            build_backend(settings)
    else:
        pytest.skip("azure-identity is installed")


def test_mock_settings_cannot_build_a_backend() -> None:
    with pytest.raises(ConfigError):
        build_backend(Settings())


def test_ollama_defaults_reach_the_local_server() -> None:
    settings = Settings.from_env({"USE_MOCK_LLM": "false", "LLM_PROVIDER": "ollama"})
    endpoint = FakeEndpoint(ok(DOMINANCE_SPEC))
    run(LlmClaimAnalyst(endpoint.backend(settings)).analyze(claim_context("Đội nhà áp đảo hiệp 2")))
    request = endpoint.requests[0]
    assert str(request.url) == "http://127.0.0.1:11434/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer ollama"  # placeholder, ignored by Ollama
    assert endpoint.body()["model"] == "qwen2.5:3b"


def test_retired_provider_text_answers_are_errors() -> None:
    """GitHub Models now answers a bare "OK" (text/plain): that must be an LLM error."""
    plain = httpx.Response(200, text="OK\n", headers={"content-type": "text/plain"})
    analyst = LlmClaimAnalyst(FakeEndpoint(plain).backend())
    with pytest.raises(LlmError):
        run(analyst.analyze(claim_context("Đội nhà áp đảo hiệp 2")))
