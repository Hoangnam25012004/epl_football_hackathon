"""Rate gate, clip cache and durable stores (docs/day5-design.md sections 8.5 and 8.8).

The Azurite test runs against a real Blob emulator when one listens on 127.0.0.1:10000
(`npx azurite-blob --silent --location <dir>`), and is skipped otherwise."""

import asyncio
import json
import os
import socket
from pathlib import Path
from typing import Any

import pytest
from helpers import run

from pundit.llm.settings import Settings
from pundit.speech.audio import mock_wav
from pundit.speech.clips import TEMP_PREFIX, ClipCache
from pundit.speech.durable import BlobStore, LocalStore, build_durable
from pundit.speech.gate import RateGate

ID1, ID2, ID3 = "1" * 32, "2" * 32, "3" * 32
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + bytes(5996)  # 1 s


# --------------------------------------------------------------------------- RateGate


def test_gate_admits_by_priority_then_arrival() -> None:
    async def scenario() -> list[str]:
        gate = RateGate(per_minute=600, concurrency=1)
        assert await gate.acquire(0, 1.0)  # holds the only slot
        order: list[str] = []

        async def waiter(name: str, priority: int) -> None:
            assert await gate.acquire(priority, 2.0)
            order.append(name)
            await gate.release()

        tasks = [
            asyncio.create_task(waiter(n, p)) for n, p in (("low", 0), ("goal", 3), ("mid", 1))
        ]
        await asyncio.sleep(0.05)
        await gate.release()
        await asyncio.gather(*tasks)
        return order

    assert run(scenario()) == ["goal", "mid", "low"]


def test_gate_rate_throttles_and_counts() -> None:
    async def scenario() -> tuple[list[bool], dict[str, Any]]:
        gate = RateGate(per_minute=2, concurrency=4)
        results = []
        for _ in range(3):
            admitted = await gate.acquire(1, 0.05)
            results.append(admitted)
            if admitted:
                await gate.release()
        return results, gate.stats()

    results, stats = run(scenario())
    assert results == [True, True, False]  # the third would wait 30 s for a token
    assert stats["throttled"] == 1 and stats["admitted"] == 2 and stats["waiting"] == 0


def test_gate_cooldown_after_429() -> None:
    async def scenario() -> tuple[bool, bool, float]:
        gate = RateGate(per_minute=600, concurrency=4)
        gate.cooldown(0.3)
        early = await gate.acquire(3, 0.05)
        loop = asyncio.get_running_loop()
        began = loop.time()
        later = await gate.acquire(3, 2.0)
        return early, later, loop.time() - began

    early, later, waited = run(scenario())
    assert not early and later and 0.15 <= waited <= 1.5


def test_gate_concurrency_and_cancellation() -> None:
    async def scenario() -> dict[str, Any]:
        gate = RateGate(per_minute=600, concurrency=2)
        assert await gate.acquire(1, 0.1) and await gate.acquire(1, 0.1)
        blocked = asyncio.create_task(gate.acquire(1, 5.0))
        await asyncio.sleep(0.05)
        assert not blocked.done()  # both slots taken
        blocked.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocked
        stats = gate.stats()
        await gate.release()
        assert await gate.acquire(1, 0.5)  # a released slot is reused
        return stats

    stats = run(scenario())
    assert stats["waiting"] == 0 and stats["active"] == 2


# --------------------------------------------------------------------------- ClipCache


def test_cache_put_get_and_atomic_files(tmp_path: Path) -> None:
    cache = ClipCache(tmp_path / "cache", max_bytes=10_000_000)
    assert not (tmp_path / "cache").exists()  # created on the first write only
    wav = mock_wav(500)
    clip = cache.put(ID1, wav, "audio/wav", 500)
    assert clip.path.read_bytes() == wav and clip.extension == "wav"
    assert cache.get(ID1) == clip
    assert not [p for p in clip.path.parent.iterdir() if p.name.startswith(TEMP_PREFIX)]
    assert cache.get("../../etc/passwd") is None and cache.get(ID2) is None
    with pytest.raises(ValueError):
        cache.put("../x", wav, "audio/wav", 500)
    clip.path.unlink()  # removed behind the cache's back
    assert cache.get(ID1) is None


def test_cache_rebuilds_its_index_and_drops_temp_files(tmp_path: Path) -> None:
    first = ClipCache(tmp_path, max_bytes=10_000_000)
    first.put(ID1, mock_wav(700), "audio/wav", 700)
    first.put(ID2, MP3, "audio/mpeg", 1000)
    (tmp_path / f"{TEMP_PREFIX}leftover").write_bytes(b"half a clip")
    (tmp_path / "notes.txt").write_text("not a clip")
    second = ClipCache(tmp_path, max_bytes=10_000_000)
    assert second.get(ID1) is not None and second.get(ID1).duration_ms == 700  # type: ignore[union-attr]
    assert second.get(ID2).duration_ms == 1000  # type: ignore[union-attr]
    assert not (tmp_path / f"{TEMP_PREFIX}leftover").exists()
    assert second.stats()["clips"] == 2


