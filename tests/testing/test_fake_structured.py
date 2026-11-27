"""``FakeStructuredBackend``: structured output without a model.

The contract half (signature against ``OllamaGenerator``) lives in
``test_fakes_contract.py``. Here: queued instances come back in order, every
call is recorded with what the route sent, and the two ways a test can be
wrong — nothing queued, or the wrong schema queued — fail loudly instead of
returning something the route then serialises.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from tempest_fastapi_sdk.genai import GenerationConfig, StructuredTextBackend
from tempest_fastapi_sdk.testing.fakes import (
    FakeStructuredBackend,
    StructuredRequest,
)


class Invoice(BaseModel):
    """The schema a route asks the model to fill."""

    number: str
    total_cents: int


class Contact(BaseModel):
    """A second schema, to queue against the wrong call."""

    email: str


class TestFakeStructuredBackend:
    async def test_chat_structured_returns_the_queued_instances_in_order(
        self,
    ) -> None:
        backend = FakeStructuredBackend()
        first = Invoice(number="1", total_cents=100)
        second = Invoice(number="2", total_cents=200)
        backend.queue(first, second)

        messages = [{"role": "user", "content": "nota 1"}]
        assert await backend.chat_structured(messages, Invoice) is first
        assert await backend.chat_structured(messages, Invoice) is second

    async def test_generate_structured_builds_the_turns_the_real_one_sends(
        self,
    ) -> None:
        """``system`` becomes its own turn ahead of the prompt, as in Ollama."""
        backend = FakeStructuredBackend()
        backend.queue(Invoice(number="7", total_cents=700))

        reply = await backend.generate_structured(
            "nota 7",
            Invoice,
            system="Extraia a nota.",
            think="low",
        )

        assert reply.number == "7"
        assert backend.prompts == ["nota 7"]
        assert backend.calls == ["generate_structured"]
        assert backend.requests == [
            StructuredRequest(
                method="generate_structured",
                messages=[
                    {"role": "system", "content": "Extraia a nota."},
                    {"role": "user", "content": "nota 7"},
                ],
                schema=Invoice,
                config=None,
                think="low",
                truncate=None,
                options={},
            ),
        ]

    async def test_every_argument_of_a_chat_call_is_recorded(self) -> None:
        backend = FakeStructuredBackend()
        backend.queue(Invoice(number="1", total_cents=1))
        config = GenerationConfig(temperature=0.0)
        messages = [
            {"role": "system", "content": "Extraia."},
            {"role": "user", "content": "documento"},
        ]

        await backend.chat_structured(
            messages,
            Invoice,
            config=config,
            truncate=False,
            num_ctx=16384,
        )
        messages.append({"role": "user", "content": "depois"})

        request = backend.requests[0]
        assert request.method == "chat_structured"
        assert request.schema is Invoice
        assert request.config is config
        assert request.truncate is False
        assert request.options == {"num_ctx": 16384}
        assert len(request.messages) == 2, "the record is a copy"
        assert backend.prompts == ["documento"]

    async def test_an_empty_queue_raises_naming_the_schema(self) -> None:
        """A forgotten ``queue`` is a test bug, not a reply to invent."""
        backend = FakeStructuredBackend()

        with pytest.raises(LookupError, match="Invoice"):
            await backend.chat_structured([], Invoice)

        assert backend.requests[0].schema is Invoice

    async def test_a_reply_of_another_schema_raises(self) -> None:
        """Handing a ``Contact`` to a route that asked for ``Invoice`` fails."""
        backend = FakeStructuredBackend()
        backend.queue(Contact(email="a@b.c"))

        with pytest.raises(TypeError, match=r"Contact.*Invoice"):
            await backend.chat_structured([], Invoice)

    async def test_fail_next_raises_before_anything_is_consumed(self) -> None:
        backend = FakeStructuredBackend()
        queued = Invoice(number="1", total_cents=1)
        backend.queue(queued)
        backend.fail_next(ValueError("model answered with no content"))

        with pytest.raises(ValueError, match="no content"):
            await backend.chat_structured([], Invoice)

        assert await backend.chat_structured([], Invoice) is queued

    def test_it_satisfies_the_structured_backend_protocol(self) -> None:
        assert isinstance(FakeStructuredBackend(), StructuredTextBackend)
