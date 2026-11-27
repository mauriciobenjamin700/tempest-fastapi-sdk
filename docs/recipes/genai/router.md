# Endpoints prontos e cache Redis

Use para expor o módulo por HTTP com `make_genai_router` e compartilhar o cache de embeddings entre workers com `RedisEmbeddingCache`.

Parte da receita [IA generativa self-hosted](../genai.md).

## `make_genai_router` — endpoints prontos

Injete os objetos que você tem carregados e o router monta **só** os
endpoints correspondentes:

```python
from fastapi import FastAPI
from tempest_fastapi_sdk.genai import Embedder, TextGenerator, make_genai_router

app = FastAPI()
app.include_router(
    make_genai_router(
        text_generator=TextGenerator("Qwen/Qwen2.5-7B-Instruct"),
        embedder=Embedder("sentence-transformers/all-MiniLM-L6-v2"),
    )
)
```

| Objeto | Endpoints |
| --- | --- |
| `text_generator` | `POST /generate`, `POST /generate/stream` (SSE token a token), `POST /chat` |
| `embedder` | `POST /embed` |
| `retriever` | `POST /rag` (query → bloco de contexto) |
| `speech_to_text` | `POST /transcribe` (upload de áudio + campo opcional `prompt` no form, limitado por `max_prompt_chars`; `SpeechToText`, `OllamaSpeech`, `OpenAICompatSpeech` ou qualquer `SpeechTranscriber`) |
| `text_to_speech` | `POST /tts` (devolve `audio/wav`; `TextToSpeech`, `OllamaSpeech` ou qualquer `SpeechSynthesizer`) |

!!! tip "Streaming"
    `/generate/stream` devolve `text/event-stream`: cada token vira um
    evento SSE, encerrando com um evento `done`. Reaproveita o
    `sse_response` do SDK — cliente com `EventSource` recebe os tokens ao
    vivo.

### Limites por request: `GenAIRequestLimits`

Todo endpoint de IA transforma tamanho de request em tempo de GPU ou
memória. Por isso o router confere o request contra um
`GenAIRequestLimits` **antes** de chamar o modelo, e responde `422`
quando algum teto estoura:

| Campo | Default | Onde vale |
| --- | --- | --- |
| `max_prompt_chars` | `32_000` | prompt do `/generate`, soma das mensagens do `/chat`, query do `/rag`, cada texto do `/embed`, prompt e negative prompt do `/image` |
| `max_chat_messages` | `100` | mensagens por `/chat` |
| `max_new_tokens` | `4096` | `config.max_new_tokens` enviado pelo cliente |
| `max_embed_texts` | `256` | textos por `/embed` |
| `max_top_k` | `50` | `top_k` do `/rag` (que também exige `>= 1` no schema) |
| `max_tts_chars` | `5_000` | texto do `/tts` |
| `max_image_side` | `2048` | `config.width` / `config.height` do `/image` |
| `max_image_steps` | `100` | `config.steps` do `/image` |
| `max_upload_bytes` | 25 MiB | upload do `/transcribe`, lido em blocos; este responde `413 UPLOAD_TOO_LARGE`, não `422` |

Os defaults servem um deploy self-hosted pequeno. Para mudar, passe a sua
instância:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.genai import GenAIRequestLimits, TextGenerator, make_genai_router

app: FastAPI = FastAPI()
register_exception_handlers(app)
app.include_router(
    make_genai_router(
        text_generator=TextGenerator("Qwen/Qwen2.5-7B-Instruct"),
        limits=GenAIRequestLimits(max_prompt_chars=8_000, max_new_tokens=1024),
    ),
)
```

Com o `register_exception_handlers`, o corpo do `422` diz qual campo
estourou e qual é o teto:

```text
POST /api/genai/generate {"prompt": "hi", "config": {"max_new_tokens": 1000000000}}
422 {"detail": "max_new_tokens exceeds the limit of 4096", "code": "VALIDATION_ERROR",
     "details": {"field": "max_new_tokens", "limit": 4096}}