def test_cache_evicts_least_recently_used_but_keeps_recent(tmp_path: Path) -> None:
    now = [0.0]
    wav = mock_wav(300)  # ~9.6 KB
    cache = ClipCache(tmp_path, max_bytes=2 * len(wav), keep_recent_s=600, clock=lambda: now[0])
    cache.put(ID1, wav, "audio/wav", 300)
    now[0] = 10
    cache.put(ID2, wav, "audio/wav", 300)
    now[0] = 20
    cache.put(ID3, wav, "audio/wav", 300)  # over the limit, but every clip is recent
    assert cache.stats()["clips"] == 3 and cache.stats()["evicted"] == 0
    now[0] = 1000  # the first two are old now: the next write evicts the least recently used
    cache.get(ID2)
    cache.put("4" * 32, wav, "audio/wav", 300)
    assert cache.get(ID1) is None and cache.get(ID2) is not None
    assert not (tmp_path / f"{ID1}.wav").exists()


def test_cache_hard_limit_evicts_even_recent_clips(tmp_path: Path) -> None:
    wav = mock_wav(300)
    cache = ClipCache(tmp_path, max_bytes=len(wav), keep_recent_s=600, clock=lambda: 0.0)
    for clip_id in (ID1, ID2, ID3):
        cache.put(clip_id, wav, "audio/wav", 300)
    assert cache.stats()["bytes"] <= 1.5 * len(wav) + len(wav)


# --------------------------------------------------------------------------- durable stores


def test_local_store_round_trip_and_idempotence(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "store")
    assert run(store.get(ID1, "mp3")) is None
    run(store.put(ID1, "mp3", MP3, "audio/mpeg", {"kind": "recap", "scope": "FULL"}))
    run(store.put(ID1, "mp3", b"other bytes", "audio/mpeg", {"kind": "recap"}))  # kept as is
    assert run(store.get(ID1, "mp3")) == MP3
    meta = json.loads((tmp_path / "store" / f"{ID1}.json").read_text(encoding="utf-8"))
    assert meta == {"media_type": "audio/mpeg", "kind": "recap", "scope": "FULL"}
    with pytest.raises(ValueError):
        run(store.get("bad", "mp3"))
    with pytest.raises(ValueError):
        run(store.put(ID1, "exe", MP3, "audio/mpeg", {}))


class FakeBlob:
    def __init__(self, container: "FakeContainer", name: str) -> None:
        self.container, self.name = container, name

    def upload_blob(self, data: bytes, *, overwrite: bool, content_settings: Any,
                    metadata: dict[str, str]) -> None:  # fmt: skip
        from azure.core.exceptions import ResourceExistsError

        assert overwrite is False
        if self.name in self.container.blobs:
            raise ResourceExistsError("BlobAlreadyExists")
        self.container.blobs[self.name] = (data, content_settings, metadata)

    def download_blob(self) -> Any:
        from azure.core.exceptions import ResourceNotFoundError

        if self.name not in self.container.blobs:
            raise ResourceNotFoundError("BlobNotFound")
        data = self.container.blobs[self.name][0]
        return type("Download", (), {"readall": lambda _: data})()


class FakeContainer:
    def __init__(self) -> None:
        self.blobs: dict[str, tuple[bytes, Any, dict[str, str]]] = {}
        self.created = 0
        self.closed = False

    def create_container(self) -> None:
        from azure.core.exceptions import ResourceExistsError

        self.created += 1
        raise ResourceExistsError("ContainerAlreadyExists")

    def get_blob_client(self, name: str) -> FakeBlob:
        return FakeBlob(self, name)

    def close(self) -> None:
        self.closed = True


def test_blob_store_with_a_fake_container() -> None:
    container = FakeContainer()
    store = BlobStore(container)
    assert run(store.get(ID1, "mp3")) is None
    run(store.put(ID1, "mp3", MP3, "audio/mpeg", {"kind": "recap", "chars": 120}))  # type: ignore[dict-item]
    run(store.put(ID1, "mp3", b"again", "audio/mpeg", {}))  # exists: fine, unchanged
    data, settings, metadata = container.blobs[f"clips/{ID1}.mp3"]
    assert data == MP3 and settings.content_type == "audio/mpeg"
    assert "immutable" in settings.cache_control and metadata == {"kind": "recap", "chars": "120"}
    assert run(store.get(ID1, "mp3")) == MP3 and container.created == 1
    run(store.aclose())
    assert container.closed


def test_build_durable_follows_the_settings(tmp_path: Path) -> None:
    assert build_durable(Settings(audio_store="none")) is None
    local = build_durable(Settings(audio_store="local", audio_store_dir=str(tmp_path)))
    assert isinstance(local, LocalStore) and local.directory == tmp_path


def azurite_listening() -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", 10000)) == 0


@pytest.mark.skipif(not azurite_listening(), reason="no Azurite on 127.0.0.1:10000")
def test_blob_store_against_azurite() -> None:
    from pydantic import SecretStr

    settings = Settings(
        audio_store="blob",
        audio_blob_connection_string=SecretStr("UseDevelopmentStorage=true"),
        audio_blob_container=f"audio-test-{os.getpid()}",
    )
    store = build_durable(settings)
    assert isinstance(store, BlobStore)
    clip_id = os.urandom(16).hex()
    assert run(store.get(clip_id, "mp3")) is None
    run(store.put(clip_id, "mp3", MP3, "audio/mpeg", {"kind": "recap", "scope": "H1"}))
    run(store.put(clip_id, "mp3", b"x", "audio/mpeg", {}))  # BlobAlreadyExists: kept
    assert run(store.get(clip_id, "mp3")) == MP3
    run(store.aclose())
