"""Self-hosted audio — speech-to-text and text-to-speech on your hardware.

`SpeechToText` (faster-whisper) interprets audio into text; `TextToSpeech`
(Coqui TTS) generates audio from text. Both load lazily and run inference
in worker threads. The engines live behind the ``[genai-audio]`` extra and
import lazily, so this package imports without it.
"""

from tempest_fastapi_sdk.genai.audio.language import Language as Language
from tempest_fastapi_sdk.genai.audio.language import LanguagePreset as LanguagePreset
from tempest_fastapi_sdk.genai.audio.language import preset_for as preset_for
from tempest_fastapi_sdk.genai.audio.schemas import (
    Transcription as Transcription,
)
from tempest_fastapi_sdk.genai.audio.schemas import (
    TranscriptionSegment as TranscriptionSegment,
)
from tempest_fastapi_sdk.genai.audio.stt import SpeechToText as SpeechToText
from tempest_fastapi_sdk.genai.audio.stt import (
    resolve_audio_device as resolve_audio_device,
)
from tempest_fastapi_sdk.genai.audio.tts import TextToSpeech as TextToSpeech

__all__: list[str] = [
    "Language",
    "LanguagePreset",
    "SpeechToText",
    "TextToSpeech",
    "Transcription",
    "TranscriptionSegment",
    "preset_for",
    "resolve_audio_device",
]
