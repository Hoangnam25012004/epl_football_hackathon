"""A private session per viewer (docs/day6-design.md section 6, W11): session ids, a watched
session never stopped, the cap (503), new private sessions per address (429), the janitor, the
API, the WebSocket, claims and MCP on a session id, and a state machine of random lives."""

import itertools
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import api_settings, generated, mock_service, run
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from pundit.agents.contracts import ClaimRequest
from pundit.api.app import create_app
from pundit.api.sessions import (
    PRIVATE_WINDOW_S,
    MatchSession,
    SessionManager,
    SessionsFull,
    TooManySessions,
)
from pundit.domain.ids import SESSION_ID_PATTERN, new_token, parse_session_id, session_id
from pundit.engine.replay import SteppingClock
from pundit.llm.settings import ConfigError, Settings, read_env_file

KEY = re.compile(SESSION_ID_PATTERN)


class Now:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def manager(now: Now, **options: Any) -> SessionManager:
    sessions = SessionManager(mock_service(), clock_factory=SteppingClock, now=now, **options)
    for key in (("balanced", 1), ("dominant_loser", 42)):
        sessions._matches[key] = generated(*key)  # generated once for the whole module
    return sessions


def create(sessions: SessionManager, **options: Any) -> MatchSession:
    options.setdefault("autostart", False)
    scenario = options.pop("scenario", "balanced")
    seed = options.pop("seed", 1)
    return run(sessions.create(scenario, seed, **options))


def watch(session: MatchSession) -> Any:
    return session.hub.subscribe()


# ---------------------------------------------------------------------------------- ids


def test_session_ids() -> None:
    assert session_id("dominant_loser-42", None) == "dominant_loser-42"
    assert session_id("dominant_loser-42", "k3v9x2ma") == "dominant_loser-42~k3v9x2ma"
    assert parse_session_id("dominant_loser-42~k3v9x2ma") == ("dominant_loser-42", "k3v9x2ma")
    assert parse_session_id("dominant_loser-42") == ("dominant_loser-42", None)
    for good in ("dominant_loser-42", "balanced-0~a1b2c3d4"):
        assert KEY.match(good)
    for bad in ("dominant_loser-42~short", "dominant_loser-42~ABCD1234", "x~abcdefgh", "a-1~"):
        assert not KEY.match(bad)


def test_tokens_are_random_and_well_formed() -> None:
    tokens = {new_token() for _ in range(2000)}
    assert len(tokens) == 2000
    assert all(re.fullmatch(r"[a-z0-9]{8}", t) for t in tokens)


# ----------------------------------------------------------------------------- manager


def test_a_private_session_has_its_own_id_and_claims() -> None:
    now = Now()
    sessions = manager(now)
    shared = create(sessions)
    mine, theirs = create(sessions, private=True), create(sessions, private=True)
    assert shared.key == "balanced-1" and not shared.private
    assert mine.key.startswith("balanced-1~") and KEY.match(mine.key) and mine.private
    assert len({shared.key, mine.key, theirs.key}) == 3
    assert mine.match_id == "balanced-1"  # the data keeps the match id
    assert mine.info()["match_id"] == mine.key and mine.info()["private"] is True
    assert "private" not in shared.info()  # the Day 5 shape
    assert mine.next_claim_id() != theirs.next_claim_id()
    assert mine.hub.match_id == mine.key
    assert [s.key for s in sessions.list()] == ["balanced-1"]  # private ones are not listed


def test_a_token_names_a_session_restarted_or_made_again() -> None:
    now = Now()
    sessions = manager(now)
    first = create(sessions, private=True)
    _, token = parse_session_id(first.key)
    same = create(sessions, token=token)
    assert same is first  # idempotent
    again = create(sessions, token=token, restart=True)
    assert again is not first and again.key == first.key
    del sessions.sessions[first.key]  # as the janitor would
    made = create(sessions, token=token)
    assert made.key == first.key and made.private


def test_a_watched_session_is_never_stopped_to_make_room() -> None:
    now = Now()
    sessions = manager(now, max_sessions=2)
    a = create(sessions, private=True)
    b = create(sessions, private=True)
    watch(a), watch(b)
    with pytest.raises(SessionsFull):
        create(sessions, private=True, client="other")
    assert set(sessions.sessions) == {a.key, b.key}


