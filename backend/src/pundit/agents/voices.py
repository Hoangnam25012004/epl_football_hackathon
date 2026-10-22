"""The voices of the fan player (docs/day5-design.md sections 3.1 and 5.7).

A voice is chosen by the viewer's language and gender. Vietnamese is read today; English has its
voices here but no spoken pack yet (numbers to words, abbreviations), so TTS_LANGUAGES only
accepts the languages of SPOKEN_LANGUAGES.
"""

from dataclasses import dataclass

from pundit.agents.contracts import Lang, SpeechLang, SpeechVoice, VoiceGender


@dataclass(frozen=True)
class VoiceSpec:
    name: str  # Azure short name
    lang: SpeechLang  # BCP-47 of the voice (SSML xml:lang)
    language: Lang
    gender: VoiceGender
    display_name: str


VOICES: dict[tuple[Lang, VoiceGender], VoiceSpec] = {
    ("vi", "female"): VoiceSpec("vi-VN-HoaiMyNeural", "vi-VN", "vi", "female", "Hoài My"),
    ("vi", "male"): VoiceSpec("vi-VN-NamMinhNeural", "vi-VN", "vi", "male", "Nam Minh"),
    ("en", "female"): VoiceSpec("en-GB-SoniaNeural", "en-GB", "en", "female", "Sonia"),
    ("en", "male"): VoiceSpec("en-GB-RyanNeural", "en-GB", "en", "male", "Ryan"),
}
# languages with a spoken pack (agents/spoken.py); English is P2
SPOKEN_LANGUAGES: tuple[Lang, ...] = ("vi",)
GENDERS: tuple[VoiceGender, ...] = ("female", "male")


def voice_for(language: Lang, gender: VoiceGender) -> VoiceSpec:
    return VOICES[(language, gender)]


def voice_list(languages: tuple[Lang, ...], server_audio: bool) -> list[SpeechVoice]:
    """The voices of the enabled languages, female first."""
    return [
        SpeechVoice(
            lang=spec.lang,
            language=spec.language,
            gender=spec.gender,
            voice=spec.name,
            display_name=spec.display_name,
            server_audio=server_audio,
        )
        for language in languages
        for gender in GENDERS
        for spec in (voice_for(language, gender),)
    ]
