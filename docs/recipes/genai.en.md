---
description: "Index of self-hosted generative AI on your own hardware: points to the subpage for each task (hardware, text, audio, RAG, chat, memory, operations)."
---

# Self-hosted generative AI

Run HuggingFace models on **your own hardware** — no external API, no data
leaving your servers. This page covers the whole `tempest_fastapi_sdk.genai`
module, starting where every adoption starts: knowing, *before* you download
gigabytes of weights, whether the machine can handle the model.

!!! info "How the module was built"
    The module shipped in slices, and the order below is the recommended
    reading path -- each section assumes the ones before it. After v0.108 came
    structured output (v0.142), diarization (v0.223), STT correction (v0.235)
    and the Coqui runtime behind the `[genai-audio]` extra (v0.252), all
    documented in their own sections on this same page.
    - **v0.96:** `genai.hardware` — probing + `can_run` / `recommend`.
    - **v0.97:** `genai.rag` — RAG context (SearXNG web search + PDF
      reading) to inject into LLMs (this page, [RAG context](genai/rag.md)).
    - **v0.98:** `TextGenerator` — local LLM + int4/int8 quantization
      (section [Generate text](genai/text.md#generate-text-with-a-local-llm)).
    - **v0.99:** `Embedder`, `BatchScheduler`, `ModelRegistry` — embeddings
      + scale (section [Embeddings and scale](genai/embeddings.md)).
    - **v0.102:** `SpeechToText` / `TextToSpeech` — audio (section
      [Audio (voice)](genai/audio.md)).
    - **v0.107:** Ollama backend — `OllamaGenerator` / `OllamaEmbedder`,
      a local LLM without torch (section [Ollama backend](genai/ollama.md)).
    - **v0.108:** long-term memory, AI chat pipeline and vision/tools —
      `ChatMemory` / `AIChatPipeline` (sections
      [Long-term memory](genai/memory.md) and
      [AI chat pipeline](genai/chat.md)).

The `[genai]` extra (transformers + torch + accelerate) is only needed to
**run** models. The capacity functions **import without the extra** —
`torch` is only used (when present) to read real GPU VRAM.

## Where each task lives

Use this table to open only the subpage for the task at hand; they are listed in reading order.

| Subpage | What it solves |
| --- | --- |
| [Can the machine handle it? Hardware and estimates](genai/hardware.md) | Use before downloading weights: probe CPU, RAM and GPU (containers included) and estimate whether the model fits. |
| [Generate text (local and hosted)](genai/text.md) | Use to generate text with a local LLM (`TextGenerator`) or an OpenAI-compatible backend (DeepSeek, Groq, OpenRouter, vLLM), with per-call cost and `extra_body`. |
| [Ollama backend](genai/ollama.md) | Use to run LLMs and embeddings through Ollama, without torch: generation, RAG, hybrid search, reranking and the same router swapping torch for Ollama. |
| [Long-term memory](genai/memory.md) | Use when the assistant must remember facts across conversations (`ChatMemory`), in memory or in Postgres with pgvector. |
| [AI chat pipeline](genai/chat.md) | Use to assemble the full chat (history, memory, tools) with `AIChatPipeline`, the ready `make_ai_chat_router` endpoint and streaming. |
| [Embeddings and scale](genai/embeddings.md) | Use to generate embeddings (torch or ONNX), batch concurrent inference and share and inspect the loaded models. |
| [Audio: STT and TTS](genai/audio.md) | Use to transcribe audio (STT) and generate speech (TTS), and to pick the language and voice model by licence and where it runs. |
| [Sound effects and music](genai/sound.md) | Use to generate sound effects and music (MusicGen) and serve the result over HTTP at `/audio`. |
| [RAG context](genai/rag.md) | Use to inject context into the LLM: web search (SearXNG), page-body extraction, PDF reading and RAG over your own corpus. |
| [Typed config and structured output](genai/structured.md) | Use when the model must return a filled-in Pydantic object, and to pin generation parameters with `GenerationConfig`. |
| [Moderation, usage, metrics and cache](genai/operations.md) | Use in production: moderate content, account usage per user, expose metrics and tracing, count tokens and cache generations. |
| [Multimodal vision (local VLM)](genai/vision.md) | Use to ask a locally running vision-language model about an image. |
| [Ready endpoints and Redis cache](genai/router.md) | Use to expose the module over HTTP with `make_genai_router` and share the embedding cache across workers with `RedisEmbeddingCache`. |
| [Who said what: diarization](genai/diarization.md) | Use to split audio by speaker (sherpa-onnx), recognise who it is from a voice profile and serve it over HTTP or the CLI. |

## Recap

- **`GenerationConfig`** — typed, reusable generation parameters instead
  of `**kwargs`.
- **`make_genai_router`** — mounts only the endpoints of the injected
  objects; `/generate/stream` streams tokens over SSE.
- **`RedisEmbeddingCache`** — a shared vector cache; `Embedder` accepts a
  sync or async cache at the same call site.
- **Ollama backend** — `OllamaGenerator` / `OllamaEmbedder` use the same
  surface (router, `Retriever`, `GenerationConfig`) via a local Ollama
  daemon: no torch, no weights, no `load()`; `TextBackend` is the seam for
  other engines.
- **`ChatMemory` / `ChromaVectorStore`** — per-user long-term memory with
  similarity + recency recall (`[genai-chroma]`); `ChromaVectorStore` is a
  persistent `VectorStore` for generic RAG.
- **`PgChatMemory`** — the same memory in a pgvector table of the service's
  Postgres, without `[genai-chroma]`; deletes join your transaction.
- **`AIChatPipeline` / `make_ai_chat_router`** — a full chatbot in-process
  (memory + web RAG + tool-calling + optional TTS); one stateless router
  (`/chat` + `/chat/stream` SSE) kills the inference microservice.
- **`can_run` / `recommend`** — answer whether the host runs the model
  and what to do if not, **before** the download.
- **RAG over a corpus** — `Retriever` + `VectorStore` (`InMemoryVectorStore`
  / `PgVectorStore` pgvector): index chunks once, retrieve top-k by
  similarity.
- **Audio** — `SpeechToText` (faster-whisper) transcribes; `TextToSpeech`
  (Coqui TTS) synthesizes — local voice, end to end.
- **RAG** — `WebSearch`/`SearxngBackend` (search), `ContentExtractor`
  (page bodies), `PdfReader` (PDF → text/chunks) and `build_context`
  (prompt block), all under the `[genai-rag]` extra.
- **`probe_hardware`** — a CPU/RAM/GPU/disk snapshot; degrades without the
  extras.
- **`estimate_model_bytes` / `bytes_per_param`** — the estimation math,
  testable and reusable.
- Everything imports without the `[genai]` extra; install it to actually
  run models (upcoming module slices).