def test_thirty_viewers_ten_of_them_watching() -> None:
    """W11 load: 30 viewers each make a private session and 10 watch theirs (cap 16). Room is
    always made with an unwatched one; once all 16 are watched, the next one is refused (503)."""
    now = Now()
    sessions = manager(now, max_sessions=16)
    watched: list[MatchSession] = []
    for viewer in range(30):
        now.t += 1
        made = create(sessions, private=True, client=f"10.0.0.{viewer}")
        if viewer < 10:
            watch(made)
            watched.append(made)
        assert all(w.key in sessions.sessions for w in watched)  # never a watched one
        assert len(sessions.sessions) <= 16
    assert len(sessions.sessions) == 16
    for session in list(sessions.sessions.values()):
        if session not in watched:
            watch(session)
    with pytest.raises(SessionsFull):
        create(sessions, private=True, client="10.0.1.1")
    assert len(sessions.sessions) == 16 and all(w.key in sessions.sessions for w in watched)


def test_room_is_made_with_a_finished_session_first_then_the_longest_idle() -> None:
    now = Now()
    sessions = manager(now, max_sessions=3, private_per_ip=10)
    old = create(sessions, private=True)
    now.t += 10
    finished = create(sessions, private=True)
    finished.status = "finished"
    now.t += 10
    watched = create(sessions, private=True)
    watch(watched)
    fresh = create(sessions, private=True)
    assert finished.key not in sessions.sessions and old.key in sessions.sessions
    newest = create(sessions, private=True)
    assert old.key not in sessions.sessions  # then the one left alone longest
    assert {watched.key, fresh.key, newest.key} == set(sessions.sessions)


def test_new_private_sessions_per_address() -> None:
    now = Now()
    sessions = manager(now, private_per_ip=2)
    create(sessions, private=True, client="198.51.100.1")
    second = create(sessions, private=True, client="198.51.100.1")
    with pytest.raises(TooManySessions) as caught:
        create(sessions, private=True, client="198.51.100.1")
    assert 0 < caught.value.retry_after_s <= PRIVATE_WINDOW_S
    with pytest.raises(TooManySessions):  # a made-up token is a new session too
        create(sessions, token="zzzzzzzz", client="198.51.100.1")
    create(sessions, private=True, client="198.51.100.2")  # another address
    _, token = parse_session_id(second.key)
    create(sessions, token=token, restart=True, client="198.51.100.1")  # a restart is free
    now.t += PRIVATE_WINDOW_S + 1
    create(sessions, private=True, client="198.51.100.1")


def test_the_shared_sessions_are_not_limited_per_address() -> None:
    now = Now()
    sessions = manager(now, private_per_ip=1)
    for seed in (1, 2, 3):
        run(sessions.create("balanced", seed, autostart=False, client="198.51.100.1"))


def test_the_janitor() -> None:
    now = Now()
    sessions = manager(now, private_idle_s=900, shared_idle_s=3600)
    shared = create(sessions)
    alone = create(sessions, private=True)
    seen = create(sessions, private=True)
    viewer = watch(seen)
    now.t += 899
    assert run(sessions.sweep()) == []
    now.t += 2
    assert run(sessions.sweep()) == [alone.key]  # a private session nobody watched for 15'
    seen.hub.unsubscribe(viewer)  # the viewer left a moment ago: kept
    now.t += 10
    assert run(sessions.sweep()) == []
    now.t += 3600
    removed = run(sessions.sweep())
    assert set(removed) == {seen.key, shared.key}
    assert sessions.sessions == {}


def test_a_session_used_through_the_api_outlives_the_janitor() -> None:
    """Day 7: an eval sent 100 claims over two hours to a shared session nobody watched; it was
    removed an hour after it was made, in the middle of a claim."""
    now = Now()
    sessions = manager(now, private_idle_s=900, shared_idle_s=3600)
    shared = create(sessions)
    request = ClaimRequest(match_id=shared.key, text="Đội nhà áp đảo hiệp 2")
    for _ in range(3):
        now.t += 3000
        run(shared.verify(request))
        assert run(sessions.sweep()) == [], "a session with a claim 50 min ago is not idle"
    now.t += 3601
    assert run(sessions.sweep()) == [shared.key]  # idle again: removed, as before


