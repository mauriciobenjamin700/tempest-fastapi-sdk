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

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Response, UploadFile, status
from pydantic import Field

from tempest_fastapi_sdk.exceptions.validation import ValidationException
from tempest_fastapi_sdk.genai.inventory import ModelRuntimeReport, runtime_report
from tempest_fastapi_sdk.genai.schemas import (
    GenAIRequestLimits,
    GenerationConfig,
    ImageGenerationConfig,
)
from tempest_fastapi_sdk.schemas.base import BaseSchema
from tempest_fastapi_sdk.sse import ServerSentEvent, sse_response
from tempest_fastapi_sdk.utils.upload import read_upload_capped

if TYPE_CHECKING:
    from starlette.responses import StreamingResponse

    from tempest_fastapi_sdk.genai.audio import SpeechToText, TextToSpeech
    from tempest_fastapi_sdk.genai.image import ImageGenerator
    from tempest_fastapi_sdk.genai.rag import SupportsEmbed, SupportsRetrieve
    from tempest_fastapi_sdk.genai.registry import ModelRegistry
    from tempest_fastapi_sdk.genai.text import TextBackend


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
        top_k (int): How many chunks to include in the context; at least
            ``1``. The router also caps it at
            ``GenAIRequestLimits.max_top_k``.
    """

    query: str
    top_k: int = Field(
        default=5,
        ge=1,
        title="Top-k",
        description="How many chunks to include in the context.",
    )


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


class ImageRequestSchema(BaseSchema):
    """Request body for ``POST /image``.

    Attributes:
        prompt (str): What to draw.
        config (ImageGenerationConfig | None): Size, steps, guidance and
            seed. Left unset, the model's own defaults apply.
    """

    prompt: str
    config: ImageGenerationConfig | None = None


def make_genai_router(
    *,
    text_generator: TextBackend | None = None,
    embedder: SupportsEmbed | None = None,
    retriever: SupportsRetrieve | None = None,
    speech_to_text: SpeechToText | None = None,
    text_to_speech: TextToSpeech | None = None,
    image_generator: ImageGenerator | None = None,
    models: ModelRegistry | dict[str, Any] | list[Any] | None = None,
    prefix: str = "/api/genai",
    tags: list[str] | None = None,
    limits: GenAIRequestLimits | None = None,
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
    * ``image_generator`` -> ``POST {prefix}/image`` (returns the encoded
      image, with the seed in ``X-Image-Seed``).
    * ``models`` -> ``GET {prefix}/models`` (what is resident in memory
      right now, next to the host's memory picture).

    The router owns only the HTTP surface; the caller owns model
    lifecycle (loading, idle-unloading, auth). Add your own auth by
    including the router under an authenticated parent or wrapping it.

    Every request is checked against ``limits`` before the model runs:
    prompt length, chat size, ``max_new_tokens``, ``/embed`` batch size,
    ``top_k``, ``/tts`` text length, image size and steps, and the
    ``/transcribe`` upload size (read in chunks, so an oversized upload is
    refused before it is held in memory). A request over any of them gets
    ``422``; with ``register_exception_handlers`` installed the body also
    carries ``details={"field": ..., "limit": ...}`` (or
    ``{"max_bytes": ...}`` for the upload). ``/image`` answers with one
    image, so it refuses ``config.num_images`` above ``1`` instead of
    rendering a batch and dropping all but the first.

    Args:
        text_generator (TextBackend | None): Backs the text endpoints
            (a ``TextGenerator``, an ``OllamaGenerator``, or any object
            implementing the ``TextBackend`` protocol).
        embedder (SupportsEmbed | None): Backs ``/embed`` (an ``Embedder``,
            an ``OllamaEmbedder``, or any ``SupportsEmbed``).
        retriever (SupportsRetrieve | None): Backs ``/rag`` — a ``Retriever``
            (rerank via ``Retriever(reranker=...)``) or a ``HybridRetriever``.
        speech_to_text (SpeechToText | None): Backs ``/transcribe``.
        text_to_speech (TextToSpeech | None): Backs ``/tts``.
        image_generator (ImageGenerator | None): Backs ``/image``.
        models (ModelRegistry | dict[str, Any] | list[Any] | None): Backs
            ``/models`` — a ``ModelRegistry``, or a dict/list of handles you
            hold yourself.
        prefix (str): URL prefix. Defaults to ``"/api/genai"``.
        tags (list[str] | None): OpenAPI tags. Defaults to ``["genai"]``.
        limits (GenAIRequestLimits | None): Per-request ceilings. ``None``
            uses ``GenAIRequestLimits()`` and its defaults.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.

    Raises:
        ValueError: When no GenAI object is provided (the router would be
            empty). Emptiness is tested with ``is None``, not truthiness:
            an empty ``ModelRegistry`` — the normal state at startup, before
            anything has been loaded — is falsy, and refusing it would make
            the endpoint impossible to mount at the only time you can mount
            it.
    """
    injected = (
        text_generator,
        embedder,
        retriever,
        speech_to_text,
        text_to_speech,
        image_generator,
        models,
    )
    if all(item is None for item in injected):
        raise ValueError(
            "make_genai_router needs at least one GenAI object "
            "(text_generator / embedder / retriever / speech_to_text / "
            "text_to_speech / image_generator / models).",
        )

    router = APIRouter(prefix=prefix, tags=list(tags or ["genai"]))
    bounds = limits if limits is not None else GenAIRequestLimits()

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
            _check_prompt(body.prompt, bounds)
            _check_generation(body.config, bounds)
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
            _check_prompt(body.prompt, bounds)
            _check_generation(body.config, bounds)

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
            _check_chat(body.messages, bounds)
            _check_generation(body.config, bounds)
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
            _check_at_most("texts", len(body.texts), bounds.max_embed_texts)
            for text in body.texts:
                _check_at_most("texts", len(text), bounds.max_prompt_chars)
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
            _check_at_most("query", len(body.query), bounds.max_prompt_chars)
            _check_at_most("top_k", body.top_k, bounds.max_top_k)
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
            audio = await read_upload_capped(
                file,
                max_bytes=bounds.max_upload_bytes,
                label="audio",
            )
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
            _check_at_most("text", len(body.text), bounds.max_tts_chars)
            wav = await tts.synthesize(
                body.text,
                language=body.language,
                speaker=body.speaker,
            )
            return Response(content=wav, media_type="audio/wav")

    if models is not None:
        held = models

        @router.get("/models", response_model=ModelRuntimeReport)
        async def list_models(probe: bool = True) -> ModelRuntimeReport:
            """Report which models are resident in memory right now.

            Args:
                probe (bool): Include the host memory snapshot. Pass
                    ``false`` to skip reading NVML — the only part of this
                    endpoint that costs anything.

            The report is built in a worker thread: probing reads NVML and
            ``torch.cuda``, synchronous calls that would otherwise stall
            every other request on the event loop.

            Returns:
                ModelRuntimeReport: The handles, loaded first and
                longest-idle first, plus the host picture when probed.
            """
            if hasattr(held, "inventory"):
                report: ModelRuntimeReport = await asyncio.to_thread(
                    held.inventory,
                    probe=probe,
                )
                return report
            return await asyncio.to_thread(runtime_report, held, probe=probe)

    if image_generator is not None:
        images = image_generator

        @router.post(
            "/image",
            status_code=status.HTTP_200_OK,
            response_class=Response,
        )
        async def render_image(body: ImageRequestSchema) -> Response:
            """Render one image and return its bytes.

            Exactly one image is rendered, because the response body is
            the image itself: ``config.num_images`` above ``1`` is refused
            with ``422`` rather than rendering a batch and returning only
            its first image. Ask for several with the class directly when
            you need a batch. The seed that produced it travels in the
            ``X-Image-Seed`` header, so a client can reproduce the render.

            Args:
                body (ImageRequestSchema): Prompt + optional config.

            Returns:
                Response: The encoded image, typed by the generator's
                ``image_format``.
            """
            _check_image(body, bounds)
            rendered = await images.generate(body.prompt, config=body.config)
            first = rendered[0]
            return Response(
                content=first.data,
                media_type=f"image/{first.image_format}",
                headers={"X-Image-Seed": str(first.seed)},
            )

    return router


