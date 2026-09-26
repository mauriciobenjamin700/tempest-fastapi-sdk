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

from typing import Any, Protocol, runtime_checkable


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
