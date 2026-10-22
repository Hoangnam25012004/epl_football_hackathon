"""Durable clip store (docs/day5-design.md section 8.8): a local directory now, Blob Storage on
Azure. It keeps what is worth keeping across restarts: every Azure clip, the recaps and the
greeting. Clips are named by their content, so a write is idempotent (an existing clip is never
replaced) and a restarted app reads them back instead of paying for them again.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Protocol

from pundit.llm.settings import Settings
from pundit.speech.clips import valid_clip_id, write_atomic

log = logging.getLogger("pundit.speech")
IMMUTABLE = "public, max-age=31536000, immutable"
BLOB_PREFIX = "clips/"


class DurableStore(Protocol):
    @property
    def name(self) -> Literal["local", "blob"]: ...

    async def get(self, clip_id: str, extension: str) -> bytes | None: ...

    async def put(
        self, clip_id: str, extension: str, data: bytes, media_type: str,
        metadata: Mapping[str, str],
    ) -> None: ...  # fmt: skip

    async def aclose(self) -> None: ...


def _check(clip_id: str, extension: str) -> None:
    if not valid_clip_id(clip_id) or extension not in ("mp3", "wav"):
        raise ValueError(f"bad clip {clip_id!r}.{extension!r}")


class LocalStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @property
    def name(self) -> Literal["local"]:
        return "local"

    async def get(self, clip_id: str, extension: str) -> bytes | None:
        _check(clip_id, extension)
        path = self.directory / f"{clip_id}.{extension}"
        return await asyncio.to_thread(lambda: path.read_bytes() if path.is_file() else None)

    async def put(
        self, clip_id: str, extension: str, data: bytes, media_type: str,
        metadata: Mapping[str, str],
    ) -> None:  # fmt: skip
        _check(clip_id, extension)

        def write() -> None:
            if (self.directory / f"{clip_id}.{extension}").is_file():
                return
            meta = {"media_type": media_type, **metadata}
            write_atomic(self.directory, f"{clip_id}.json", json.dumps(meta).encode("utf-8"))
            write_atomic(self.directory, f"{clip_id}.{extension}", data)

        await asyncio.to_thread(write)

    async def aclose(self) -> None:
        return None


class BlobStore:
    """Private container, blobs `clips/{clip_id}.{ext}`. The SDK client is synchronous and runs
    in a thread: recaps and Azure clips are few, and no aiohttp is needed."""

    def __init__(self, container: Any) -> None:
        self._container = container
        self._ensured = False

    @classmethod
    def from_settings(cls, settings: Settings) -> "BlobStore":
        from azure.storage.blob import BlobServiceClient  # lazy: only with AUDIO_STORE=blob

        if settings.audio_blob_connection_string is not None:
            service = BlobServiceClient.from_connection_string(
                settings.audio_blob_connection_string.get_secret_value()
            )
        else:
            from azure.identity import DefaultAzureCredential

            service = BlobServiceClient(
                str(settings.audio_blob_account_url), credential=DefaultAzureCredential()
            )
        return cls(service.get_container_client(settings.audio_blob_container))

    @property
    def name(self) -> Literal["blob"]:
        return "blob"

    async def get(self, clip_id: str, extension: str) -> bytes | None:
        _check(clip_id, extension)
        from azure.core.exceptions import ResourceNotFoundError

        def read() -> bytes | None:
            blob = self._container.get_blob_client(f"{BLOB_PREFIX}{clip_id}.{extension}")
            try:
                data: bytes = blob.download_blob().readall()
            except ResourceNotFoundError:
                return None
            return data

        return await asyncio.to_thread(read)

    async def put(
        self, clip_id: str, extension: str, data: bytes, media_type: str,
        metadata: Mapping[str, str],
    ) -> None:  # fmt: skip
        _check(clip_id, extension)
        from azure.core.exceptions import HttpResponseError, ResourceExistsError
        from azure.storage.blob import ContentSettings

        def write() -> None:
            if not self._ensured:
                try:
                    self._container.create_container()
                except ResourceExistsError:
                    pass
                except HttpResponseError as exc:  # no right to create it: Bicep made it
                    log.info("audio container not created (%s)", exc.status_code)
                self._ensured = True
            blob = self._container.get_blob_client(f"{BLOB_PREFIX}{clip_id}.{extension}")
            with contextlib.suppress(ResourceExistsError):  # the same content is already there
                blob.upload_blob(
                    data,
                    overwrite=False,
                    content_settings=ContentSettings(
                        content_type=media_type, cache_control=IMMUTABLE
                    ),
                    metadata={k: str(v) for k, v in metadata.items()},
                )

        await asyncio.to_thread(write)

    async def aclose(self) -> None:
        await asyncio.to_thread(self._container.close)


def build_durable(settings: Settings) -> DurableStore | None:
    if settings.audio_store == "local":
        return LocalStore(settings.store_dir)
    if settings.audio_store == "blob":
        return BlobStore.from_settings(settings)
    return None
