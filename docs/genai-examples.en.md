# GenAI examples — complete flows

The [generative-AI recipe](recipes/genai.md) documents each piece; here
they combine into **real flows**, self-hosted end to end. Install the
extras per example:
`uv add "tempest-fastapi-sdk[genai,genai-rag,genai-audio]"`.

## 1. Load the model only if it fits

Before downloading gigabytes, check; let the SDK pick the precision that
fits:

```python
from tempest_fastapi_sdk.genai import TextGenerator, recommend

MODEL = "Qwen/Qwen2.5-7B-Instruct"
rec = recommend(model_id=MODEL)          # tries bf16 -> int8 -> int4

if not rec.fits:
    raise RuntimeError(rec.reason + " " + (rec.suggestion or ""))

gen = TextGenerator(
    MODEL,
    device=rec.device,
    quantization=rec.dtype.value if rec.dtype.value in ("int8", "int4") else None,
    idle_unload_seconds=300,             # free VRAM between bursts
)
answer = await gen.generate("Explain PIX in one sentence.")
print(answer)
```

## 2. RAG over a PDF knowledge base

Index the PDFs once, answer grounded questions afterwards. In-memory here
(dev); swap for `PgVectorStore(db, dim=384)` in production.

```python
from tempest_fastapi_sdk.genai import Embedder, TextGenerator
from tempest_fastapi_sdk.genai.rag import InMemoryVectorStore, PdfReader, Retriever

rag = Retriever(
    Embedder("sentence-transformers/all-MiniLM-L6-v2", normalize=True),
    InMemoryVectorStore(),
)
gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct", quantization="int4")

# indexing (once, at startup or in a job)
for pdf in ("manual.pdf", "faq.pdf", "policies.pdf"):
    await rag.index(PdfReader().chunks(f"/kb/{pdf}", max_chars=1500, overlap=150))

# querying (cheap, per request)
async def answer(question: str) -> str:
    context = await rag.retrieve(question, top_k=5)
    prompt = f"{context}\n\nAnswer only from the sources above.\n{question}"
    return await gen.generate(prompt, max_new_tokens=400)
```

## 3. Web-grounded answer (SearXNG)

No own corpus — search the web, extract, answer:

```python
import httpx

from tempest_fastapi_sdk.genai import TextGenerator
from tempest_fastapi_sdk.genai.rag import ContentExtractor, SearxngBackend, WebSearch

client = httpx.AsyncClient()
search = WebSearch(SearxngBackend("http://localhost:8080", http_client=client))
extractor = ContentExtractor(http_client=client)
gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct", quantization="int4")


async def answer_from_web(question: str) -> str:
    context = await search.retrieve(question, extractor=extractor, max_results=5)
    return await gen.generate(f"{context}\n\n{question}", max_new_tokens=400)
```

## 4. Fully local voice assistant

The full loop: **audio → text → RAG/LLM → text → audio**. Nothing leaves
the box.

```python
from tempest_fastapi_sdk.genai import TextGenerator
from tempest_fastapi_sdk.genai.audio import Language, SpeechToText, TextToSpeech

stt = SpeechToText("base")
gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct", quantization="int4")
tts = TextToSpeech.for_language(Language.EN_US)


async def voice_turn(audio_wav_path: str) -> bytes:
    """Take speech, return speech."""
    heard = await stt.transcribe(audio_wav_path, language=Language.EN_US)
    reply = await gen.chat([
        {"role": "system", "content": "You are a concise assistant."},
        {"role": "user", "content": heard.text},
    ])
    return await tts.synthesize(reply)          # WAV bytes
```

Combine with [RAG](#2-rag-over-a-pdf-knowledge-base): swap `gen.chat(...)`
for `gen.generate(await rag.retrieve(heard.text))` to get a voice
assistant that answers about your PDFs.

## 5. FastAPI transcription endpoint

```python
from fastapi import APIRouter, UploadFile

from tempest_fastapi_sdk.genai.audio import Language, SpeechToText

router = APIRouter()
stt = SpeechToText("base")                      # load in the lifespan in prod


@router.post("/transcribe")
async def transcribe(file: UploadFile) -> dict[str, object]:
    result = await stt.transcribe(await file.read(), language=Language.EN_US)
    return {"text": result.text, "duration": result.duration}
```

## Scale and economy

- **Batch**: wrap `embedder._embed_many` in a `BatchScheduler` to coalesce
  concurrent embeddings into one forward pass.
- **Share models**: `ModelRegistry(max_models=2)` reuses loaded models
  across call sites and unloads the LRU.
- **Free VRAM**: `idle_unload_seconds` + `gen.unload_if_idle()` in a
  `@tq.interval(60)` [TaskQueue](recipes/queue-tasks.md) task.
- **Embedding cache**: `Embedder(cache=...)` skips the model for text
  already seen.

Full reference for each piece: [Self-hosted generative AI](recipes/genai.md).
