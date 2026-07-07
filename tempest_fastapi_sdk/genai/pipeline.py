"""Composable AI chat orchestrator tying the GenAI pieces together.

:class:`AIChatPipeline` is the opinionated-but-composable glue that turns
the individual GenAI building blocks into a single "send a message, get a
grounded reply" call — so a monolith no longer needs a separate
``llm-api`` service. You hand it a text backend and, optionally, the
extras you want wired in; it composes them per turn:

* a :class:`~tempest_fastapi_sdk.genai.text.TextBackend` (required) — the
  LLM that actually generates the reply;
* a :class:`~tempest_fastapi_sdk.genai.rag.ChatMemory` (optional) — auto
  recall of relevant past messages before the turn + auto indexing of
  both sides of the turn after it;
* a :class:`~tempest_fastapi_sdk.genai.rag.WebSearch` (optional) — augment
  the prompt with fresh web context when ``use_web_search=True``;
* a :class:`~tempest_fastapi_sdk.genai.audio.TextToSpeech` (optional) —
  synthesize the reply to audio when ``speak=True``;
* a list of :class:`Tool` (optional) — function-calling; when the backend
  exposes ``chat_with_tools`` the pipeline runs a bounded tool loop.

The companion :func:`make_ai_chat_router` mounts the pipeline on HTTP with
the same "hand it the pieces, get a working router" shape as
:func:`~tempest_fastapi_sdk.genai.make_genai_router`.

Why not wire the SDK's ``chat/`` domain? That domain models a
human, multi-participant chat (many users in one room); an AI turn is a
single-user request/response with server-owned history. They are a poor
fit, so this pipeline is deliberately **stateless** — ``history`` comes in
on each request and callers persist it however they like (their own
tables, the ``chat/`` domain, nothing at all).
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from fastapi import APIRouter
from pydantic import Field

from tempest_fastapi_sdk.genai.rag.chroma import MemoryHit
from tempest_fastapi_sdk.genai.rag.schemas import SearchResult
from tempest_fastapi_sdk.schemas.base import BaseSchema
from tempest_fastapi_sdk.sse import ServerSentEvent, sse_response

if TYPE_CHECKING:
    from starlette.responses import StreamingResponse

    from tempest_fastapi_sdk.genai.audio import TextToSpeech
    from tempest_fastapi_sdk.genai.rag import ChatMemory, WebSearch
    from tempest_fastapi_sdk.genai.text import TextBackend


@dataclass
class Tool:
    """A callable the LLM can invoke by name during a chat turn.

    Attributes:
        name (str): The function name the model calls.
        description (str): What the tool does (shown to the model).
        parameters (dict[str, Any]): JSON-schema of the tool's arguments.
        handler (Callable[[dict[str, Any]], Awaitable[str]]): Async
            function invoked with the parsed arguments; returns the tool
            result as a string fed back to the model.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[str]]

    def to_spec(self) -> dict[str, Any]:
        """Render the tool as an OpenAI / Ollama function specification.

        Returns:
            dict[str, Any]: The ``{"type": "function", "function": {...}}``
            spec passed to the backend's ``chat_with_tools``.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class AIChatResult(BaseSchema):
    """The outcome of one :meth:`AIChatPipeline.respond` turn.

    Attributes:
        reply (str): The assistant's final text reply.
        sources (list[SearchResult]): Web sources used to augment the
            prompt (empty unless web search ran).
        memory_hits (list[MemoryHit]): Past messages recalled from memory
            and injected into the system prompt (empty when no memory).
        tool_calls_made (list[str]): Names of the tools invoked, in order
            (empty when no tool loop ran).
        audio_base64 (str | None): Base64-encoded WAV of the reply when
            ``speak=True`` and a TTS backend is configured, else ``None``.
    """

    reply: str
    sources: list[SearchResult] = Field(default_factory=list)
    memory_hits: list[MemoryHit] = Field(default_factory=list)
    tool_calls_made: list[str] = Field(default_factory=list)
    audio_base64: str | None = None


class AIChatPipeline:
    """Compose a text backend with memory, web search, TTS and tools.

    The pipeline owns no persistence and no model lifecycle — you inject
    ready objects and it orchestrates them per turn. Only the pieces you
    provide are wired: pass just a generator and you get plain chat; add a
    ``memory`` and every turn recalls + indexes; add ``tools`` and (when
    the backend supports ``chat_with_tools``) a bounded tool loop runs.

    Example:

        >>> pipeline = AIChatPipeline(
        ...     OllamaGenerator("llama3.2"),
        ...     memory=chat_memory,
        ...     web_search=web_search,
        ... )
        >>> result = await pipeline.respond(
        ...     user_id="u1", chat_id="c1", content="What is PIX?",
        ... )
        >>> result.reply

    Attributes:
        generator (TextBackend): The LLM backend.
        memory (ChatMemory | None): Optional long-term chat memory.
        web_search (WebSearch | None): Optional web-search augmenter.
        tts (TextToSpeech | None): Optional text-to-speech backend.
        tools (list[Tool]): Tools available to the model (may be empty).
        base_system_prompt (str): System prompt prepended to every turn.
        max_tool_iterations (int): Upper bound on tool-loop rounds.
    """

    def __init__(
        self,
        generator: TextBackend,
        *,
        memory: ChatMemory | None = None,
        web_search: WebSearch | None = None,
        tts: TextToSpeech | None = None,
        tools: list[Tool] | None = None,
        base_system_prompt: str = "",
        max_tool_iterations: int = 5,
    ) -> None:
        """Configure the pipeline.

        Args:
            generator (TextBackend): The text-generation backend (required).
            memory (ChatMemory | None): Long-term memory for recall +
                indexing, or ``None`` to disable.
            web_search (WebSearch | None): Web-search augmenter, or ``None``.
            tts (TextToSpeech | None): Text-to-speech backend, or ``None``.
            tools (list[Tool] | None): Tools the model may call, or ``None``.
            base_system_prompt (str): System prompt prepended every turn.
            max_tool_iterations (int): Max tool-loop rounds before giving up.
        """
        self.generator = generator
        self.memory = memory
        self.web_search = web_search
        self.tts = tts
        self.tools: list[Tool] = list(tools or [])
        self.base_system_prompt = base_system_prompt
        self.max_tool_iterations = max_tool_iterations

    async def respond(
        self,
        *,
        user_id: str | UUID,
        chat_id: str | UUID,
        content: str,
        history: list[dict[str, Any]] | None = None,
        images: list[str] | None = None,
        use_web_search: bool = False,
        speak: bool = False,
    ) -> AIChatResult:
        """Produce a grounded reply for a single chat turn.

        Recalls relevant memory, optionally augments with web context,
        builds the message list, generates a reply (running the tool loop
        when tools + a tool-capable backend are present), optionally
        synthesizes audio, and indexes both sides of the turn into memory
        (best-effort — indexing failures never break the response).

        Args:
            user_id (str | UUID): Owner of the conversation.
            chat_id (str | UUID): The active chat.
            content (str): The user's message.
            history (list[dict[str, Any]] | None): Prior turns, each
                ``{"role": ..., "content": ...}``. Defaults to empty.
            images (list[str] | None): Base64 images for a multimodal
                model; placed on the user message when truthy.
            use_web_search (bool): Augment the prompt with web context.
            speak (bool): Synthesize the reply to audio when a TTS backend
                is configured.

        Returns:
            AIChatResult: The reply plus any sources, memory hits, tool
            names and audio produced.
        """
        messages, hits, sources = await self._prepare(
            user_id=user_id,
            chat_id=chat_id,
            content=content,
            history=history,
            images=images,
            use_web_search=use_web_search,
        )

        tool_calls_made: list[str] = []
        chat_with_tools = getattr(self.generator, "chat_with_tools", None)
        if self.tools and callable(chat_with_tools):
            reply, tool_calls_made = await self._run_tool_loop(messages)
        else:
            reply = await self.generator.chat(messages)

        audio_base64: str | None = None
        if speak and self.tts is not None:
            wav = await self.tts.synthesize(reply)
            audio_base64 = base64.b64encode(wav).decode()

        await self._index_turn(
            user_id=user_id,
            chat_id=chat_id,
            content=content,
            reply=reply,
        )

        return AIChatResult(
            reply=reply,
            sources=sources,
            memory_hits=hits,
            tool_calls_made=tool_calls_made,
            audio_base64=audio_base64,
        )

    async def stream(
        self,
        *,
        user_id: str | UUID,
        chat_id: str | UUID,
        content: str,
        history: list[dict[str, Any]] | None = None,
        images: list[str] | None = None,
        use_web_search: bool = False,
    ) -> AsyncIterator[str]:
        """Stream a reply piece by piece for a single chat turn.

        Runs the same recall + augment + (non-streamed) tool loop as
        :meth:`respond` to resolve any tool calls, then streams the final
        answer. Because :meth:`TextBackend.stream` takes a single prompt,
        the built message list is flattened into one prompt string
        (system + turns) — streaming is **prompt-mode**, and it does not
        itself stream the intermediate tool-call steps. The final answer is
        indexed into memory after the stream completes (best-effort).

        Args:
            user_id (str | UUID): Owner of the conversation.
            chat_id (str | UUID): The active chat.
            content (str): The user's message.
            history (list[dict[str, Any]] | None): Prior turns.
            images (list[str] | None): Base64 images (placed on the user
                message; note prompt-mode streaming may not honor them for
                every backend).
            use_web_search (bool): Augment the prompt with web context.

        Yields:
            str: Text pieces of the reply as the backend produces them.
        """
        messages, _hits, _sources = await self._prepare(
            user_id=user_id,
            chat_id=chat_id,
            content=content,
            history=history,
            images=images,
            use_web_search=use_web_search,
        )

        chat_with_tools = getattr(self.generator, "chat_with_tools", None)
        if self.tools and callable(chat_with_tools):
            await self._run_tool_loop(messages)

        prompt = _flatten_messages(messages)
        collected: list[str] = []
        async for piece in self.generator.stream(prompt):
            collected.append(piece)
            yield piece

        await self._index_turn(
            user_id=user_id,
            chat_id=chat_id,
            content=content,
            reply="".join(collected),
        )

    async def _prepare(
        self,
        *,
        user_id: str | UUID,
        chat_id: str | UUID,
        content: str,
        history: list[dict[str, Any]] | None,
        images: list[str] | None,
        use_web_search: bool,
    ) -> tuple[list[dict[str, Any]], list[MemoryHit], list[SearchResult]]:
        """Recall memory, optionally augment, and build the message list.

        Args:
            user_id (str | UUID): Owner of the conversation.
            chat_id (str | UUID): The active chat (excluded from recall).
            content (str): The user's message.
            history (list[dict[str, Any]] | None): Prior turns.
            images (list[str] | None): Base64 images for the user message.
            use_web_search (bool): Whether to run web augmentation.

        Returns:
            tuple[list[dict[str, Any]], list[MemoryHit], list[SearchResult]]:
            The message list, recalled memory hits, and web sources.
        """
        turns = history or []

        hits: list[MemoryHit] = []
        if self.memory is not None:
            hits = await self.memory.search(
                user_id=user_id,
                query=content,
                exclude_chat_id=chat_id,
            )

        context = ""
        sources: list[SearchResult] = []
        if use_web_search and self.web_search is not None:
            context = await self.web_search.retrieve(content)
            sources = await self.web_search.search(content)

        messages = self._build_messages(content, turns, images, hits, context)
        return messages, hits, sources

    def _build_messages(
        self,
        content: str,
        history: list[dict[str, Any]],
        images: list[str] | None,
        hits: list[MemoryHit],
        context: str,
    ) -> list[dict[str, Any]]:
        """Assemble the system message, history and the new user message.

        The system message concatenates the base prompt, a "Relevant
        memory" block built from ``hits`` and a "Web context" block from
        ``context`` (each section omitted when empty). ``images`` are
        attached to the user message when truthy.

        Args:
            content (str): The user's message.
            history (list[dict[str, Any]]): Prior turns.
            images (list[str] | None): Base64 images for the user message.
            hits (list[MemoryHit]): Recalled memory to inject.
            context (str): Prompt-ready web context to inject.

        Returns:
            list[dict[str, Any]]: The message list for the backend.
        """
        sections: list[str] = []
        if self.base_system_prompt:
            sections.append(self.base_system_prompt)
        if hits:
            lines = ["Relevant memory:"]
            lines.extend(f"- {hit.content}" for hit in hits)
            sections.append("\n".join(lines))
        if context:
            sections.append(f"Web context:\n{context}")

        messages: list[dict[str, Any]] = []
        if sections:
            messages.append({"role": "system", "content": "\n\n".join(sections)})
        messages.extend(history)

        user_message: dict[str, Any] = {"role": "user", "content": content}
        if images:
            user_message["images"] = images
        messages.append(user_message)
        return messages

    async def _run_tool_loop(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[str, list[str]]:
        """Run the bounded tool-calling loop, mutating ``messages`` in place.

        Repeatedly calls the backend's ``chat_with_tools``; when the model
        emits ``tool_calls``, each is dispatched to its :class:`Tool`
        handler and the result is appended as a ``tool`` message before
        looping. Unknown tool names append an error result rather than
        crashing. The loop runs at most ``max_tool_iterations`` times.

        Args:
            messages (list[dict[str, Any]]): The running message list;
                assistant tool-call echoes and tool results are appended.

        Returns:
            tuple[str, list[str]]: The final reply text and the ordered
            list of invoked tool names.
        """
        generator: Any = self.generator
        tool_by_name: dict[str, Tool] = {tool.name: tool for tool in self.tools}
        specs: list[dict[str, Any]] = [tool.to_spec() for tool in self.tools]

        reply = ""
        names: list[str] = []
        for _ in range(self.max_tool_iterations):
            message: dict[str, Any] = await generator.chat_with_tools(messages, specs)
            reply = str(message.get("content", ""))
            tool_calls: list[dict[str, Any]] = message.get("tool_calls") or []
            if not tool_calls:
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content", ""),
                    "tool_calls": tool_calls,
                }
            )
            for call in tool_calls:
                function: dict[str, Any] = call.get("function") or {}
                name = str(function.get("name", ""))
                arguments: dict[str, Any] = function.get("arguments") or {}
                names.append(name)
                tool = tool_by_name.get(name)
                if tool is None:
                    result = f"Error: unknown tool '{name}'."
                else:
                    result = await tool.handler(arguments)
                messages.append({"role": "tool", "content": result})
        return reply, names

    async def _index_turn(
        self,
        *,
        user_id: str | UUID,
        chat_id: str | UUID,
        content: str,
        reply: str,
    ) -> None:
        """Index both sides of the turn into memory (best-effort).

        Computes ``created_at`` here (never at import time) and gives each
        turn a fresh id. Any embedding/store failure is swallowed so
        indexing can never break the response.

        Args:
            user_id (str | UUID): Owner of the conversation.
            chat_id (str | UUID): The active chat.
            content (str): The user's message.
            reply (str): The assistant's reply.
        """
        if self.memory is None:
            return
        created_at = datetime.now(UTC)
        for role, text in (("user", content), ("assistant", reply)):
            try:
                await self.memory.index(
                    user_id=user_id,
                    chat_id=chat_id,
                    message_id=uuid4(),
                    role=role,
                    content=text,
                    created_at=created_at,
                )
            except Exception:
                continue


def _flatten_messages(messages: list[dict[str, Any]]) -> str:
    """Flatten a chat message list into a single prompt string.

    Used by :meth:`AIChatPipeline.stream` because
    :meth:`~tempest_fastapi_sdk.genai.text.TextBackend.stream` takes a
    prompt rather than a message list.

    Args:
        messages (list[dict[str, Any]]): The message list.

    Returns:
        str: A ``"role: content"`` block per message, blank-line separated.
    """
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role", ""))
        content = str(message.get("content", ""))
        parts.append(f"{role}: {content}")
    return "\n\n".join(parts)


class AIChatTurnSchema(BaseSchema):
    """One prior chat turn in an :class:`AIChatRequestSchema`.

    Attributes:
        role (str): The speaker role (``"user"`` / ``"assistant"`` /
            ``"system"``).
        content (str): The message text.
    """

    role: str
    content: str


class AIChatRequestSchema(BaseSchema):
    """Request body for the AI-chat router endpoints.

    Attributes:
        user_id (str): Owner of the conversation.
        chat_id (str): The active chat.
        content (str): The user's message.
        history (list[AIChatTurnSchema]): Prior turns (caller-supplied;
            the pipeline is stateless).
        images (list[str]): Base64 images for a multimodal model.
        use_web_search (bool): Augment the prompt with web context.
        speak (bool): Synthesize the reply to audio (``/chat`` only).
    """

    user_id: str
    chat_id: str
    content: str
    history: list[AIChatTurnSchema] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    use_web_search: bool = False
    speak: bool = False


def make_ai_chat_router(
    pipeline: AIChatPipeline,
    *,
    prefix: str = "/api/ai-chat",
    tags: list[str] | None = None,
) -> APIRouter:
    """Mount an :class:`AIChatPipeline` on HTTP endpoints.

    Registers two endpoints:

    * ``POST {prefix}/chat`` — a full turn returning an
      :class:`AIChatResult` (reply, sources, memory hits, tool names,
      optional audio).
    * ``POST {prefix}/chat/stream`` — the reply streamed token-by-token
      over SSE, ending with a ``done`` event.

    The router is stateless: ``history`` rides in on each request body and
    the caller persists it however they like (see the module docstring on
    why the ``chat/`` domain is intentionally not wired). Add auth by
    including the router under an authenticated parent.

    Args:
        pipeline (AIChatPipeline): The configured pipeline to expose.
        prefix (str): URL prefix. Defaults to ``"/api/ai-chat"``.
        tags (list[str] | None): OpenAPI tags. Defaults to ``["ai-chat"]``.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.
    """
    router = APIRouter(prefix=prefix, tags=list(tags or ["ai-chat"]))

    @router.post("/chat", response_model=AIChatResult)
    async def chat(body: AIChatRequestSchema) -> AIChatResult:
        """Produce a full grounded reply for one chat turn.

        Args:
            body (AIChatRequestSchema): The turn request.

        Returns:
            AIChatResult: The reply and everything produced with it.
        """
        return await pipeline.respond(
            user_id=body.user_id,
            chat_id=body.chat_id,
            content=body.content,
            history=[turn.model_dump() for turn in body.history],
            images=body.images or None,
            use_web_search=body.use_web_search,
            speak=body.speak,
        )

    @router.post("/chat/stream")
    async def chat_stream(body: AIChatRequestSchema) -> StreamingResponse:
        """Stream the reply token-by-token over SSE.

        Args:
            body (AIChatRequestSchema): The turn request.

        Returns:
            StreamingResponse: A ``text/event-stream`` of token events,
            ending with a ``done`` event.
        """

        async def _events() -> AsyncIterator[bytes]:
            async for piece in pipeline.stream(
                user_id=body.user_id,
                chat_id=body.chat_id,
                content=body.content,
                history=[turn.model_dump() for turn in body.history],
                images=body.images or None,
                use_web_search=body.use_web_search,
            ):
                yield ServerSentEvent(data=piece).encode().encode("utf-8")
            yield ServerSentEvent(data="", event="done").encode().encode("utf-8")

        return sse_response(_events())

    return router


__all__: list[str] = [
    "AIChatPipeline",
    "AIChatRequestSchema",
    "AIChatResult",
    "AIChatTurnSchema",
    "Tool",
    "make_ai_chat_router",
]
