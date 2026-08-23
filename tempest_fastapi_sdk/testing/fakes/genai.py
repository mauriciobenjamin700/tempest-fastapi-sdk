"""Text generation and moderation without a model.

A local model is the slowest thing in a test suite and the heaviest thing in
a dev environment: weights to download, VRAM to hold, minutes per run. These
two fakes answer in microseconds, deterministically, so a chat flow, an agent
loop or a moderation gate can be exercised on a laptop with no weights at
all.
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
from typing import Any

from tempest_fastapi_sdk.genai.moderation import ModerationResult
from tempest_fastapi_sdk.genai.schemas import GenerationConfig
from tempest_fastapi_sdk.testing.fakes._control import _Steerable


class FakeTextBackend(_Steerable):
    """A ``TextBackend`` that answers from a queue, then from a template.

    Example:

        >>> backend = FakeTextBackend()
        >>> backend.queue("Bom dia!")
        >>> await backend.generate("Cumprimente o cliente")
        'Bom dia!'

    Attributes:
        prompts (list[str]): Every prompt this backend was given, in order.
        calls (list[str]): Methods that ran, in order.
    """

    def __init__(self, *, default: str | None = None) -> None:
        """Start with an empty queue.

        Args:
            default (str | None): Reply to use once the queue is empty. When
                ``None``, the reply echoes the prompt as
                ``"[fake] <prompt>"`` — deterministic, and it shows in an
                assertion which prompt produced it.
        """
        super().__init__()
        self._queued: deque[str] = deque()
        self._default: str | None = default
        self.prompts: list[str] = []

    def queue(self, *replies: str) -> None:
        """Queue replies, consumed one per call, in order.

        Args:
            *replies (str): The replies to hand out.
        """
        self._queued.extend(replies)

    async def generate(
        self,
        prompt: str,
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> str:
        """Answer a single prompt.

        Args:
            prompt (str): The prompt.
            config (GenerationConfig | None): Recorded, never applied — a
                fake has no sampler to configure.
            **kwargs (Any): Recorded and ignored, for signature parity with
                the real backends.

        Returns:
            str: The next queued reply, or the default.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("generate")
        self.prompts.append(prompt)
        return self._reply(prompt)

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> str:
        """Answer a message list.

        Args:
            messages (list[dict[str, str]]): The conversation so far.
            config (GenerationConfig | None): Recorded, never applied.
            **kwargs (Any): Recorded and ignored.

        Returns:
            str: The next queued reply, or the default derived from the last
            message's content.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("chat")
        last = messages[-1].get("content", "") if messages else ""
        self.prompts.append(last)
        return self._reply(last)

    def stream(
        self,
        prompt: str,
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream a reply word by word.

        Args:
            prompt (str): The prompt.
            config (GenerationConfig | None): Recorded, never applied.
            **kwargs (Any): Recorded and ignored.

        Returns:
            AsyncIterator[str]: The reply, one whitespace-separated chunk at
            a time, so a consumer's incremental rendering is exercised
            rather than handed the whole string at once.
        """
        self._record("stream")
        self.prompts.append(prompt)
        reply = self._reply(prompt)

        async def _chunks() -> AsyncIterator[str]:
            """Yield the reply in pieces.

            Returns:
                AsyncIterator[str]: One chunk per word.
            """
            for index, word in enumerate(reply.split()):
                yield word if index == 0 else f" {word}"

        return _chunks()

    def _reply(self, prompt: str) -> str:
        """Take the next queued reply, or build the default.

        Args:
            prompt (str): The prompt the reply answers.

        Returns:
            str: The reply.
        """
        if self._queued:
            return self._queued.popleft()
        if self._default is not None:
            return self._default
        return f"[fake] {prompt}"


class FakeModerationBackend(_Steerable):
    """A ``ModerationBackend`` that flags what you tell it to flag.

    Example:

        >>> moderator = FakeModerationBackend()
        >>> moderator.flag("idiota", category="insult")
        >>> (await moderator.check("seu idiota")).flagged
        True

    Attributes:
        checked (list[str]): Every text this backend saw, in order.
        calls (list[str]): Methods that ran, in order.
    """

    def __init__(self, *, flagged_score: float = 0.99) -> None:
        """Start flagging nothing.

        Args:
            flagged_score (float): Score reported for a flagged text. Clean
                text always scores ``0.0``.
        """
        super().__init__()
        self._triggers: dict[str, str] = {}
        self._flagged_score: float = flagged_score
        self.checked: list[str] = []

    def flag(self, substring: str, *, category: str = "fake") -> None:
        """Flag any text containing ``substring``, case-insensitively.

        Args:
            substring (str): The needle to look for.
            category (str): Category name reported for the match.
        """
        self._triggers[substring.casefold()] = category

    async def check(self, text: str) -> ModerationResult:
        """Moderate one text.

        Args:
            text (str): The text to check.

        Returns:
            ModerationResult: Flagged when any registered substring is
            present, with every matching category listed.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("check")
        self.checked.append(text)
        haystack = text.casefold()
        categories = [
            category
            for needle, category in self._triggers.items()
            if needle in haystack
        ]
        return ModerationResult(
            flagged=bool(categories),
            categories=sorted(set(categories)),
            score=self._flagged_score if categories else 0.0,
        )
