"""Opt-in FastAPI router exposing the self-hosted GenAI objects.

:func:`make_genai_router` wires the GenAI building blocks
(:class:`~tempest_fastapi_sdk.genai.TextGenerator`,
:class:`~tempest_fastapi_sdk.genai.Embedder`,
:class:`~tempest_fastapi_sdk.genai.rag.Retriever`, and the audio
:class:`~tempest_fastapi_sdk.genai.audio.SpeechToText` /
:class:`~tempest_fastapi_sdk.genai.audio.TextToSpeech`) straight onto HTTP
endpoints — the same "hand it the pieces, get a working router" shape as
:func:`tempest_fastapi_sdk.make_auth_router`.

You inject only the objects you have loaded; the router mounts **only**
the matching endpoints (pass an ``Embedder`` and you get ``/embed`` but
not ``/generate``). Heavy imports (``torch`` etc.) never happen here —
the objects are constructed by the caller and imported lazily on first
use, so importing this module costs nothing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from fastapi import APIRouter, Response, UploadFile, status

from tempest_fastapi_sdk.genai.schemas import GenerationConfig
from tempest_fastapi_sdk.schemas.base import BaseSchema
from tempest_fastapi_sdk.sse import ServerSentEvent, sse_response

if TYPE_CHECKING:
    from starlette.responses import StreamingResponse

    from tempest_fastapi_sdk.genai.audio import SpeechToText, TextToSpeech
    from tempest_fastapi_sdk.genai.embeddings import Embedder
    from tempest_fastapi_sdk.genai.rag import Retriever
    from tempest_fastapi_sdk.genai.text import TextGenerator


class ChatMessageSchema(BaseSchema):
    """One chat turn (``role`` + ``content``).

    Attributes:
        role (str): The speaker role — ``"system"`` / ``"user"`` /
            ``"assistant"``.
        content (str): The message text.
    """

    role: str
    content: str


class GenerateRequestSchema(BaseSchema):
    """Request body for ``POST /generate`` and ``/generate/stream``.

    Attributes:
        prompt (str): The input prompt.
        config (GenerationConfig | None): Optional typed generation
            parameters.
    """

    prompt: str
    config: GenerationConfig | None = None


class GenerateResponseSchema(BaseSchema):
    """Response body for ``POST /generate``.

    Attributes:
        text (str): The generated completion.
    """

    text: str


class ChatRequestSchema(BaseSchema):
    """Request body for ``POST /chat``.

    Attributes:
        messages (list[ChatMessageSchema]): The conversation so far.
        config (GenerationConfig | None): Optional generation parameters.
    """

    messages: list[ChatMessageSchema]
    config: GenerationConfig | None = None


class ChatResponseSchema(BaseSchema):
    """Response body for ``POST /chat``.

    Attributes:
        reply (str): The assistant's reply.
    """

    reply: str


class EmbedRequestSchema(BaseSchema):
    """Request body for ``POST /embed``.

    Attributes:
        texts (list[str]): The texts to embed.
    """

    texts: list[str]


class EmbedResponseSchema(BaseSchema):
    """Response body for ``POST /embed``.

    Attributes:
        vectors (list[list[float]]): One vector per input text, in order.
        dimensions (int): The embedding dimensionality (``0`` when empty).
    """

    vectors: list[list[float]]
    dimensions: int


class RagRequestSchema(BaseSchema):
    """Request body for ``POST /rag``.

    Attributes:
        query (str): The natural-language query.
        top_k (int): How many chunks to include in the context.
    """

    query: str
    top_k: int = 5


class RagResponseSchema(BaseSchema):
    """Response body for ``POST /rag``.

    Attributes:
        context (str): The prompt-ready context block.
    """

    context: str


class TTSRequestSchema(BaseSchema):
    """Request body for ``POST /tts``.

    Attributes:
        text (str): The text to synthesize.
        language (str | None): Language code / preset, or ``None``.
        speaker (str | None): Speaker name for multi-speaker models.
    """

    text: str
    language: str | None = None
    speaker: str | None = None


def make_genai_router(
    *,
    text_generator: TextGenerator | None = None,
    embedder: Embedder | None = None,
    retriever: Retriever | None = None,
    speech_to_text: SpeechToText | None = None,
    text_to_speech: TextToSpeech | None = None,
    prefix: str = "/api/genai",
    tags: list[str] | None = None,
) -> APIRouter:
    """Build a router exposing whichever GenAI objects you inject.

    Only the endpoints backed by a provided object are registered:

    * ``text_generator`` -> ``POST {prefix}/generate`` (JSON reply) and
      ``POST {prefix}/generate/stream`` (token-by-token SSE), plus
      ``POST {prefix}/chat``.
    * ``embedder`` -> ``POST {prefix}/embed``.
    * ``retriever`` -> ``POST {prefix}/rag`` (query -> context block).
    * ``speech_to_text`` -> ``POST {prefix}/transcribe`` (audio upload).
    * ``text_to_speech`` -> ``POST {prefix}/tts`` (returns ``audio/wav``).

    The router owns only the HTTP surface; the caller owns model
    lifecycle (loading, idle-unloading, auth). Add your own auth by
    including the router under an authenticated parent or wrapping it.

    Args:
        text_generator (TextGenerator | None): Backs the text endpoints.
        embedder (Embedder | None): Backs ``/embed``.
        retriever (Retriever | None): Backs ``/rag``.
        speech_to_text (SpeechToText | None): Backs ``/transcribe``.
        text_to_speech (TextToSpeech | None): Backs ``/tts``.
        prefix (str): URL prefix. Defaults to ``"/api/genai"``.
        tags (list[str] | None): OpenAPI tags. Defaults to ``["genai"]``.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.

    Raises:
        ValueError: When no GenAI object is provided (the router would be
            empty).
    """
    if not any(
        (text_generator, embedder, retriever, speech_to_text, text_to_speech),
    ):
        raise ValueError(
            "make_genai_router needs at least one GenAI object "
            "(text_generator / embedder / retriever / speech_to_text / "
            "text_to_speech).",
        )

    router = APIRouter(prefix=prefix, tags=list(tags or ["genai"]))

    if text_generator is not None:
        generator = text_generator

        @router.post("/generate", response_model=GenerateResponseSchema)
        async def generate(body: GenerateRequestSchema) -> GenerateResponseSchema:
            """Generate a completion for the prompt.

            Args:
                body (GenerateRequestSchema): Prompt + optional config.

            Returns:
                GenerateResponseSchema: The generated text.
            """
            text = await generator.generate(body.prompt, config=body.config)
            return GenerateResponseSchema(text=text)

        @router.post("/generate/stream")
        async def generate_stream(body: GenerateRequestSchema) -> StreamingResponse:
            """Stream a completion token by token over SSE.

            Args:
                body (GenerateRequestSchema): Prompt + optional config.

            Returns:
                StreamingResponse: A ``text/event-stream`` of token events,
                ending with a ``done`` event.
            """

            async def _events() -> AsyncIterator[bytes]:
                async for piece in generator.stream(body.prompt, config=body.config):
                    yield ServerSentEvent(data=piece).encode().encode("utf-8")
                yield ServerSentEvent(data="", event="done").encode().encode("utf-8")

            return sse_response(_events())

        @router.post("/chat", response_model=ChatResponseSchema)
        async def chat(body: ChatRequestSchema) -> ChatResponseSchema:
            """Generate a reply for a chat message list.

            Args:
                body (ChatRequestSchema): Messages + optional config.

            Returns:
                ChatResponseSchema: The assistant reply.
            """
            messages = [{"role": m.role, "content": m.content} for m in body.messages]
            reply = await generator.chat(messages, config=body.config)
            return ChatResponseSchema(reply=reply)

    if embedder is not None:
        embed_model = embedder

        @router.post("/embed", response_model=EmbedResponseSchema)
        async def embed(body: EmbedRequestSchema) -> EmbedResponseSchema:
            """Embed one or many texts into vectors.

            Args:
                body (EmbedRequestSchema): The texts to embed.

            Returns:
                EmbedResponseSchema: The vectors and their dimensionality.
            """
            vectors = await embed_model.embed(body.texts)
            dimensions = len(vectors[0]) if vectors else 0
            return EmbedResponseSchema(vectors=vectors, dimensions=dimensions)

    if retriever is not None:
        rag = retriever

        @router.post("/rag", response_model=RagResponseSchema)
        async def rag_context(body: RagRequestSchema) -> RagResponseSchema:
            """Search the corpus and return a prompt-ready context block.

            Args:
                body (RagRequestSchema): Query + ``top_k``.

            Returns:
                RagResponseSchema: The assembled context.
            """
            context = await rag.retrieve(body.query, top_k=body.top_k)
            return RagResponseSchema(context=context)

    if speech_to_text is not None:
        stt = speech_to_text

        @router.post("/transcribe")
        async def transcribe(
            file: UploadFile,
            language: str | None = None,
        ) -> object:
            """Transcribe an uploaded audio file.

            Args:
                file (UploadFile): The audio file to transcribe.
                language (str | None): Force a language, or auto-detect.

            Returns:
                object: The :class:`Transcription` (text, language,
                duration, segments).
            """
            audio = await file.read()
            return await stt.transcribe(audio, language=language)

    if text_to_speech is not None:
        tts = text_to_speech

        @router.post(
            "/tts",
            status_code=status.HTTP_200_OK,
            response_class=Response,
        )
        async def synthesize(body: TTSRequestSchema) -> Response:
            """Synthesize speech and return the WAV bytes.

            Args:
                body (TTSRequestSchema): Text + optional language/speaker.

            Returns:
                Response: The ``audio/wav`` payload.
            """
            wav = await tts.synthesize(
                body.text,
                language=body.language,
                speaker=body.speaker,
            )
            return Response(content=wav, media_type="audio/wav")

    return router


__all__: list[str] = [
    "ChatMessageSchema",
    "ChatRequestSchema",
    "ChatResponseSchema",
    "EmbedRequestSchema",
    "EmbedResponseSchema",
    "GenerateRequestSchema",
    "GenerateResponseSchema",
    "RagRequestSchema",
    "RagResponseSchema",
    "TTSRequestSchema",
    "make_genai_router",
]
