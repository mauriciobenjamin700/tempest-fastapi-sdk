"""What an agent needs from the model it drives.

`Agent` used to take ``generator: Any``, which typed nothing: a caller
passing an embedder, a router, or a plain string only found out at the
first `await`, deep inside the loop. These protocols name the contract
instead, so a wrong object is a type error at the call site and any
engine — the SDK's ``TextGenerator``/``OllamaGenerator``, vLLM, TGI, a
hosted API — is a valid backend the moment it implements them.

The split is deliberate. `ChatBackend` is the floor: `chat` alone runs a
toolless agent, which is a single-shot answer. `ToolCallingBackend` adds
`chat_with_tools`, which is what makes the loop worth having. The agent
probes for it at runtime and falls back to `chat`, so both are accepted
where a backend is asked for.

Both describe a client we do not own, so they state only what `Agent`
actually does with it: every argument is passed **positionally** (so the
parameters are positional-only and an implementation may name them as it
likes), and no keyword option is ever passed (so an implementation does not
need ``**kwargs``). The message dicts are ``dict[str, Any]`` because the
conversation carries more than role/content strings — an assistant turn
holds its ``tool_calls`` list.
"""

from __future__ import annotations

from collections.abc import Awaitable, Sequence
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from tempest_fastapi_sdk.genai.schemas import (
        GeneratedImage,
        ImageGenerationConfig,
    )


@runtime_checkable
class ChatBackend(Protocol):
    """The minimum an agent needs: turn a message list into a reply."""

    async def chat(self, messages: list[dict[str, Any]], /) -> str:
        """Return the model's reply for a chat ``messages`` list.

        Args:
            messages (list[dict[str, Any]]): The conversation, oldest first.

        Returns:
            str: The reply text.
        """
        ...


@runtime_checkable
class ToolCallingBackend(ChatBackend, Protocol):
    """A chat backend that can also ask for tools by name."""

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        /,
    ) -> dict[str, Any]:
        """Return a reply that may request tool calls.

        Args:
            messages (list[dict[str, Any]]): The conversation so far,
                including assistant turns with ``tool_calls`` and
                ``role: tool`` results.
            tools (list[dict[str, Any]]): JSON-schema specs of the tools
                the model may call this turn.

        Returns:
            dict[str, Any]: A message dict with ``content`` and, when the
            model asked for tools, a ``tool_calls`` list. Each call's
            ``arguments`` may be a dict or a JSON string.
        """
        ...


AgentBackend = ChatBackend | ToolCallingBackend
"""What `Agent` accepts as its model.

A backend without ``chat_with_tools`` still drives a toolless agent; the
loop probes for the method and falls back to :meth:`ChatBackend.chat`.
"""


class SupportsImageGeneration(Protocol):
    """What :func:`~tempest_fastapi_sdk.agents.generate_image_tool` draws with.

    Satisfied by :class:`~tempest_fastapi_sdk.genai.ImageGenerator`; any
    other engine qualifies by rendering a prompt into
    :class:`~tempest_fastapi_sdk.genai.GeneratedImage` values. The tool
    passes the prompt positionally and always passes ``config``.
    """

    def generate(
        self,
        prompt: str,
        /,
        *,
        config: ImageGenerationConfig | None = None,
    ) -> Awaitable[Sequence[GeneratedImage]]:
        """Render ``prompt`` into one or more images.

        Args:
            prompt (str): What to draw.
            config (ImageGenerationConfig | None): Steps and seed the model
                asked for; ``None`` for the engine's defaults.

        Returns:
            Awaitable[Sequence[GeneratedImage]]: The rendered images; the
            tool keeps the first.
        """
        ...


class SupportsVisionGenerate(Protocol):
    """What :func:`~tempest_fastapi_sdk.agents.describe_image_tool` reads with.

    Satisfied by :class:`~tempest_fastapi_sdk.genai.VisionTextGenerator`.
    Each image arrives as raw ``bytes`` (a run artifact) or as the absolute
    path string of a file under the tool's ``path_root``, so an engine that
    only takes base64 strings does not qualify.
    """

    def generate(
        self,
        prompt: str,
        /,
        *,
        images: list[bytes | str],
    ) -> Awaitable[str]:
        """Answer ``prompt`` about ``images``.

        Args:
            prompt (str): The question about the image.
            images (list[bytes | str]): Raw image bytes or file paths.

        Returns:
            Awaitable[str]: The model's answer.
        """
        ...


class SupportsWebRetrieve(Protocol):
    """What :func:`~tempest_fastapi_sdk.agents.web_search_tool` searches with.

    Satisfied by :class:`~tempest_fastapi_sdk.genai.rag.WebSearch`: a query
    in, a prompt-ready context block out.
    """

    def retrieve(
        self,
        query: str,
        /,
        *,
        max_results: int = 5,
    ) -> Awaitable[str]:
        """Search the web and build a context block for ``query``.

        Args:
            query (str): What to search for.
            max_results (int): How many results to read.

        Returns:
            Awaitable[str]: The context block; empty when nothing matched.
        """
        ...
