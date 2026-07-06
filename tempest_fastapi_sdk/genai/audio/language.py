"""Language presets for audio — pick ``pt-BR`` / ``en-US``, not model codes.

STT and TTS want different identifiers per language (Whisper wants ``"pt"``,
Coqui wants a model id). A :class:`Language` member hides that: choose
``Language.PT_BR`` and the SDK resolves the Whisper code and a sensible
default TTS model for you. Dependency-free — imports without the
``[genai-audio]`` extra.
"""

from __future__ import annotations

from tempest_fastapi_sdk.core import BaseStrEnum
from tempest_fastapi_sdk.schemas.base import BaseSchema


class Language(BaseStrEnum):
    """A supported audio language.

    * ``PT_BR`` — Brazilian Portuguese.
    * ``EN_US`` — US English.
    """

    PT_BR = "pt-BR"
    EN_US = "en-US"


class LanguagePreset(BaseSchema):
    """Per-language identifiers for the audio engines.

    Attributes:
        whisper_language (str): The faster-whisper language code
            (``"pt"`` / ``"en"``).
        tts_model (str): A good default Coqui TTS model for the language.
        tts_language (str | None): The Coqui ``language`` argument, or
            ``None`` for single-language models that don't take one.
    """

    whisper_language: str
    tts_model: str
    tts_language: str | None = None


_PRESETS: dict[Language, LanguagePreset] = {
    Language.PT_BR: LanguagePreset(
        whisper_language="pt",
        tts_model="tts_models/pt/cv/vits",
        tts_language=None,
    ),
    Language.EN_US: LanguagePreset(
        whisper_language="en",
        tts_model="tts_models/en/ljspeech/vits",
        tts_language=None,
    ),
}


def preset_for(language: Language) -> LanguagePreset:
    """Return the audio preset for ``language``.

    Args:
        language (Language): The target language.

    Returns:
        LanguagePreset: Whisper code + default TTS model for it.
    """
    return _PRESETS[language]


def whisper_language(language: Language | str | None) -> str | None:
    """Resolve a Whisper language code from a :class:`Language` or raw value.

    Args:
        language (Language | str | None): A :class:`Language` member, a raw
            code (``"pt"``, passed through), or ``None`` (auto-detect).

    Returns:
        str | None: The Whisper code, or ``None`` for auto-detect.
    """
    if language is None:
        return None
    if isinstance(language, Language):
        return _PRESETS[language].whisper_language
    return language


def tts_language(language: Language | str | None) -> str | None:
    """Resolve a Coqui TTS language argument from a value.

    Args:
        language (Language | str | None): A :class:`Language` member (mapped
            to its preset's ``tts_language``), a raw code (passed through),
            or ``None``.

    Returns:
        str | None: The Coqui ``language`` argument, or ``None``.
    """
    if language is None:
        return None
    if isinstance(language, Language):
        return _PRESETS[language].tts_language
    return language


__all__: list[str] = [
    "Language",
    "LanguagePreset",
    "preset_for",
    "tts_language",
    "whisper_language",
]
