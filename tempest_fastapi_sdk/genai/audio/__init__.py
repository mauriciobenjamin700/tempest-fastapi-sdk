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
from tempest_fastapi_sdk.genai.audio.profiles import (
    DEFAULT_MODEL_NAME as DEFAULT_MODEL_NAME,
)
from tempest_fastapi_sdk.genai.audio.profiles import ConsentRequired as ConsentRequired
from tempest_fastapi_sdk.genai.audio.profiles import VoiceMatch as VoiceMatch
from tempest_fastapi_sdk.genai.audio.profiles import (
    VoiceProfileService as VoiceProfileService,
)
from tempest_fastapi_sdk.genai.audio.profiles import pack_embedding as pack_embedding
from tempest_fastapi_sdk.genai.audio.profiles import (
    unpack_embedding as unpack_embedding,
)
from tempest_fastapi_sdk.genai.audio.router import (
    DEFAULT_MAX_UPLOAD_BYTES as DEFAULT_MAX_UPLOAD_BYTES,
)
from tempest_fastapi_sdk.genai.audio.router import (
    EnrollmentResponseSchema as EnrollmentResponseSchema,
)
from tempest_fastapi_sdk.genai.audio.router import (
    ErasureResponseSchema as ErasureResponseSchema,
)
from tempest_fastapi_sdk.genai.audio.router import (
    VoiceProfileSchema as VoiceProfileSchema,
)
from tempest_fastapi_sdk.genai.audio.router import (
    make_voice_router as make_voice_router,
)
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
from tempest_fastapi_sdk.genai.audio.voiceprint import (
    DEFAULT_MATCH_THRESHOLD as DEFAULT_MATCH_THRESHOLD,
)
from tempest_fastapi_sdk.genai.audio.voiceprint import (
    EMBEDDING_DIMENSIONS as EMBEDDING_DIMENSIONS,
)
from tempest_fastapi_sdk.genai.audio.voiceprint import (
    MIN_ENROLLMENT_SECONDS as MIN_ENROLLMENT_SECONDS,
)
from tempest_fastapi_sdk.genai.audio.voiceprint import VoiceEmbedder as VoiceEmbedder
from tempest_fastapi_sdk.genai.audio.voiceprint import (
    cosine_similarity as cosine_similarity,
)

__all__: list[str] = [
    "DEFAULT_CLUSTERING_THRESHOLD",
    "DEFAULT_MATCH_THRESHOLD",
    "DEFAULT_MAX_UPLOAD_BYTES",
    "DEFAULT_MODEL_NAME",
    "DIARIZATION_SAMPLE_RATE",
    "EMBEDDING_DIMENSIONS",
    "EMBEDDING_MODEL",
    "MIN_ENROLLMENT_SECONDS",
    "SEGMENTATION_MODEL",
    "ConsentRequired",
    "ConversationTranscriber",
    "DiarizationModel",
    "DiarizedTranscription",
    "EnrollmentResponseSchema",
    "ErasureResponseSchema",
    "Language",
    "LanguagePreset",
    "SpeakerDiarizer",
    "SpeakerTurn",
    "SpeechToText",
    "TextToSpeech",
    "Transcription",
    "TranscriptionSegment",
    "VoiceEmbedder",
    "VoiceMatch",
    "VoiceProfileSchema",
    "VoiceProfileService",
    "align_turns",
    "cosine_similarity",
    "default_cache_dir",
    "ensure_models",
    "make_voice_router",
    "pack_embedding",
    "preset_for",
    "resolve_audio_device",
    "unpack_embedding",
]