def test_a_watched_session_outlives_the_janitor() -> None:
    now = Now()
    sessions = manager(now)
    session = create(sessions, private=True)
    watch(session)
    now.t += 10 * 3600
    assert run(sessions.sweep()) == [] and session.key in sessions.sessions


# --------------------------------------------------------------------------------- API


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = api_settings(max_sessions=4, private_per_ip=2)
    with TestClient(create_app(settings, clock_factory=SteppingClock)) as test_client:
        yield test_client


def post(client: TestClient, **body: Any) -> Any:
    payload = {"scenario": "dominant_loser", "seed": 42, "speed": 1000, "autostart": False, **body}
    return client.post("/matches", json=payload)


def test_api_private_sessions(client: TestClient) -> None:
    shared = post(client).json()
    mine = post(client, private=True).json()
    assert "private" not in shared and shared["match_id"] == "dominant_loser-42"
    assert mine["private"] is True and mine["match_id"].startswith("dominant_loser-42~")
    listed = [m["match_id"] for m in client.get("/matches").json()]
    assert listed == ["dominant_loser-42"]
    assert client.get(f"/matches/{mine['match_id']}").json()["info"]["private"] is True
    token = parse_session_id(mine["match_id"])[1]
    again = post(client, session=token, restart=True).json()
    assert again["match_id"] == mine["match_id"]


def test_api_claims_and_websocket_on_a_session_id(client: TestClient) -> None:
    key = post(client, private=True, autostart=True).json()["match_id"]
    with client.websocket_connect(f"/ws/match/{key}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["match_id"] == key
    claim = client.post("/claims/verify", json={"match_id": key, "text": "Kestrel won"})
    assert claim.status_code == 200, claim.text
    body = claim.json()
    assert body["match_id"] == key and body["claim_id"].startswith(key + "-c")
    assert client.get(f"/claims/{body['claim_id']}").status_code == 200


def test_api_refusals_say_when_to_retry(client: TestClient) -> None:
    post(client, private=True)
    post(client, private=True)
    limited = post(client, private=True)
    assert limited.status_code == 429
    assert limited.json()["detail"] == "too_many_sessions"
    assert int(limited.headers["retry-after"]) == limited.json()["retry_after_s"] > 0
    bad = post(client, session="NOT-A-TOKEN")
    assert bad.status_code == 422


def test_api_demo_full(client: TestClient) -> None:
    keys = [post(client, seed=seed).json()["match_id"] for seed in (1, 2, 3, 4)]
    sockets = [client.websocket_connect(f"/ws/match/{key}") for key in keys]
    for ws in sockets:
        ws.__enter__().receive_json()
    try:
        full = post(client, seed=5)
        assert full.status_code == 503 and full.json() == {"detail": "demo_full",
                                                           "retry_after_s": 60}  # fmt: skip
        assert full.headers["retry-after"] == "60"
        assert [m["status"] for m in client.get("/matches").json()] == ["created"] * 4
    finally:
        for ws in sockets:
            ws.__exit__(None, None, None)


def test_api_counts_the_forwarded_client_behind_a_trusted_proxy() -> None:
    settings = api_settings(private_per_ip=1, trust_proxy=True)
    with TestClient(create_app(settings, clock_factory=SteppingClock)) as client:
        one = {"X-Forwarded-For": "203.0.113.1, 198.51.100.7"}
        two = {"X-Forwarded-For": "203.0.113.1, 198.51.100.8"}  # the same forged first hop
        body = {"scenario": "balanced", "seed": 1, "autostart": False, "private": True}
        assert client.post("/matches", json=body, headers=one).status_code == 200
        assert client.post("/matches", json=body, headers=one).status_code == 429
        assert client.post("/matches", json=body, headers=two).status_code == 200


def test_mcp_reads_a_private_session_it_does_not_list() -> None:
    settings = api_settings(mcp_enabled=True)
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with TestClient(create_app(settings, clock_factory=SteppingClock),
                    base_url="http://127.0.0.1:8000") as client:  # fmt: skip
        key = post(client, private=True, autostart=True).json()["match_id"]

        def tool(name: str, arguments: dict[str, Any]) -> Any:
            message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments}}  # fmt: skip
            return client.post("/mcp", json=message, headers=headers).json()["result"]

        board = tool("get_scoreboard", {"match_id": key})["structuredContent"]
        assert (board["match_id"], board["source"]) == (key, "live")
        listed = tool("list_matches", {})["structuredContent"]["live"]
        assert key not in [entry["match_id"] for entry in listed]