def _check_at_most(field: str, value: int, limit: int) -> None:
    """Refuse a request whose ``field`` measures above ``limit``.

    Args:
        field (str): The request field being measured, reported back in
            ``details``.
        value (int): The measured size (characters, items or the value).
        limit (int): The largest accepted size.

    Raises:
        ValidationException: When ``value`` exceeds ``limit`` (``422``,
            ``details={"field": field, "limit": limit}``).
    """
    if value > limit:
        raise ValidationException(
            message=f"{field} exceeds the limit of {limit}",
            details={"field": field, "limit": limit},
        )


def _check_prompt(prompt: str, bounds: GenAIRequestLimits) -> None:
    """Refuse a prompt longer than ``bounds.max_prompt_chars``.

    Args:
        prompt (str): The prompt.
        bounds (GenAIRequestLimits): The router's limits.

    Raises:
        ValidationException: When the prompt is too long.
    """
    _check_at_most("prompt", len(prompt), bounds.max_prompt_chars)


def _check_generation(
    config: GenerationConfig | None,
    bounds: GenAIRequestLimits,
) -> None:
    """Refuse a ``max_new_tokens`` above ``bounds.max_new_tokens``.

    An unset ``max_new_tokens`` passes: the generator's own default was
    chosen by the operator, not by the caller.

    Args:
        config (GenerationConfig | None): The request's generation config.
        bounds (GenAIRequestLimits): The router's limits.

    Raises:
        ValidationException: When the requested token budget is too large.
    """
    if config is not None and config.max_new_tokens is not None:
        _check_at_most("max_new_tokens", config.max_new_tokens, bounds.max_new_tokens)


