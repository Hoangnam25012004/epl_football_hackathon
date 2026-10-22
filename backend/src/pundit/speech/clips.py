"""ClipCache: every clip the app made, as files in AUDIO_CACHE_DIR (docs/day5-design.md 8.8).

A clip is written to a temporary file of the same directory and renamed, so a reader never sees
half a file. The cache is least-recently-used by size, but never drops a clip used in the last
ten minutes (a listener may be about to fetch it) unless it is 1.5 times over its limit. The
directory is created on the first write: an app that never speaks writes nothing.
"""

import contextlib
import logging
import os
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pundit.agents.contracts import CLIP_ID_PATTERN, AudioMediaType
from pundit.speech.audio import (
    EXTENSIONS,
    MEDIA_TYPES,
    mp3_duration_of_size,
    wav_duration_of_size,
)

log = logging.getLogger("pundit.speech")
KEEP_RECENT_S = 600.0
HARD_LIMIT_FACTOR = 1.5
TEMP_PREFIX = ".tmp-"
_CLIP_ID = re.compile(CLIP_ID_PATTERN)
_FILE = re.compile(r"^([0-9a-f]{32})\.(mp3|wav)$")


@dataclass(frozen=True)
class ClipFile:
    clip_id: str
    path: Path
    media_type: AudioMediaType
    duration_ms: int
    size: int

    @property
    def extension(self) -> str:
        return EXTENSIONS[self.media_type]


def valid_clip_id(clip_id: str) -> bool:
    return _CLIP_ID.match(clip_id) is not None


def _duration_of(path: Path, media_type: AudioMediaType) -> int:
    """From the size and the first bytes only: a cache of hundreds of clips scans quickly."""
    with path.open("rb") as handle:
        head = handle.read(4096)
    size = path.stat().st_size
    if media_type == "audio/mpeg":
        return mp3_duration_of_size(size, head)
    return wav_duration_of_size(size, head)


def write_atomic(directory: Path, name: str, data: bytes) -> Path:
    """Write `data` as `directory/name` through a temporary file and a rename."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    handle, temp = tempfile.mkstemp(dir=directory, prefix=TEMP_PREFIX)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temp, target)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        if not target.exists():  # another writer may hold the same content: that is fine
            raise
    return target


class ClipCache:
    def __init__(
        self,
        directory: Path,
        max_bytes: int,
        *,
        keep_recent_s: float = KEEP_RECENT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.directory = directory
        self.max_bytes = max_bytes
        self.keep_recent_s = keep_recent_s
        self._clock = clock
        self._index: dict[str, ClipFile] = {}
        self._used: dict[str, float] = {}
        self._bytes = 0
        self.evicted = 0
        self._scan()

    def _scan(self) -> None:
        """Index the clips a previous run left; drop temporary files of an interrupted write."""
        if not self.directory.is_dir():
            return
        for path in self.directory.iterdir():
            if path.name.startswith(TEMP_PREFIX):
                with contextlib.suppress(OSError):
                    path.unlink()
                continue
            match = _FILE.match(path.name)
            if match is None:
                continue
            media_type = MEDIA_TYPES[match.group(2)]
            try:
                clip = ClipFile(
                    match.group(1), path, media_type, _duration_of(path, media_type),
                    path.stat().st_size,
                )  # fmt: skip
            except (OSError, ValueError):
                log.warning("ignoring unreadable clip %s", path.name)
                continue
            self._add(clip)

    def _add(self, clip: ClipFile) -> None:
        old = self._index.get(clip.clip_id)
        if old is not None:
            self._bytes -= old.size
        self._index[clip.clip_id] = clip
        self._used[clip.clip_id] = self._clock()
        self._bytes += clip.size

    def get(self, clip_id: str) -> ClipFile | None:
        if not valid_clip_id(clip_id):
            return None
        clip = self._index.get(clip_id)
        if clip is None:
            return None
        if not clip.path.is_file():  # removed behind our back
            self._forget(clip_id)
            return None
        self._used[clip_id] = self._clock()
        return clip

    def put(
        self, clip_id: str, data: bytes, media_type: AudioMediaType, duration_ms: int
    ) -> ClipFile:
        if not valid_clip_id(clip_id):
            raise ValueError(f"bad clip id {clip_id!r}")
        name = f"{clip_id}.{EXTENSIONS[media_type]}"
        path = self.directory / name
        if not path.is_file():
            path = write_atomic(self.directory, name, data)
        clip = ClipFile(clip_id, path, media_type, duration_ms, len(data))
        self._add(clip)
        self._evict(keep=clip_id)
        return clip

    def _forget(self, clip_id: str) -> None:
        clip = self._index.pop(clip_id, None)
        self._used.pop(clip_id, None)
        if clip is not None:
            self._bytes -= clip.size

    def _evict(self, keep: str) -> None:
        if self._bytes <= self.max_bytes:
            return
        now = self._clock()
        hard = self._bytes > self.max_bytes * HARD_LIMIT_FACTOR
        for clip_id in sorted(self._used, key=self._used.__getitem__):
            if self._bytes <= self.max_bytes:
                return
            if clip_id == keep or (not hard and now - self._used[clip_id] < self.keep_recent_s):
                continue
            clip = self._index[clip_id]
            try:
                clip.path.unlink(missing_ok=True)
            except OSError:  # Windows: a reader holds the file; try again on the next write
                continue
            self._forget(clip_id)
            self.evicted += 1

    def stats(self) -> dict[str, Any]:
        return {
            "clips": len(self._index),
            "bytes": self._bytes,
            "max_bytes": self.max_bytes,
            "evicted": self.evicted,
        }