def test_session_settings() -> None:
    values = Settings.from_env(
        {"MAX_SESSIONS": "8", "PRIVATE_IDLE_S": "60", "SHARED_IDLE_S": "120", "PRIVATE_PER_IP": "5"}
    )
    assert (values.max_sessions, values.private_idle_s, values.shared_idle_s) == (8, 60.0, 120.0)
    assert values.private_per_ip == 5
    defaults = Settings.from_env({})
    assert (defaults.max_sessions, defaults.private_idle_s, defaults.shared_idle_s) == (
        16,
        900.0,
        3600.0,
    )
    for name, value in (("MAX_SESSIONS", "0"), ("PRIVATE_IDLE_S", "0"), ("PRIVATE_PER_IP", "x")):
        with pytest.raises(ConfigError, match=name):
            Settings.from_env({name: value})
    example = read_env_file(Path(__file__).resolve().parents[2] / ".env.example")
    for name in ("MAX_SESSIONS", "PRIVATE_IDLE_S", "SHARED_IDLE_S", "PRIVATE_PER_IP"):
        assert name in example
    assert Settings.from_env(example).max_sessions == 16


# ----------------------------------------------------------------------- state machine


class Lives(RuleBasedStateMachine):
    """Random creations, viewers coming and going, time passing and sweeps: a watched session
    is never removed, the cap holds, every id is a session id."""

    def __init__(self) -> None:
        super().__init__()
        self.now = Now()
        self.sessions = manager(self.now, max_sessions=4, private_per_ip=50)
        self.viewers: dict[str, list[Any]] = {}
        # tokens in order, not random: the sorted ids `come` draws from must be the same each
        # time Hypothesis replays an example (else FlakyStrategyDefinition, now and then)
        tokens = (f"t{n:07d}" for n in itertools.count())
        self.sessions._fresh_token = lambda match_id: next(tokens)  # type: ignore[method-assign]

    def _watched(self) -> set[str]:
        return {key for key, clients in self.viewers.items() if clients}

    @rule(private=st.booleans(), seed=st.sampled_from([1]))
    def create(self, private: bool, seed: int) -> None:
        watched = self._watched()
        try:
            create(self.sessions, private=private, seed=seed)
        except SessionsFull:
            assert len(self.sessions.sessions) >= 4 and watched >= set(self.sessions.sessions)

    @rule(data=st.data())
    def come(self, data: st.DataObject) -> None:
        if self.sessions.sessions:
            key = data.draw(st.sampled_from(sorted(self.sessions.sessions)))
            self.viewers.setdefault(key, []).append(self.sessions.sessions[key].hub.subscribe())

    @rule(data=st.data())
    def leave(self, data: st.DataObject) -> None:
        watched = sorted(k for k in self._watched() if k in self.sessions.sessions)
        if watched:
            key = data.draw(st.sampled_from(watched))
            self.sessions.sessions[key].hub.unsubscribe(self.viewers[key].pop())

    @rule(seconds=st.floats(min_value=0, max_value=5000))
    def wait_and_sweep(self, seconds: float) -> None:
        watched = self._watched() & set(self.sessions.sessions)
        self.now.t += seconds
        removed = run(self.sessions.sweep())
        assert not set(removed) & watched

    @invariant()
    def holds(self) -> None:
        assert len(self.sessions.sessions) <= 4
        assert all(KEY.match(key) for key in self.sessions.sessions)
        for key in self._watched():
            assert key in self.sessions.sessions or not self.viewers[key]
        for key in list(self.viewers):
            if key not in self.sessions.sessions:
                assert not self.viewers[key]
                del self.viewers[key]


TestLives = Lives.TestCase
TestLives.settings = hypothesis_settings(max_examples=40, stateful_step_count=25, deadline=None)