def _check_chat(
    messages: list[ChatMessageSchema],
    bounds: GenAIRequestLimits,
) -> None:
    """Refuse a chat with too many messages or too many characters.

    The character budget is ``bounds.max_prompt_chars`` summed across every
    message, because the whole transcript is what reaches the model.

    Args:
        messages (list[ChatMessageSchema]): The conversation.
        bounds (GenAIRequestLimits): The router's limits.

    Raises:
        ValidationException: When either ceiling is exceeded.
    """
    _check_at_most("messages", len(messages), bounds.max_chat_messages)
    total = sum(len(message.content) for message in messages)
    _check_at_most("messages", total, bounds.max_prompt_chars)


def _check_image(body: ImageRequestSchema, bounds: GenAIRequestLimits) -> None:
    """Refuse an ``/image`` request over the router's limits.

    Args:
        body (ImageRequestSchema): The request.
        bounds (GenAIRequestLimits): The router's limits.

    Raises:
        ValidationException: When the prompt, negative prompt, a side,
            the step count or ``num_images`` is over its limit.
    """
    _check_prompt(body.prompt, bounds)
    config = body.config
    if config is None:
        return
    if config.negative_prompt is not None:
        _check_at_most(
            "negative_prompt",
            len(config.negative_prompt),
            bounds.max_prompt_chars,
        )
    if config.width is not None:
        _check_at_most("width", config.width, bounds.max_image_side)
    if config.height is not None:
        _check_at_most("height", config.height, bounds.max_image_side)
    if config.steps is not None:
        _check_at_most("steps", config.steps, bounds.max_image_steps)
    _check_at_most("num_images", config.num_images, 1)


__all__: list[str] = [
    "ChatMessageSchema",
    "ChatRequestSchema",
    "ChatResponseSchema",
    "EmbedRequestSchema",
    "EmbedResponseSchema",
    "GenerateRequestSchema",
    "GenerateResponseSchema",
    "ImageRequestSchema",
    "RagRequestSchema",
    "RagResponseSchema",
    "TTSRequestSchema",
    "make_genai_router",
]
