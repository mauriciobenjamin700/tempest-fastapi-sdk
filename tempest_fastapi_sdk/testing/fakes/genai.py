"""Text generation, structured output and moderation without a model.

A local model is the slowest thing in a test suite and the heaviest thing in
a dev environment: weights to download, VRAM to hold, minutes per run. These
fakes answer in microseconds, deterministically, so a chat flow, an
extraction route, an agent loop or a moderation gate can be exercised on a
laptop with no weights at all.
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from tempest_fastapi_sdk.genai.moderation import ModerationResult
from tempest_fastapi_sdk.genai.ollama._constants import ThinkLevel
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


@dataclass(frozen=True)
class StructuredRequest:
    """One call a :class:`FakeStructuredBackend` received.

    Attributes:
        method (str): ``"chat_structured"`` or ``"generate_structured"``.
        messages (list[dict[str, Any]]): The turns the real generator would
            post — for ``generate_structured``, the optional ``system`` turn
            followed by the prompt as a ``user`` turn, as
            :class:`~tempest_fastapi_sdk.genai.ollama.OllamaGenerator`
            builds them. A copy: mutating the caller's list afterwards does
            not change the record.
        schema (type[BaseModel]): The schema the caller asked for.
        config (GenerationConfig | None): The ``config`` argument.
        think (ThinkLevel | None): The ``think`` argument.
        truncate (bool | None): The ``truncate`` argument.
        options (dict[str, Any]): Every other keyword argument.
    """

    method: str
    messages: list[dict[str, Any]]
    schema: type[BaseModel]
    config: GenerationConfig | None = None
    think: ThinkLevel | None = None
    truncate: bool | None = None
    options: dict[str, Any] = field(default_factory=dict)


class FakeStructuredBackend(_Steerable):
    """A structured-output generator that answers with instances you queue.

    Same ``chat_structured`` and ``generate_structured`` signatures as
    :class:`~tempest_fastapi_sdk.genai.ollama.OllamaGenerator`, so it slots
    in wherever a route calls one of the two — typically through
    ``app.dependency_overrides`` — and it satisfies
    :class:`~tempest_fastapi_sdk.genai.StructuredTextBackend`.

    There is no default reply: a schema has no neutral instance to invent,
    and a made-up one would let the route serialise data the test never
    chose. A call with nothing queued raises :class:`LookupError`; a
    queued instance of another class raises :class:`TypeError`. Both are
    raised after the call is recorded, so :attr:`requests` still shows
    what the route asked for.

    Example:

        >>> backend = FakeStructuredBackend()
        >>> backend.queue(Invoice(number="42", total_cents=1990))
        >>> (await backend.chat_structured(messages, Invoice)).number
        '42'

    Attributes:
        requests (list[StructuredRequest]): Every call, in order, with the
            arguments it carried.
        prompts (list[str]): The content of the last turn of each call —
            the document or question the route sent.
        calls (list[str]): Methods that ran, in order.
    """

    def __init__(self) -> None:
        """Start with an empty queue and nothing recorded."""
        super().__init__()
        self._queued: deque[BaseModel] = deque()
        self.requests: list[StructuredRequest] = []
        self.prompts: list[str] = []

    def queue(self, *replies: BaseModel) -> None:
        """Queue instances, consumed one per call, in order.

        Args:
            *replies (BaseModel): The validated instances to hand out. Each
                must be an instance of the schema the matching call asks
                for.
        """
        self._queued.extend(replies)

    async def chat_structured[StructuredT: BaseModel](
        self,
        messages: list[dict[str, Any]],
        schema: type[StructuredT],
        *,
        config: GenerationConfig | None = None,
        think: ThinkLevel | None = None,
        truncate: bool | None = None,
        **kwargs: Any,
    ) -> StructuredT:
        """Answer a message list with the next queued instance.

        Args:
            messages (list[dict[str, Any]]): Chat turns, recorded.
            schema (type[StructuredT]): The schema the reply must be.
            config (GenerationConfig | None): Recorded, never applied.
            think (ThinkLevel | None): Recorded, never applied.
            truncate (bool | None): Recorded, never applied.
            **kwargs (Any): Recorded in :attr:`StructuredRequest.options`.

        Returns:
            StructuredT: The next queued instance.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
            LookupError: When nothing is queued.
            TypeError: When the queued instance is not a ``schema``.
        """
        self._record("chat_structured")
        return self._answer(
            StructuredRequest(
                method="chat_structured",
                messages=[dict(message) for message in messages],
                schema=schema,
                config=config,
                think=think,
                truncate=truncate,
                options=dict(kwargs),
            ),
            schema,
        )

    async def generate_structured[StructuredT: BaseModel](
        self,
        prompt: str,
        schema: type[StructuredT],
        *,
        system: str | None = None,
        config: GenerationConfig | None = None,
        think: ThinkLevel | None = None,
        truncate: bool | None = None,
        **kwargs: Any,
    ) -> StructuredT:
        """Answer a prompt with the next queued instance.

        Args:
            prompt (str): The input text, recorded as a ``user`` turn.
            schema (type[StructuredT]): The schema the reply must be.
            system (str | None): Recorded as a ``system`` turn ahead of
                ``prompt``.
            config (GenerationConfig | None): Recorded, never applied.
            think (ThinkLevel | None): Recorded, never applied.
            truncate (bool | None): Recorded, never applied.
            **kwargs (Any): Recorded in :attr:`StructuredRequest.options`.

        Returns:
            StructuredT: The next queued instance.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
            LookupError: When nothing is queued.
            TypeError: When the queued instance is not a ``schema``.
        """
        self._record("generate_structured")
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self._answer(
            StructuredRequest(
                method="generate_structured",
                messages=messages,
                schema=schema,
                config=config,
                think=think,
                truncate=truncate,
                options=dict(kwargs),
            ),
            schema,
        )

    def _answer[StructuredT: BaseModel](
        self,
        request: StructuredRequest,
        schema: type[StructuredT],
    ) -> StructuredT:
        """Record ``request`` and hand out the next queued instance.

        Args:
            request (StructuredRequest): The call being answered.
            schema (type[StructuredT]): The schema the reply must be.

        Returns:
            StructuredT: The next queued instance.

        Raises:
            LookupError: When nothing is queued.
            TypeError: When the queued instance is not a ``schema``.
        """
        self.requests.append(request)
        last = request.messages[-1].get("content", "") if request.messages else ""
        self.prompts.append(str(last))
        if not self._queued:
            raise LookupError(
                f"FakeStructuredBackend has no reply queued for "
                f"{schema.__name__}; call queue({schema.__name__}(...)) first",
            )
        reply = self._queued.popleft()
        if not isinstance(reply, schema):
            raise TypeError(
                f"FakeStructuredBackend queued a {type(reply).__name__} but "
                f"the call asked for {schema.__name__}",
            )
        return reply


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