```

!!! note "`max_new_tokens` ausente passa"
    O teto vale para o valor que o **cliente** manda. Request sem
    `max_new_tokens` cai no default do gerador, que quem escolheu foi você.

!!! warning "`/image` devolve uma imagem, então pede uma"
    O corpo da resposta é a imagem, então `config.num_images` acima de `1`
    recebe `422` — antes a rota gerava o lote inteiro na GPU e devolvia só a
    primeira. Para lote, use o `ImageGenerator` direto.

### Áudio: o limite é a duração decodificada, não só os bytes

`max_upload_bytes` limita o que o cliente envia, mas não o que o upload vira
depois de decodificado: o cabeçalho do arquivo declara a própria taxa de
amostragem e um codec comprime silêncio a quase nada (dez minutos de FLAC
silencioso, mono a 16 kHz, cabem em ~114 KB no encoder FLAC do PyAV). Por isso o `SpeechToText`, o
`SpeakerDiarizer` e o `VoiceEmbedder` decodificam o áudio pelo mesmo
decodificador limitado, antes de o modelo ver qualquer coisa:

- taxa declarada fora de `MIN_SOURCE_RATE`–`MAX_SOURCE_RATE`
  (8 000–192 000 Hz) é recusada antes de reamostrar;
- áudio decodificado acima de `max_audio_seconds` é recusado assim que o
  contador de amostras passa do limite — o default é
  `DEFAULT_MAX_AUDIO_SECONDS`, uma hora.

As duas recusas são `ValidationException`, então com o
`register_exception_handlers` o `/transcribe` (deste router e do
`make_voice_router`) responde `422`, com `details` trazendo `min_rate` e
`max_rate`, ou `max_seconds`. O modelo do Whisper recebe as amostras já
decodificadas, não o arquivo.

```python
from tempest_fastapi_sdk.genai.audio import SpeakerDiarizer, SpeechToText

stt: SpeechToText = SpeechToText("base", max_audio_seconds=900.0)
diarizer: SpeakerDiarizer = SpeakerDiarizer(max_audio_seconds=900.0)
```

`max_audio_seconds=None` tira o limite de duração (a faixa de taxa continua
valendo) — só para arquivo confiável, que o próprio operador fornece.

## `RedisEmbeddingCache` — cache compartilhado entre workers

`Embedder` aceita cache síncrono (`InMemoryEmbeddingCache`) **ou**
assíncrono. Troque por `RedisEmbeddingCache` para compartilhar vetores
entre processos sem mudar o call site:

```python
import asyncio

from tempest_fastapi_sdk.cache import AsyncRedisManager
from tempest_fastapi_sdk.genai import Embedder, RedisEmbeddingCache

from src.core.settings import settings

redis = AsyncRedisManager(**settings.redis_kwargs())
# no lifespan: await redis.connect()  (antes de acessar .client)

cache = RedisEmbeddingCache(redis.client, ttl_seconds=86400)
embedder = Embedder("sentence-transformers/all-MiniLM-L6-v2", cache=cache)


async def main() -> None:
    """Run this example."""
    await embedder.embed(["texto"])  # 1ª vez calcula; próximos workers reaproveitam


asyncio.run(main())
```

O `Embedder` aguarda `get`/`set` quando o cache é assíncrono e chama
direto quando é síncrono — o mesmo código serve aos dois.

!!! note "Client Redis: `AsyncRedisManager`"
    O `RedisEmbeddingCache` recebe um `redis.asyncio.Redis` cru. Como o embedder
    roda em contexto async (serviço/RAG, não middleware), use o
    `AsyncRedisManager` (client gerenciado do SDK) e passe o `.client` **depois**
    do `await redis.connect()` no lifespan — antes disso, `.client` levanta
    `RuntimeError`. Precisa do extra `[cache]` (o pacote `redis`) além do `[genai]`.
