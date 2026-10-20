"""Day 2 time budgets (design section 13.8). Skipped under coverage, like the Day 1 budgets."""

import statistics
import sys
import time

import pytest
from fastapi.testclient import TestClient
from helpers import generated, mock_service, verify

from pundit.agents.moments import detect_all
from pundit.api.app import create_app
from pundit.engine.replay import SteppingClock
from pundit.llm.settings import Settings

pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif("coverage" in sys.modules, reason="timings are meaningless under coverage"),
]

CLAIMS = (
    "Đội nhà áp đảo hiệp 2",
    "Harbor had twice as many shots as Kestrel in the second half",
    "KES chưa có quả phạt góc nào trong hiệp 2",
    "HAR áp đảo hiệp 2 nhưng vẫn thua",
    "Kestrel won despite lower xG",
    "HAR pressing mạnh hơn KES trong hiệp 2",
)


def p95(samples: list[float]) -> float:
    return statistics.quantiles(samples, n=20, method="inclusive")[-1]


def test_mock_claim_p95_under_150_ms() -> None:
    service = mock_service()
    verify(CLAIMS[0], service=service)  # warm-up: regex compilation, caches
    samples = []
    for _ in range(5):
        for text in CLAIMS:
            started = time.perf_counter()
            verify(text, service=service)
            samples.append((time.perf_counter() - started) * 1000)
    assert p95(samples) < 150, f"p95 {p95(samples):.0f} ms"


def test_api_verify_p95_under_300_ms() -> None:
    with TestClient(create_app(Settings(), clock_factory=SteppingClock)) as client:
        client.post(
            "/matches",
            json={"scenario": "dominant_loser", "seed": 42, "speed": 1000, "autostart": False},
        )
        body = {"match_id": "dominant_loser-42", "text": CLAIMS[0]}
        client.post("/claims/verify", json=body)  # warm-up
        samples = []
        for text in CLAIMS * 4:
            started = time.perf_counter()
            assert client.post("/claims/verify", json={**body, "text": text}).status_code == 200
            samples.append((time.perf_counter() - started) * 1000)
    assert p95(samples) < 300, f"p95 {p95(samples):.0f} ms"


def test_moment_detector_under_2_ms_per_event() -> None:
    match = generated("comeback_var", 7)
    started = time.perf_counter()
    detect_all(match.meta, match.events)
    per_event = (time.perf_counter() - started) * 1000 / len(match.events)
    assert per_event < 2.0, f"{per_event:.3f} ms per event"
