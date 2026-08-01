"""Ready-made tools over the models the SDK already self-hosts.

Each factory takes an object you built — an
:class:`~tempest_fastapi_sdk.genai.ImageGenerator`, a
:class:`~tempest_fastapi_sdk.genai.audio.SpeechToText`, a
:class:`~tempest_fastapi_sdk.genai.rag.Retriever` — and returns an
:class:`~tempest_fastapi_sdk.agents.AgentTool` the model can call by name.
Nothing here loads a model or imports a heavy library; the objects arrive
ready and keep their own lazy loading.

The tools chain through **named artifacts**. ``generate_image`` registers
``chart.png`` on the run; ``describe_image`` accepts that same name and
reads the bytes back from the context. That is what lets one agent draw
something and then look at what it drew, with the picture never leaving
memory and the model never carrying base64 through a prompt.

Every factory takes ``name=`` so two of the same kind can coexist (two
image models, two retrievers over different corpora) — the model
distinguishes tools only by name and description.
"""

from __future__ import annotations

import io
from typing import Any

from tempest_fastapi_sdk.agents.schemas import AgentArtifact, ToolResult
from tempest_fastapi_sdk.agents.tools import AgentContext, AgentTool, AgentToolError

_IMAGE_MEDIA_TYPES: dict[str, str] = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "webp": "image/webp",
}
"""Media type per encoding the image generator can emit."""


def _artifact_or_path(
    arguments: dict[str, Any],
    context: AgentContext,
) -> Any:
    """Resolve an image argument to something a model can consume.

    Accepts either ``artifact`` (a name registered on the run) or ``path``
    (a file on disk), because a model may want to look at something it just
    made *or* at something that was already there.

    Args:
        arguments (dict[str, Any]): The tool call's arguments.
        context (AgentContext): The run context holding the artifacts.

    Returns:
        Any: Raw ``bytes`` for an artifact, or the path string.

    Raises:
        AgentToolError: When neither argument is present, or the named
            artifact does not exist.
    """
    name = arguments.get("artifact")
    if name:
        return context.require_artifact(str(name)).data
    path = arguments.get("path")
    if path:
        return str(path)
    raise AgentToolError("provide either 'artifact' or 'path'")


def generate_image_tool(
    image_generator: Any,
    *,
    name: str = "generate_image",
    description: str = (
        "Draw an image from a text prompt and save it under a name. "
        "Use the returned name with other tools to inspect or send it."
    ),
    default_steps: int | None = None,
) -> AgentTool:
    """Build a tool that draws an image and registers it as an artifact.

    Example:

        >>> tool = generate_image_tool(ImageGenerator("stabilityai/sdxl-turbo"))

    Args:
        image_generator (Any): An
            :class:`~tempest_fastapi_sdk.genai.ImageGenerator`.
        name (str): The function name the model calls.
        description (str): What the tool does, written for the model.
        default_steps (int | None): Denoising steps when the model does not
            ask for a count. Set it to the value your checkpoint wants —
            a turbo model needs ~4 and a full one ~30, and the model
            choosing blind is how a render takes ten times too long.

    Returns:
        AgentTool: The image-generation tool.
    """

    async def handler(
        arguments: dict[str, Any],
        context: AgentContext,
    ) -> ToolResult:
        """Render the prompt and store the result under ``filename``."""
        from tempest_fastapi_sdk.genai import ImageGenerationConfig

        prompt = str(arguments.get("prompt", "")).strip()
        if not prompt:
            raise AgentToolError("'prompt' is required")
        steps = arguments.get("steps", default_steps)
        config = ImageGenerationConfig(
            steps=int(steps) if steps else None,
            seed=int(arguments["seed"]) if arguments.get("seed") else None,
        )
        images = await image_generator.generate(prompt, config=config)
        image = images[0]
        filename = str(arguments.get("filename") or f"{name}-{len(context.artifacts)}")
        if "." not in filename:
            filename = f"{filename}.{image.image_format}"
        artifact = AgentArtifact(
            name=filename,
            media_type=_IMAGE_MEDIA_TYPES.get(image.image_format, "image/png"),
            data=image.data,
            description=prompt,
        )
        return ToolResult(
            text=(
                f"Generated {filename} ({image.width}x{image.height}, "
                f"seed {image.seed})."
            ),
            artifacts=[artifact],
        )

    return AgentTool(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "What to draw.",
                },
                "filename": {
                    "type": "string",
                    "description": "Name to store the image under.",
                },
                "steps": {
                    "type": "integer",
                    "description": "Denoising steps; leave unset for the default.",
                },
                "seed": {
                    "type": "integer",
                    "description": "Seed for a reproducible image.",
                },
            },
            "required": ["prompt"],
        },
        handler=handler,
    )


