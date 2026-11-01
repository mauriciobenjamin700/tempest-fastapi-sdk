"""Self-hosted audio — speech-to-text and text-to-speech on your hardware.

`SpeechToText` (faster-whisper) interprets audio into text; `TextToSpeech`
(Coqui TTS) generates audio from text. Both load lazily and run inference
in worker threads. The engines live behind the ``[genai-audio]`` extra and
import lazily, so this package imports without it.
"""

from tempest_fastapi_sdk.genai.audio.backends import (
    SpeechSynthesizer as SpeechSynthesizer,
)
from tempest_fastapi_sdk.genai.audio.backends import (
    SpeechTranscriber as SpeechTranscriber,
)
from tempest_fastapi_sdk.genai.audio.bark import (
    DEFAULT_BARK_MODEL as DEFAULT_BARK_MODEL,
)
from tempest_fastapi_sdk.genai.audio.bark import BarkSpeech as BarkSpeech
from tempest_fastapi_sdk.genai.audio.bark import split_for_bark as split_for_bark
from tempest_fastapi_sdk.genai.audio.conversation import (
    ConversationTranscriber as ConversationTranscriber,
)
from tempest_fastapi_sdk.genai.audio.conversation import align_turns as align_turns
from tempest_fastapi_sdk.genai.audio.decoding import (
    DEFAULT_MAX_AUDIO_SECONDS as DEFAULT_MAX_AUDIO_SECONDS,
)
from tempest_fastapi_sdk.genai.audio.decoding import MAX_CHANNELS as MAX_CHANNELS
from tempest_fastapi_sdk.genai.audio.decoding import (
    MAX_SOURCE_RATE as MAX_SOURCE_RATE,
)
from tempest_fastapi_sdk.genai.audio.decoding import (
    MIN_SOURCE_RATE as MIN_SOURCE_RATE,
)
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
from tempest_fastapi_sdk.genai.audio.language import LanguageEnum as LanguageEnum
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
from tempest_fastapi_sdk.genai.audio.speaker_count import (
    DEFAULT_AFFINITY_PERCENTILE as DEFAULT_AFFINITY_PERCENTILE,
)
from tempest_fastapi_sdk.genai.audio.speaker_count import (
    DEFAULT_MAX_SPEAKERS as DEFAULT_MAX_SPEAKERS,
)
from tempest_fastapi_sdk.genai.audio.speaker_count import (
    affinity_report as affinity_report,
)
from tempest_fastapi_sdk.genai.audio.speaker_count import (
    estimate_speaker_count as estimate_speaker_count,
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
    "DEFAULT_AFFINITY_PERCENTILE",
    "DEFAULT_BARK_MODEL",
    "DEFAULT_CLUSTERING_THRESHOLD",
    "DEFAULT_MATCH_THRESHOLD",
    "DEFAULT_MAX_AUDIO_SECONDS",
    "DEFAULT_MAX_SPEAKERS",
    "DEFAULT_MAX_UPLOAD_BYTES",
    "DEFAULT_MODEL_NAME",
    "DIARIZATION_SAMPLE_RATE",
    "EMBEDDING_DIMENSIONS",
    "EMBEDDING_MODEL",
    "MAX_CHANNELS",
    "MAX_SOURCE_RATE",
    "MIN_ENROLLMENT_SECONDS",
    "MIN_SOURCE_RATE",
    "SEGMENTATION_MODEL",
    "BarkSpeech",
    "ConsentRequired",
    "ConversationTranscriber",
    "DiarizationModel",
    "DiarizedTranscription",
    "EnrollmentResponseSchema",
    "ErasureResponseSchema",
    "LanguageEnum",
    "LanguagePreset",
    "SpeakerDiarizer",
    "SpeakerTurn",
    "SpeechSynthesizer",
    "SpeechToText",
    "SpeechTranscriber",
    "TextToSpeech",
    "Transcription",
    "TranscriptionSegment",
    "VoiceEmbedder",
    "VoiceMatch",
    "VoiceProfileSchema",
    "VoiceProfileService",
    "affinity_report",
    "align_turns",
    "cosine_similarity",
    "default_cache_dir",
    "ensure_models",
    "estimate_speaker_count",
    "make_voice_router",
    "pack_embedding",
    "preset_for",
    "resolve_audio_device",
    "split_for_bark",
    "unpack_embedding",
]
