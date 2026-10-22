"""Day 4 REST endpoints (docs/day4-design.md sections 3.1-3.7) through FastAPI's TestClient."""

import json
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import STUDIO_ON, api_settings

from pundit.api.app import create_app
from pundit.engine.replay import SteppingClock
from pundit.llm.settings import Settings

MATCH = "dominant_loser-42"
EVALS = Path(__file__).resolve().parents[2] / "evals" / "claims_dominant_loser_42.json"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(api_settings(), clock_factory=SteppingClock)) as test_client:
        yield test_client


def create(client: TestClient, **body: Any) -> dict[str, Any]:
    payload = {"scenario": "dominant_loser", "seed": 42, "speed": 1000, **body}
    response = client.post("/matches", json=payload)
    assert response.status_code == 200, response.text
    info: dict[str, Any] = response.json()
    return info


def wait_status(client: TestClient, *statuses: str, match_id: str = MATCH) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        info: dict[str, Any] = client.get(f"/matches/{match_id}").json()["info"]
        if info["status"] in statuses:
            return info
        time.sleep(0.01)
    raise AssertionError(f"never reached {statuses}")


def test_scenarios_list_teams_colours_and_example_claims(client: TestClient) -> None:
    body = client.get("/scenarios").json()
    assert [s["key"] for s in body] == [
        "dominant_loser",
        "comeback_var",
        "red_card_swing",
        "balanced",
    ]
    for scenario in body:
        assert scenario["default_seed"] == 42 and scenario["title_vi"] and scenario["title_en"]
        for side in ("home", "away"):
            team = scenario[side]
            assert re.fullmatch(r"#[0-9A-F]{6}", team["primary_color"]), team
            assert team["short_name"] and team["name"]
        assert scenario["example_claims"]["vi"] and scenario["example_claims"]["en"]
    labelled = {c["text"] for c in json.loads(EVALS.read_text(encoding="utf-8"))["claims"]}
    loser = body[0]["example_claims"]
    assert set(loser["vi"]) | set(loser["en"]) <= labelled  # one source: the Day 2 eval set


def test_matches_list(client: TestClient) -> None:
    assert client.get("/matches").json() == []
    create(client, autostart=False)
    listed = client.get("/matches").json()
    assert [m["match_id"] for m in listed] == [MATCH] and listed[0]["status"] == "created"


def test_players_of_both_squads(client: TestClient) -> None:
    assert client.get("/matches/nope-1/players").status_code == 404
    create(client, autostart=False)
    players = client.get(f"/matches/{MATCH}/players").json()
    teams = [p["team"] for p in players]
    assert teams[0] == "harbor_lions" and teams[-1] == "kestrel_athletic"
    assert teams == sorted(teams, key=lambda t: t != "harbor_lions")  # home squad first
    home = [p["shirt"] for p in players if p["team"] == "harbor_lions"]
    assert home == sorted(home) and all(p["name"] and p["short_name"] for p in players)


def test_pause_resume_speed(client: TestClient) -> None:
    for path in ("pause", "resume"):
        assert client.post(f"/matches/nope-1/{path}").status_code == 404
    assert client.post("/matches/nope-1/speed", json={"speed": 5}).status_code == 404
    create(client, autostart=False)
    assert client.post(f"/matches/{MATCH}/pause").json()["status"] == "created"  # no-op
    for bad in (0, -1, 1001, "fast"):
        assert client.post(f"/matches/{MATCH}/speed", json={"speed": bad}).status_code == 422
    assert client.post(f"/matches/{MATCH}/speed", json={"speed": 5, "x": 1}).status_code == 422
    assert client.post(f"/matches/{MATCH}/speed", json={"speed": 1000}).json()["speed"] == 1000
    assert client.post(f"/matches/{MATCH}/resume").json()["status"] == "running"
    paused = client.post(f"/matches/{MATCH}/pause").json()
    if paused["status"] == "paused":  # the replay may already be over at 1000x
        before = paused["events_applied"]
        time.sleep(0.2)
        assert client.get(f"/matches/{MATCH}").json()["info"]["events_applied"] == before
        assert client.post(f"/matches/{MATCH}/resume").json()["status"] == "running"
    wait_status(client, "finished")


