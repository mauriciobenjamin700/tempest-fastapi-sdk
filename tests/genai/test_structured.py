"""Tests for schema-constrained structured output."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from tempest_fastapi_sdk.genai import OllamaGenerator, parse_structured
from tempest_fastapi_sdk.utils.http_client import HTTPClient


class Person(BaseModel):
    """Tiny schema used across the structured-output tests."""

    name: str
    age: int


class TestParseStructured:
    def test_plain_json(self) -> None:
        person = parse_structured('{"name": "Alice", "age": 30}', Person)
        assert person == Person(name="Alice", age=30)

    def test_markdown_fenced_json(self) -> None:
        text = '```json\n{"name": "Bob", "age": 5}\n```'
        assert parse_structured(text, Person) == Person(name="Bob", age=5)

    def test_json_embedded_in_prose(self) -> None:
        text = 'Here you go: {"name": "Cid", "age": 9}. Hope it helps!'
        assert parse_structured(text, Person) == Person(name="Cid", age=9)

    def test_no_json_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="no JSON object"):
            parse_structured("there is nothing here", Person)

    def test_malformed_json_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="could not parse"):
            parse_structured("{name: Alice, age: }", Person)

    def test_schema_violation_raises_validationerror(self) -> None:
        with pytest.raises(ValidationError):
            parse_structured('{"name": "Alice"}', Person)


@pytest.mark.model
class TestBuildPrefixFnTf5:
    def test_adapter_builds_on_real_tokenizer(self) -> None:
        import torch
        from transformers import AutoTokenizer

        from tempest_fastapi_sdk.genai import build_prefix_allowed_tokens_fn

        tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-0.5B-Instruct")
        fn = build_prefix_allowed_tokens_fn(tokenizer, Person)
        allowed = fn(0, torch.tensor(tokenizer.encode("{")))
        assert isinstance(allowed, list)
        assert allowed


class TestOllamaStructured:
    async def test_sends_format_schema_and_parses_instance(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "message": {"content": '{"name": "Alice", "age": 30}'},
                    "done": True,
                },
            )

        client = HTTPClient(transport=httpx.MockTransport(handler))
        gen = OllamaGenerator("llama3.2", http_client=client)
        result = await gen.generate_structured("Give me a person.", Person)
        await client.aclose()

        assert result == Person(name="Alice", age=30)
        assert captured["url"] == "http://127.0.0.1:11434/api/chat"
        body = captured["body"]
        assert body["format"] == Person.model_json_schema()  # type: ignore[index]


class _CountingTokenizer:
    """A tiny tokenizer that counts how often the vocabulary is decoded.

    Attributes:
        decodes (int): Number of ``decode`` calls so far.
        size (int): Vocabulary size reported by ``len``.
    """

    eos_token_id: int = 0

    def __init__(self, size: int = 8) -> None:
        """Build the tokenizer.

        Args:
            size (int): Vocabulary size.
        """
        self.all_special_ids: list[int] = [0]
        self.decodes = 0
        self.size = size

    def __len__(self) -> int:
        """Return the vocabulary size."""
        return self.size

    def encode(self, text: str) -> list[int]:
        """Encode ``text`` as one id per character (modulo the vocabulary)."""
        return [1 + (ord(c) % (self.size - 1)) for c in text]

    def decode(self, ids: list[int]) -> str:
        """Decode ids to one letter each, counting the call."""
        self.decodes += 1
        return "".join(chr(ord("a") + (i % 26)) for i in ids)


class TestTokenizerDataCache:
    """The vocabulary index is built once per tokenizer, not once per call."""

    def test_second_call_reuses_the_index(self) -> None:
        """Every call used to re-decode the whole vocabulary."""
        pytest.importorskip("lmformatenforcer")
        from tempest_fastapi_sdk.genai.structured import _token_enforcer_tokenizer_data

        tokenizer = _CountingTokenizer()
        first = _token_enforcer_tokenizer_data(tokenizer)
        after_first = tokenizer.decodes
        second = _token_enforcer_tokenizer_data(tokenizer)
        assert after_first > 0
        assert tokenizer.decodes == after_first
        assert second is first

    def test_grown_vocabulary_rebuilds(self) -> None:
        """``add_tokens`` changes ``len(tokenizer)``; the stale index is dropped."""
        pytest.importorskip("lmformatenforcer")
        from tempest_fastapi_sdk.genai.structured import _token_enforcer_tokenizer_data

        tokenizer = _CountingTokenizer()
        first = _token_enforcer_tokenizer_data(tokenizer)
        tokenizer.size = 10
        assert _token_enforcer_tokenizer_data(tokenizer) is not first

    def test_distinct_tokenizers_do_not_share(self) -> None:
        """Two tokenizer objects get two indexes."""
        pytest.importorskip("lmformatenforcer")
        from tempest_fastapi_sdk.genai.structured import _token_enforcer_tokenizer_data

        a = _token_enforcer_tokenizer_data(_CountingTokenizer())
        b = _token_enforcer_tokenizer_data(_CountingTokenizer())
        assert a is not b
