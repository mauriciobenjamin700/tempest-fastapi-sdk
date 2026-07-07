"""Tests for the Ollama text/embedding backend."""

from __future__ import annotations

import json

import httpx

from tempest_fastapi_sdk.genai import (
    GenerationConfig,
    OllamaEmbedder,
    OllamaGenerator,
    TextBackend,
)
from tempest_fastapi_sdk.genai.ollama import _build_options
from tempest_fastapi_sdk.genai.rag import SupportsEmbed


class TestBuildOptions:
    def test_maps_hf_names_to_ollama(self) -> None:
        config = GenerationConfig(
            max_new_tokens=128,
            temperature=0.2,
            top_p=0.8,
            top_k=40,
            repetition_penalty=1.1,
        )
        options = _build_options(config, {})
        assert options == {
            "num_predict": 128,
            "temperature": 0.2,
            "top_p": 0.8,
            "top_k": 40,
            "repeat_penalty": 1.1,
        }

    def test_carries_seed_and_stop(self) -> None:
        config = GenerationConfig(seed=7, stop=["END", "STOP"])
        options = _build_options(config, {})
        assert options["seed"] == 7
        assert options["stop"] == ["END", "STOP"]

    def test_greedy_when_do_sample_false(self) -> None:
        options = _build_options(None, {"do_sample": False})
        assert options["temperature"] == 0.0

    def test_explicit_temperature_survives_greedy_flag(self) -> None:
        options = _build_options(None, {"do_sample": False, "temperature": 0.9})
        assert options["temperature"] == 0.9

    def test_overrides_win_over_config(self) -> None:
        config = GenerationConfig(max_new_tokens=128)
        options = _build_options(config, {"max_new_tokens": 512})
        assert options["num_predict"] == 512


class TestOllamaGenerator:
    async def test_generate_posts_prompt_and_parses_response(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"response": "PIX is instant.", "done": True},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gen = OllamaGenerator("llama3.2", http_client=client)
        text = await gen.generate(
            "What is PIX?",
            config=GenerationConfig(temperature=0.3),
        )
        await client.aclose()

        assert text == "PIX is instant."
        assert captured["url"] == "http://127.0.0.1:11434/api/generate"
        body = captured["body"]
        assert body["model"] == "llama3.2"
        assert body["prompt"] == "What is PIX?"
        assert body["stream"] is False
        assert body["options"] == {"temperature": 0.3}

    async def test_chat_posts_messages_and_parses_reply(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "message": {"role": "assistant", "content": "Olá!"},
                    "done": True,
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gen = OllamaGenerator("llama3.2", http_client=client)
        messages = [{"role": "user", "content": "Oi"}]
        reply = await gen.chat(messages)
        await client.aclose()

        assert reply == "Olá!"
        assert captured["url"] == "http://127.0.0.1:11434/api/chat"
        assert captured["body"]["messages"] == messages

    async def test_stream_yields_pieces_until_done(self) -> None:
        lines = [
            json.dumps({"response": "Hel", "done": False}),
            json.dumps({"response": "lo", "done": False}),
            json.dumps({"response": "", "done": True}),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="\n".join(lines))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gen = OllamaGenerator("llama3.2", http_client=client)
        pieces = [piece async for piece in gen.stream("hi")]
        await client.aclose()

        assert pieces == ["Hel", "lo"]

    async def test_keep_alive_forwarded(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"response": "ok", "done": True})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gen = OllamaGenerator("llama3.2", http_client=client, keep_alive="5m")
        await gen.generate("x")
        await client.aclose()

        assert captured["body"]["keep_alive"] == "5m"

    def test_satisfies_text_backend_protocol(self) -> None:
        assert isinstance(OllamaGenerator("llama3.2"), TextBackend)

    def test_base_url_trailing_slash_trimmed(self) -> None:
        gen = OllamaGenerator("llama3.2", base_url="http://host:11434/")
        assert gen.base_url == "http://host:11434"


class TestOllamaEmbedder:
    async def test_embed_posts_input_and_parses_vectors(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        emb = OllamaEmbedder("nomic-embed-text", http_client=client)
        vectors = await emb.embed(["a", "b"])
        await client.aclose()

        assert vectors == [[0.1, 0.2], [0.3, 0.4]]
        assert captured["url"] == "http://127.0.0.1:11434/api/embed"
        assert captured["body"] == {"model": "nomic-embed-text", "input": ["a", "b"]}

    async def test_single_text_wrapped_in_list(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["input"] == ["solo"]
            return httpx.Response(200, json={"embeddings": [[1.0]]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        emb = OllamaEmbedder("nomic-embed-text", http_client=client)
        vectors = await emb.embed("solo")
        await client.aclose()

        assert vectors == [[1.0]]

    async def test_batches_respect_batch_size(self) -> None:
        batches: list[list[str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            batches.append(body["input"])
            return httpx.Response(
                200,
                json={"embeddings": [[0.0] for _ in body["input"]]},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        emb = OllamaEmbedder("nomic-embed-text", http_client=client)
        vectors = await emb.embed(["a", "b", "c"], batch_size=2)
        await client.aclose()

        assert batches == [["a", "b"], ["c"]]
        assert len(vectors) == 3

    async def test_empty_input_returns_empty(self) -> None:
        emb = OllamaEmbedder("nomic-embed-text")
        assert await emb.embed([]) == []

    def test_satisfies_supports_embed_protocol(self) -> None:
        assert isinstance(OllamaEmbedder("nomic-embed-text"), SupportsEmbed)