def test_timeline_stats_and_events(client: TestClient) -> None:
    create(client)
    wait_status(client, "finished")
    timeline = client.get(f"/matches/{MATCH}/timeline").json()
    assert timeline["points"] and timeline["home"] == "harbor_lions"
    assert [m["display_minute"] for m in timeline["markers"] if m["kind"] == "goal"] == [
        "27'",
        "63'",
        "78'",
    ]
    assert client.get(f"/matches/{MATCH}/timeline?window=0").status_code == 422
    stats = client.get(f"/matches/{MATCH}/stats?period=H2").json()
    row = next(r for r in stats["rows"] if r["metric"] == "shots")
    assert (row["home"]["display"], row["away"]["display"]) == ("15", "1")  # eval e01
    assert client.get(f"/matches/{MATCH}/stats?period=H3").status_code == 422
    last = client.get(f"/matches/{MATCH}/events?last=5").json()
    assert len(last["events"]) == 5 and last["missing"] == []
    ids = ",".join([last["events"][0]["id"], "dominant_loser-42-99999", "x"])
    page = client.get(f"/matches/{MATCH}/events", params={"ids": ids}).json()
    assert [e["id"] for e in page["events"]] == [last["events"][0]["id"]]
    assert page["missing"] == ["dominant_loser-42-99999", "x"]
    for query in ("", "?ids=a&last=2", "?last=0", "?last=301", "?ids=,,"):
        assert client.get(f"/matches/{MATCH}/events{query}").status_code == 422, query
    many = ",".join(f"id{i}" for i in range(201))
    assert client.get(f"/matches/{MATCH}/events", params={"ids": many}).status_code == 422
    assert client.get("/matches/nope-1/timeline").status_code == 404


@pytest.mark.skipif(not STUDIO_ON, reason="the on-air control belongs to the studio")
def test_on_air_endpoints(client: TestClient) -> None:
    create(client)
    wait_status(client, "finished")
    state = client.get(f"/matches/{MATCH}/on-air").json()
    assert state["mode"] == "auto" and state["revision"] == 0
    assert client.post(f"/matches/{MATCH}/on-air", json={"action": "take"}).status_code == 404
    unknown = client.post(f"/matches/{MATCH}/on-air", json={"action": "take", "item_id": "x"})
    assert unknown.status_code == 404
    assert client.post(f"/matches/{MATCH}/on-air", json={"action": "jump"}).status_code == 422
    item = client.get(f"/matches/{MATCH}/lower-thirds").json()[0]["id"]
    taken = client.post(f"/matches/{MATCH}/on-air", json={"action": "take", "item_id": item})
    assert taken.json()["mode"] == "manual" and taken.json()["item_id"] == item
    assert client.post(f"/matches/{MATCH}/on-air", json={"action": "auto"}).json()["revision"] == 2


def test_on_air_needs_the_studio() -> None:
    settings = Settings(studio_enabled=False)
    with TestClient(create_app(settings, clock_factory=SteppingClock)) as test_client:
        create(test_client, autostart=False)
        assert test_client.get(f"/matches/{MATCH}/on-air").status_code == 404


def write_build(root: Path) -> Path:
    (root / "_next" / "static" / "chunks").mkdir(parents=True)
    (root / "studio").mkdir()
    (root / "index.html").write_text("<html>home</html>", encoding="utf-8")
    (root / "studio" / "index.html").write_text("<html>studio</html>", encoding="utf-8")
    (root / "_next" / "static" / "chunks" / "app.js").write_text("1", encoding="utf-8")
    return root


def test_the_ui_is_served_after_the_api(tmp_path: Path) -> None:
    settings = Settings(ui_dir=str(write_build(tmp_path)))
    with TestClient(create_app(settings, clock_factory=SteppingClock)) as test_client:
        home = test_client.get("/")
        assert home.status_code == 200 and "home" in home.text
        assert home.headers["cache-control"] == "no-cache"
        studio = test_client.get("/studio/")
        assert "studio" in studio.text and studio.headers["cache-control"] == "no-cache"
        asset = test_client.get("/_next/static/chunks/app.js")
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert test_client.get("/matches").json() == []  # the API wins over the UI
        assert test_client.get("/healthz").json()["status"] == "ok"
        assert test_client.get("/docs").status_code == 200
        assert test_client.get("/nope.html").status_code == 404


def test_no_ui_without_a_build(tmp_path: Path) -> None:
    for ui_dir in (None, str(tmp_path)):  # no setting, or a folder without index.html
        with TestClient(create_app(Settings(ui_dir=ui_dir))) as test_client:
            assert test_client.get("/").status_code == 404
            assert test_client.get("/healthz").status_code == 200


def test_serve_refuses_a_missing_build(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from pundit.cli import main

    assert main(["serve", "--ui", str(tmp_path)]) == 2
    assert "npm run build" in capsys.readouterr().out