def describe_image_tool(
    vision_generator: Any,
    *,
    name: str = "describe_image",
    description: str = (
        "Look at an image and answer a question about it. "
        "Pass either 'artifact' (a name from an earlier step) or 'path'."
    ),
) -> AgentTool:
    """Build a tool that reads an image with a vision-language model.

    This is the other half of :func:`generate_image_tool`: together they
    let an agent draw something and then judge what it drew.

    Args:
        vision_generator (Any): A
            :class:`~tempest_fastapi_sdk.genai.VisionTextGenerator`.
        name (str): The function name the model calls.
        description (str): What the tool does, written for the model.

    Returns:
        AgentTool: The image-understanding tool.
    """

    async def handler(
        arguments: dict[str, Any],
        context: AgentContext,
    ) -> str:
        """Ask the vision model about the referenced image."""
        source = _artifact_or_path(arguments, context)
        question = str(arguments.get("question") or "Describe this image.")
        return str(await vision_generator.generate(question, images=[source]))

    return AgentTool(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "artifact": {
                    "type": "string",
                    "description": "Name of an image produced earlier.",
                },
                "path": {
                    "type": "string",
                    "description": "Path to an image on disk.",
                },
                "question": {
                    "type": "string",
                    "description": "What to ask about the image.",
                },
            },
        },
        handler=handler,
    )


def transcribe_audio_tool(
    speech_to_text: Any,
    *,
    name: str = "transcribe_audio",
    description: str = (
        "Transcribe speech from an audio file into text. "
        "Pass either 'artifact' (a name from an earlier step) or 'path'."
    ),
) -> AgentTool:
    """Build a tool that turns audio into text.

    Args:
        speech_to_text (Any): A
            :class:`~tempest_fastapi_sdk.genai.audio.SpeechToText`.
        name (str): The function name the model calls.
        description (str): What the tool does, written for the model.

    Returns:
        AgentTool: The transcription tool.
    """

    async def handler(
        arguments: dict[str, Any],
        context: AgentContext,
    ) -> str:
        """Transcribe the referenced audio, reporting the detected language."""
        source = _artifact_or_path(arguments, context)
        language = arguments.get("language")
        result = await speech_to_text.transcribe(
            source,
            language=str(language) if language else None,
        )
        detected = getattr(result, "language", None)
        prefix = f"[{detected}] " if detected else ""
        return f"{prefix}{result.text}"

    return AgentTool(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "artifact": {
                    "type": "string",
                    "description": "Name of an audio clip from an earlier step.",
                },
                "path": {
                    "type": "string",
                    "description": "Path to an audio file on disk.",
                },
                "language": {
                    "type": "string",
                    "description": "Language hint (e.g. 'pt'); omit to detect.",
                },
            },
        },
        handler=handler,
    )


def speak_tool(
    text_to_speech: Any,
    *,
    name: str = "speak",
    description: str = (
        "Turn text into spoken audio and save it under a name. "
        "Use it when the answer should be heard rather than read."
    ),
) -> AgentTool:
    """Build a tool that synthesizes speech into an artifact.

    Args:
        text_to_speech (Any): A
            :class:`~tempest_fastapi_sdk.genai.audio.TextToSpeech`.
        name (str): The function name the model calls.
        description (str): What the tool does, written for the model.

    Returns:
        AgentTool: The speech-synthesis tool.
    """

    async def handler(
        arguments: dict[str, Any],
        context: AgentContext,
    ) -> ToolResult:
        """Synthesize the text and store the WAV under ``filename``."""
        text = str(arguments.get("text", "")).strip()
        if not text:
            raise AgentToolError("'text' is required")
        language = arguments.get("language")
        wav = await text_to_speech.synthesize(
            text,
            language=str(language) if language else None,
        )
        filename = str(arguments.get("filename") or f"{name}-{len(context.artifacts)}")
        if not filename.endswith(".wav"):
            filename = f"{filename}.wav"
        artifact = AgentArtifact(
            name=filename,
            media_type="audio/wav",
            data=wav,
            description=text[:200],
        )
        return ToolResult(
            text=f"Spoke {len(text)} characters into {filename}.",
            artifacts=[artifact],
        )

    return AgentTool(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "What to say.",
                },
                "filename": {
                    "type": "string",
                    "description": "Name to store the audio under.",
                },
                "language": {
                    "type": "string",
                    "description": "Language or preset for the voice.",
                },
            },
            "required": ["text"],
        },
        handler=handler,
    )


