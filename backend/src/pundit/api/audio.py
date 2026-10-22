"""Voice endpoints (docs/day5-design.md sections 3.5, 9.2, 9.3 and 11).

`GET /audio/{clip_id}.{mp3|wav}` serves clips the Narrator made from verified content: it never
synthesizes a text it is given, so the public demo is not a free text-to-speech service. A clip
being made is awaited (TTS_GET_WAIT_S); a failed one answers 503 with its reason, and the fan
player reads the same words with the browser's voice instead. Clips are named by their content:
cached for ever, ranges for seeking a recap.
"""

import re
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.responses import Response

from pundit.agents.contracts import Lang, SpeechCue, SpeechVoice, VoiceGender
from pundit.agents.voices import voice_list
from pundit.speech.clips import ClipFile
from pundit.speech.durable import IMMUTABLE
from pundit.speech.narrator import ClipFailure, Narrator

router = APIRouter()
_CLIP_NAME = re.compile(r"^([0-9a-f]{32})\.(mp3|wav)$")
NO_STORE = {"Cache-Control": "no-store"}


def narrator_of(request: Request) -> Narrator:
    narrator: Narrator | None = request.app.state.narrator
    if narrator is None:
        raise HTTPException(status_code=404, detail="the voice is off (TTS_PROVIDER=off)")
    return narrator


@router.api_route("/audio/{name}", methods=["GET", "HEAD"], response_model=None)
async def get_audio(name: str, request: Request) -> Response:
    narrator = narrator_of(request)
    match = _CLIP_NAME.match(name)
    if match is None:
        raise HTTPException(status_code=422, detail="a clip is named <32 hex digits>.mp3|wav")
    clip_id, extension = match.groups()
    found = await narrator.wait(clip_id, extension, narrator.settings.tts_get_wait_s)
    if isinstance(found, ClipFile) and found.extension == extension:
        return FileResponse(
            found.path,
            media_type=found.media_type,
            headers={"Cache-Control": IMMUTABLE, "X-Content-Type-Options": "nosniff"},
        )
    if isinstance(found, ClipFailure):
        return JSONResponse(
            status_code=503,
            content={"detail": "audio_unavailable", "reason": found.reason},
            headers={"Retry-After": "2", **NO_STORE},
        )
    return JSONResponse(status_code=404, content={"detail": "unknown clip"}, headers=NO_STORE)


@router.get("/speech/voices", response_model=list[SpeechVoice])
async def get_voices(request: Request) -> list[SpeechVoice]:
    narrator = narrator_of(request)
    return voice_list(narrator.settings.tts_languages, narrator.server_audio)


@router.get("/speech/greeting", response_model=SpeechCue)
async def get_greeting(
    request: Request,
    lang: Annotated[Lang, Query(description="language of the voice")] = "vi",
    voice: Annotated[VoiceGender, Query(description="female or male")] = "female",
) -> SpeechCue:
    """The greeting played when a fan turns the voice on: an AI voice, synthetic data."""
    narrator = narrator_of(request)
    cue = narrator.greeting_cue(lang, voice)
    if cue is None:
        raise HTTPException(status_code=404, detail=f"no voice for {lang!r}")
    return cue
