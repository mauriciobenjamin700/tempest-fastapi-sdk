# Exemplos de GenAI — fluxos completos

A [receita de IA generativa](recipes/genai.md) documenta cada peça; aqui
elas se combinam em **fluxos reais**, self-hosted de ponta a ponta.
Instale os extras conforme o exemplo:
`uv add "tempest-fastapi-sdk[genai,genai-rag,genai-audio]"`.

## 1. Carregar o modelo só se couber

Antes de baixar gigabytes, cheque; deixe o SDK escolher a precisão que
cabe:

```python
from tempest_fastapi_sdk.genai import TextGenerator, recommend

MODEL = "Qwen/Qwen2.5-7B-Instruct"
rec = recommend(model_id=MODEL)          # tenta bf16 -> int8 -> int4

if not rec.fits:
    raise RuntimeError(rec.reason + " " + (rec.suggestion or ""))

gen = TextGenerator(
    MODEL,
    device=rec.device,
    quantization=rec.dtype.value if rec.dtype.value in ("int8", "int4") else None,
    idle_unload_seconds=300,             # libera VRAM entre picos
)
answer = await gen.generate("Explique PIX em uma frase.")
print(answer)
```

## 2. RAG sobre uma base de PDFs

Indexe os PDFs uma vez, responda perguntas fundamentadas depois. Aqui em
memória (dev); troque por `PgVectorStore(db, dim=384)` em produção.

```python
from tempest_fastapi_sdk.genai import Embedder, TextGenerator
from tempest_fastapi_sdk.genai.rag import InMemoryVectorStore, PdfReader, Retriever

rag = Retriever(
    Embedder("sentence-transformers/all-MiniLM-L6-v2", normalize=True),
    InMemoryVectorStore(),
)
gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct", quantization="int4")

# indexação (uma vez, na subida do serviço ou num job)
for pdf in ("manual.pdf", "faq.pdf", "politicas.pdf"):
    await rag.index(PdfReader().chunks(f"/kb/{pdf}", max_chars=1500, overlap=150))

# consulta (barata, por request)
async def answer(question: str) -> str:
    context = await rag.retrieve(question, top_k=5)
    prompt = f"{context}\n\nResponda só com base nas fontes acima.\n{question}"
    return await gen.generate(prompt, max_new_tokens=400)
```

## 3. Resposta fundamentada na web (SearXNG)

Sem base própria — busque na web, extraia e responda:

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

## 4. Assistente de voz 100% local

O loop completo: **áudio → texto → RAG/LLM → texto → áudio**. Nada sai da
máquina.

```python
from tempest_fastapi_sdk.genai import TextGenerator
from tempest_fastapi_sdk.genai.audio import Language, SpeechToText, TextToSpeech

stt = SpeechToText("base")
gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct", quantization="int4")
tts = TextToSpeech.for_language(Language.PT_BR)


async def voice_turn(audio_wav_path: str) -> bytes:
    """Recebe fala, devolve fala."""
    heard = await stt.transcribe(audio_wav_path, language=Language.PT_BR)
    reply = await gen.chat([
        {"role": "system", "content": "Você é um assistente conciso em PT-BR."},
        {"role": "user", "content": heard.text},
    ])
    return await tts.synthesize(reply)          # WAV bytes
```

Junte com [RAG](#2-rag-sobre-uma-base-de-pdfs): troque `gen.chat(...)` por
`gen.generate(await rag.retrieve(heard.text))` pra um assistente de voz
que responde sobre seus PDFs.

## 5. Endpoint FastAPI de transcrição

```python
from fastapi import APIRouter, UploadFile

from tempest_fastapi_sdk.genai.audio import Language, SpeechToText

router = APIRouter()
stt = SpeechToText("base")                      # carregue no lifespan em prod


@router.post("/transcribe")
async def transcribe(file: UploadFile) -> dict[str, object]:
    result = await stt.transcribe(await file.read(), language=Language.PT_BR)
    return {"text": result.text, "duration": result.duration}
```

## Escala e economia

- **Batch**: envolva `embedder._embed_many` num `BatchScheduler` pra
  coalescer embeddings concorrentes num forward pass só.
- **Compartilhar modelos**: `ModelRegistry(max_models=2)` reusa modelos
  carregados entre call sites e descarrega o LRU.
- **Liberar VRAM**: `idle_unload_seconds` + `gen.unload_if_idle()` num
  `@tq.interval(60)` do [TaskQueue](recipes/queue-tasks.md).
- **Cache de embeddings**: `Embedder(cache=...)` pula o modelo em texto já
  visto.

Referência completa de cada peça: [IA generativa self-hosted](recipes/genai.md).
