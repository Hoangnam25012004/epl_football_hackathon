"""SSML of a spoken cue, and the name of its clip (docs/day5-design.md sections 6.5 and V6).

The SSML is built from the segments only: their words are escaped and every attribute comes from
the preset table, never from a text. `check_ssml` parses the result back and allows four elements
(speak, voice, prosody, break). The clip id hashes the provider, the voice, the output format and
the SSML: the same words in the same voice are one file, shared by every listener.
"""

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from xml.sax.saxutils import escape

from pundit.agents.contracts import Lang, SpeechSegment
from pundit.agents.prosody import PRESETS
from pundit.agents.voices import VoiceSpec

SSML_NAMESPACE = "http://www.w3.org/2001/10/synthesis"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
MAX_SSML_BYTES = 10_000
MAX_BREAK_MS = 1000
CHARS_PER_SECOND: dict[Lang, float] = {"vi": 15.0, "en": 14.5}  # to be calibrated (tier B)
CLIP_VERSION = "v1"
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PERCENT = re.compile(r"^[+-]\d{1,2}%$")
_BREAK = re.compile(r"^(\d{1,4})ms$")
_OUTER_TAGS = re.compile(r"</?speak[^>]*>|</?voice[^>]*>")
ALLOWED_ATTRIBUTES: dict[str, frozenset[str]] = {
    "prosody": frozenset({"rate", "pitch", "volume"}),
    "break": frozenset({"time"}),
}


def _percent(value: int) -> str:
    return f"{value:+d}%"


def build_ssml(segments: Sequence[SpeechSegment], voice: VoiceSpec) -> str:
    parts = [
        f'<speak version="1.0" xmlns="{SSML_NAMESPACE}" xml:lang="{voice.lang}">',
        f'<voice name="{voice.name}">',
    ]
    for segment in segments:
        if segment.pause_before_ms:
            parts.append(f'<break time="{segment.pause_before_ms}ms"/>')
        prosody = PRESETS[segment.preset]
        attributes = " ".join(
            f'{name}="{_percent(value)}"'
            for name, value in (
                ("rate", prosody.rate_pct),
                ("pitch", prosody.pitch_pct),
                ("volume", prosody.volume_pct),
            )
            if value
        )
        text = escape(_CONTROL.sub("", segment.text))
        parts.append(f"<prosody {attributes}>{text}</prosody>" if attributes else text)
    parts.append("</voice></speak>")
    return "".join(parts)


def check_ssml(ssml: str, voice: VoiceSpec) -> list[str]:
    """Problems of an SSML document (empty: valid): parse, elements, attributes, size."""
    problems: list[str] = []
    if len(ssml.encode("utf-8")) > MAX_SSML_BYTES:
        problems.append(f"SSML is larger than {MAX_SSML_BYTES} bytes")
    if "<!" in ssml or "<?" in ssml:
        return [*problems, "SSML has a declaration (DOCTYPE, ENTITY or processing instruction)"]
    try:
        root = ET.fromstring(ssml)
    except ET.ParseError as exc:
        return [*problems, f"SSML does not parse: {exc}"]
    ns = f"{{{SSML_NAMESPACE}}}"
    if root.tag != f"{ns}speak":
        problems.append(f"root is {root.tag}, not speak")
    if root.get(XML_LANG) != voice.lang:
        problems.append(f"xml:lang is {root.get(XML_LANG)!r}, the voice speaks {voice.lang}")
    voices = list(root)
    if len(voices) != 1 or voices[0].tag != f"{ns}voice":
        return [*problems, "speak must hold exactly one voice"]
    if voices[0].get("name") != voice.name:
        problems.append(f"voice is {voices[0].get('name')!r}, expected {voice.name}")
    if (root.text or "").strip() or (voices[0].tail or "").strip():
        problems.append("text outside the voice element")
    for element in voices[0]:
        tag = element.tag.removeprefix(ns)
        if tag not in ALLOWED_ATTRIBUTES:
            problems.append(f"element {tag!r} is not allowed")
            continue
        extra = set(element.attrib) - ALLOWED_ATTRIBUTES[tag]
        if extra:
            problems.append(f"{tag} has attributes {sorted(extra)}")
        if len(element):
            problems.append(f"{tag} has child elements")
        if tag == "break":
            match = _BREAK.match(element.get("time", ""))
            if match is None or int(match.group(1)) > MAX_BREAK_MS:
                problems.append(f"bad break time {element.get('time')!r}")
            if (element.text or "").strip():
                problems.append("break holds text")
        else:
            for name in ("rate", "pitch", "volume"):
                value = element.get(name)
                if value is not None and not _PERCENT.match(value):
                    problems.append(f"bad prosody {name} {value!r}")
    return problems


def billable_chars(ssml: str) -> int:
    """Characters Azure bills: everything but the speak and voice tags (markup included)."""
    return len(_OUTER_TAGS.sub("", ssml))


def clip_id(provider: str, voice: str, output_format: str, ssml: str) -> str:
    key = f"{CLIP_VERSION}|{provider}|{voice}|{output_format}|{ssml}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def estimate_ms(segments: Sequence[SpeechSegment], language: Lang) -> int:
    """How long the segments take to read, before any audio exists."""
    speed = CHARS_PER_SECOND[language]
    total = 0.0
    for segment in segments:
        total += segment.pause_before_ms + len(segment.text) / (speed * segment.rate) * 1000
    return round(total)
