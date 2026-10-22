"""Audio bytes: the mock's WAV, checks of what a synthesizer returns, durations, and the WAV a
browser records for speech to text (docs/day5-design.md sections 8.2, 8.3 and 11)."""

import math
import struct
from array import array
from dataclasses import dataclass

from pundit.agents.contracts import AudioMediaType

WAV_RATE = 16_000
TONE_MS = 80
TONE_HZ = 440
TONE_AMPLITUDE = 0.08
MP3_KBPS = 48  # audio-24khz-48kbitrate-mono-mp3
MIN_MP3_BYTES = 1024
EXTENSIONS: dict[AudioMediaType, str] = {"audio/mpeg": "mp3", "audio/wav": "wav"}
MEDIA_TYPES: dict[str, AudioMediaType] = {"mp3": "audio/mpeg", "wav": "audio/wav"}


def _tone() -> bytes:
    samples = array("h")
    count = WAV_RATE * TONE_MS // 1000
    for index in range(count):
        fade = min(1.0, index / 160, (count - index) / 160)  # no click at either end
        value = math.sin(2 * math.pi * TONE_HZ * index / WAV_RATE) * TONE_AMPLITUDE * fade
        samples.append(round(value * 32767))
    return samples.tobytes()


_TONE = _tone()


def wav_header(data_bytes: int, *, rate: int = WAV_RATE, channels: int = 1) -> bytes:
    return b"".join(
        (
            b"RIFF",
            struct.pack("<I", 36 + data_bytes),
            b"WAVE",
            b"fmt ",
            struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * channels * 2, channels * 2, 16),
            b"data",
            struct.pack("<I", data_bytes),
        )
    )


def mock_wav(duration_ms: int, tone_starts_ms: tuple[int, ...] = (0,)) -> bytes:
    """A deterministic WAV: silence with a short soft tone where each segment starts."""
    samples = max(1, WAV_RATE * duration_ms // 1000)
    pcm = bytearray(samples * 2)
    for start in tone_starts_ms:
        at = WAV_RATE * start // 1000 * 2
        if at >= len(pcm):
            continue
        chunk = _TONE[: len(pcm) - at]
        pcm[at : at + len(chunk)] = chunk
    return wav_header(len(pcm)) + bytes(pcm)


@dataclass(frozen=True)
class WavInfo:
    channels: int
    sample_rate: int
    bits: int
    data_bytes: int  # PCM bytes present
    data_offset: int  # where the PCM starts in the file

    @property
    def duration_ms(self) -> int:
        frame = self.channels * self.bits // 8
        return round(self.data_bytes / frame / self.sample_rate * 1000) if frame else 0


def parse_wav(data: bytes) -> WavInfo:
    """The format of a PCM WAV file; ValueError when it is not one."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    at, fmt, info = 12, None, None
    while at + 8 <= len(data):
        chunk, size = data[at : at + 4], struct.unpack("<I", data[at + 4 : at + 8])[0]
        body = data[at + 8 : at + 8 + size]
        if chunk == b"fmt " and len(body) >= 16:
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif chunk == b"data" and fmt is not None:
            if fmt[0] != 1:
                raise ValueError("not PCM")
            info = WavInfo(
                channels=fmt[1], sample_rate=fmt[2], bits=fmt[5], data_bytes=len(body),
                data_offset=at + 8,
            )  # fmt: skip
            break
        at += 8 + size + (size % 2)
    if info is None:
        raise ValueError("no fmt / data chunk")
    return info


def is_mp3(data: bytes) -> bool:
    if data[:3] == b"ID3":
        return True
    return len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def _id3_size(data: bytes) -> int:
    if data[:3] != b"ID3" or len(data) < 10:
        return 0
    size = 0
    for byte in data[6:10]:  # syncsafe integer
        size = (size << 7) | (byte & 0x7F)
    return size + 10


def mp3_duration_ms(data: bytes, kbps: int = MP3_KBPS) -> int:
    """Constant bit rate: bytes x 8 / kbps (good to a few tens of milliseconds)."""
    return mp3_duration_of_size(len(data), data[:10], kbps)


def mp3_duration_of_size(size: int, head: bytes, kbps: int = MP3_KBPS) -> int:
    """The same from a file's size and its first ten bytes (the ID3 header)."""
    return max(0, round((size - _id3_size(head)) * 8 / kbps))


def wav_duration_of_size(size: int, head: bytes) -> int:
    """A PCM WAV's duration from its size and header (the first few kilobytes)."""
    info = parse_wav(head)
    frame = info.channels * info.bits // 8
    if not frame or not info.sample_rate:
        raise ValueError("bad WAV format")
    return round((size - info.data_offset) / frame / info.sample_rate * 1000)


def check_audio(data: bytes, media_type: AudioMediaType) -> int:
    """The duration of valid audio; ValueError when the bytes are not what they claim."""
    if media_type == "audio/mpeg":
        if len(data) < MIN_MP3_BYTES or not is_mp3(data):
            raise ValueError("not an MP3 file")
        return mp3_duration_ms(data)
    info = parse_wav(data)
    if info.data_bytes == 0:
        raise ValueError("empty WAV")
    return info.duration_ms
