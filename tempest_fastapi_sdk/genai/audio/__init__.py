"""Self-hosted audio — speech-to-text and text-to-speech on your hardware.

`SpeechToText` (faster-whisper) interprets audio into text; `TextToSpeech`
(Coqui TTS) generates audio from text. Both load lazily and run inference
in worker threads. The engines live behind the ``[genai-audio]`` extra and
import lazily, so this package imports without it.
"""

from tempest_fastapi_sdk.genai.audio.conversation import (
    ConversationTranscriber as ConversationTranscriber,
)
from tempest_fastapi_sdk.genai.audio.conversation import align_turns as align_turns
from tempest_fastapi_sdk.genai.audio.diarization import (
    DEFAULT_CLUSTERING_THRESHOLD as DEFAULT_CLUSTERING_THRESHOLD,
)
from tempest_fastapi_sdk.genai.audio.diarization import (
    DIARIZATION_SAMPLE_RATE as DIARIZATION_SAMPLE_RATE,
)
from tempest_fastapi_sdk.genai.audio.diarization import (
    EMBEDDING_MODEL as EMBEDDING_MODEL,
)
from tempest_fastapi_sdk.genai.audio.diarization import (
    SEGMENTATION_MODEL as SEGMENTATION_MODEL,
)
from tempest_fastapi_sdk.genai.audio.diarization import (
    DiarizationModel as DiarizationModel,
)
from tempest_fastapi_sdk.genai.audio.diarization import (
    SpeakerDiarizer as SpeakerDiarizer,
)
from tempest_fastapi_sdk.genai.audio.diarization import (
    default_cache_dir as default_cache_dir,
)
from tempest_fastapi_sdk.genai.audio.diarization import ensure_models as ensure_models
from tempest_fastapi_sdk.genai.audio.language import Language as Language
from tempest_fastapi_sdk.genai.audio.language import LanguagePreset as LanguagePreset
from tempest_fastapi_sdk.genai.audio.language import preset_for as preset_for
from tempest_fastapi_sdk.genai.audio.schemas import (
    DiarizedTranscription as DiarizedTranscription,
)
from tempest_fastapi_sdk.genai.audio.schemas import (
    SpeakerTurn as SpeakerTurn,
)
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
    "DEFAULT_CLUSTERING_THRESHOLD",
    "DIARIZATION_SAMPLE_RATE",
    "EMBEDDING_MODEL",
    "SEGMENTATION_MODEL",
    "ConversationTranscriber",
    "DiarizationModel",
    "DiarizedTranscription",
    "Language",
    "LanguagePreset",
    "SpeakerDiarizer",
    "SpeakerTurn",
    "SpeechToText",
    "TextToSpeech",
    "Transcription",
    "TranscriptionSegment",
    "align_turns",
    "default_cache_dir",
    "ensure_models",
    "preset_for",
    "resolve_audio_device",
]