def retrieve_tool(
    retriever: Any,
    *,
    name: str = "search_documents",
    description: str = (
        "Search the indexed document corpus and return the passages that "
        "best match a question."
    ),
    top_k: int = 5,
) -> AgentTool:
    """Build a tool that queries a RAG corpus.

    Args:
        retriever (Any): A
            :class:`~tempest_fastapi_sdk.genai.rag.Retriever` or
            ``HybridRetriever`` (anything with ``retrieve``).
        name (str): The function name the model calls.
        description (str): What the tool does, written for the model.
        top_k (int): Passages to return when the model does not say.

    Returns:
        AgentTool: The corpus-search tool.
    """

    async def handler(
        arguments: dict[str, Any],
        _context: AgentContext,
    ) -> str:
        """Retrieve a context block for the question."""
        question = str(arguments.get("query", "")).strip()
        if not question:
            raise AgentToolError("'query' is required")
        requested = arguments.get("top_k")
        context_block = await retriever.retrieve(
            question,
            top_k=int(requested) if requested else top_k,
        )
        return str(context_block) or "No matching passages."

    return AgentTool(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to look for.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "How many passages to return.",
                },
            },
            "required": ["query"],
        },
        handler=handler,
    )


def web_search_tool(
    web_search: Any,
    *,
    name: str = "search_web",
    description: str = (
        "Search the web and return the extracted text of the top results. "
        "Use it for facts that may have changed recently."
    ),
    max_results: int = 3,
) -> AgentTool:
    """Build a tool that searches the web through a self-hosted SearXNG.

    Args:
        web_search (Any): A
            :class:`~tempest_fastapi_sdk.genai.rag.WebSearch`.
        name (str): The function name the model calls.
        description (str): What the tool does, written for the model.
        max_results (int): Results to fetch when the model does not say.

    Returns:
        AgentTool: The web-search tool.
    """

    async def handler(
        arguments: dict[str, Any],
        _context: AgentContext,
    ) -> str:
        """Search the web and return a prompt-ready context block."""
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise AgentToolError("'query' is required")
        requested = arguments.get("max_results")
        block = await web_search.retrieve(
            query,
            max_results=int(requested) if requested else max_results,
        )
        return str(block) or "No results."

    return AgentTool(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "How many results to read.",
                },
            },
            "required": ["query"],
        },
        handler=handler,
    )


def save_artifact_tool(
    *,
    name: str = "save_text",
    description: str = (
        "Save a block of text as a named file the caller will receive."
    ),
) -> AgentTool:
    """Build a tool that turns text into a downloadable artifact.

    Useful as the last step of a run whose product is a document rather
    than a chat reply — the text comes back as bytes the caller can write
    or serve, instead of being buried in the final message.

    Args:
        name (str): The function name the model calls.
        description (str): What the tool does, written for the model.

    Returns:
        AgentTool: The text-saving tool.
    """

    async def handler(
        arguments: dict[str, Any],
        context: AgentContext,
    ) -> ToolResult:
        """Store the given text as a UTF-8 artifact."""
        content = str(arguments.get("content", ""))
        if not content:
            raise AgentToolError("'content' is required")
        filename = str(
            arguments.get("filename") or f"note-{len(context.artifacts)}.txt"
        )
        buffer = io.BytesIO(content.encode("utf-8"))
        artifact = AgentArtifact(
            name=filename,
            media_type="text/plain; charset=utf-8",
            data=buffer.getvalue(),
            description=content[:200],
        )
        return ToolResult(
            text=f"Saved {len(content)} characters to {filename}.",
            artifacts=[artifact],
        )

    return AgentTool(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The text to save.",
                },
                "filename": {
                    "type": "string",
                    "description": "Name to store it under.",
                },
            },
            "required": ["content"],
        },
        handler=handler,
    )


__all__: list[str] = [
    "describe_image_tool",
    "generate_image_tool",
    "retrieve_tool",
    "save_artifact_tool",
    "speak_tool",
    "transcribe_audio_tool",
    "web_search_tool",
]
