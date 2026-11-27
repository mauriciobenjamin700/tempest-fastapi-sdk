# Moderação, uso, métricas e cache

Use em produção: moderar conteúdo, contabilizar uso por usuário, expor métricas e tracing, contar tokens e cachear gerações.

Parte da receita [IA generativa self-hosted](../genai.md).

## Moderação de conteúdo

Filtre prompts do usuário e saídas do modelo com uma camada de moderação
plugável. Dois backends satisfazem o `ModerationBackend` e devolvem um
`ModerationResult` (`flagged`, `categories`, `score`):

```python
import asyncio

from tempest_fastapi_sdk.genai import RuleModerator

user_input = "Explique PIX em uma frase."


mod = RuleModerator(["palavrão", "termo-proibido"], category="abuso")


async def main() -> None:
    """Run this example."""
    verdict = await mod.check(user_input)
    if verdict.flagged:
        ...   # bloqueie ou anote, conforme a política


asyncio.run(main())
```

`RuleModerator` é dep-free e previsível (block-list whole-word,
case-insensitive) — o piso determinístico. Antes de comparar, termo e
texto passam por NFKC, perdem os caracteres de formatação (espaço de
largura zero, marcas bidi) e são casefolded — então `"se"` + U+200B +
`"cret"` e a grafia fullwidth ainda batem com `"secret"`. "Palavra inteira"
é "sem caractere de palavra colado dos lados", o que faz termo com
pontuação na borda (`"$hit"`, `"c++"`) funcionar. Homóglifo de outro
alfabeto (o `е` cirílico no lugar do `e`) **não** é dobrado: liste essas
grafias no block-list. `ClassifierModerator` roda um
classificador local (ex. `unitary/toxic-bert`) via transformers (`[genai]`),
lazy, com `flagged_labels`/`threshold`. Qualidade PT-BR de modelos de
toxicidade varia — trate o classificador como best-effort e mantenha o
`RuleModerator` como base.

### O classificador: rótulos independentes, o texto inteiro

O `unitary/toxic-bert` é **multi-rótulo**: `toxic`, `obscene` e `insult` são
perguntas de sim/não separadas, e um mesmo texto pode ser as três coisas. O
`ClassifierModerator` lê isso da config do modelo (`problem_type =
"multi_label_classification"`) e pontua cada rótulo com **sigmoid**. Modelo
sem essa declaração continua com softmax; `activation="sigmoid"` ou
`"softmax"` força uma das duas.

Ele também nunca trunca. O texto é cortado em janelas de 64 tokens com
sobreposição (`window_tokens=64`, `window_overlap=16`), cada janela é
classificada, e cada rótulo fica com o maior score entre as janelas:

```python
import asyncio

from tempest_fastapi_sdk.genai import ClassifierModerator, ModerationResult

mod = ClassifierModerator(
    "unitary/toxic-bert",
    flagged_labels=["toxic", "insult"],
    threshold=0.5,
)

prefix = "The weather report says it will be sunny and mild. " * 60
text = prefix + "You are a stupid idiot and I hate you."


async def main() -> None:
    """Modera um texto longo cujo insulto vem depois de 660 tokens inócuos."""
    verdict: ModerationResult = await mod.check(text)
    print(verdict.flagged, verdict.categories)


asyncio.run(main())
```

Saída: `True ['toxic', 'insult']` — o mesmo veredito que a frase recebe
sozinha, 660 tokens depois.

!!! info "Por que sigmoid: o que o softmax fazia"
    Medido com o `unitary/toxic-bert` (revisão `4d6c22e`), os seis rótulos
    em `flagged_labels`, `threshold=0.5`, sobre um conjunto fixo de 10
    frases ofensivas em inglês e 5 limpas:

    | | Sigmoid (agora) | Softmax (antes) |
    | --- | --- | --- |
    | frases ofensivas sinalizadas | 10 de 10 | 10 de 10 |
    | rótulos reportados nelas | 31 (`toxic` + `obscene` + `insult` em todas, `threat` em uma) | 10 (só `toxic`, sempre) |
    | frases limpas sinalizadas | 0 de 5 | 1 de 5 |

    Com softmax os seis scores somam 1, então os rótulos disputam a mesma
    massa: `insult` nunca passou de 0,5 ao lado de `toxic`, e uma política
    chaveada em `insult` não via nada. Falha também no sentido oposto —
    todo score de *"Could you send me the invoice for last month?"* é
    minúsculo, o softmax obriga a soma a dar 1 mesmo assim, e o `toxic`
    saiu em 0,556.

!!! info "Por que janelas de 64 tokens: o modelo dilui"
    Antes, `truncation=True` classificava só os primeiros 512 tokens. Com
    uns 650 tokens inócuos na frente, o mesmo insulto pontuava `toxic` em
    0,001 — prefixar enchimento bastava para passar.

    Janela do tamanho do contexto do modelo (512) não fecha esse buraco
    inteiro, porque o próprio classificador dilui uma frase no meio de muito
    texto benigno: numa janela só, o insulto atrás de 130 tokens de
    enchimento ainda foi pego em 9 de 10 frases, atrás de 260 tokens em 3
    de 10, atrás de 490 tokens em nenhuma. Medido sobre 40 trechos da prosa
    em inglês deste site (de 1801 a 3030 tokens cada), com uma das 10 frases
    inserida numa palavra aleatória (seed 316), `window_overlap` em um
    quarto da janela:

    | `window_tokens` | insulto pego | trecho limpo sinalizado | ms por check |
    | --- | --- | --- | --- |
    | 512 | 1 de 40 | 0 de 40 | 41 |
    | 256 | 6 de 40 | 0 de 40 | 36 |
    | 128 | 23 de 40 | 0 de 40 | 41 |
    | 96 | 28 de 40 | 0 de 40 | 66 |
    | **64** (default) | **36 de 40** | 0 de 40 | 62 |
    | 48 | 39 de 40 | 0 de 40 | 80 |
    | 32 | 40 de 40 | 0 de 40 | 139 |

    A coluna de latência saiu de uma RTX 4070 Ti SUPER num host sob carga
    pesada de outros jobs: leia a razão entre as linhas, não o valor
    absoluto. Texto que cabe numa janela (até 62 tokens aqui) custa a mesma
    passada única de antes.

!!! tip "Quando mudar a janela"
    Janela mais curta vê menos da frase em volta de cada palavra. A tabela
    acima não mede o que isso custa para uma política que depende de
    contexto (ironia, citação, negação) — se a sua depende, valide um
    `window_tokens` maior nos seus dados. `window_tokens=None` usa o
    contexto inteiro do modelo, e qualquer valor é limitado a ele (512 no
    BERT).

## Contabilidade de uso por usuário (tabela)

O `GenAIMetrics` acima responde "como está a frota agora". Ele **não**
responde "qual conta queimou o orçamento no mês passado": a série é por
processo, zera no deploy, e não tem dimensão de usuário — acrescentar uma
tornaria a cardinalidade inviável.

Para essa pergunta a forma é outra: **uma linha por chamada paga**, numa
tabela que você consulta com SQL comum.

```python
# src/services/ai_usage.py
from uuid import UUID

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.genai import AIUsageStore, BaseAIUsageModel, TokenUsage


class AIUsageModel(BaseAIUsageModel):
    """Uma chamada de IA cobrada desta aplicação."""

    __tablename__ = "ai_usage"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
store: AIUsageStore[AIUsageModel] = AIUsageStore(
    db,
    model=AIUsageModel,
    price_input_per_1k=0.00014,
    price_output_per_1k=0.00028,
)


async def registrar(user_id: UUID, usage: TokenUsage | None) -> None:
    """Grava o consumo de uma chamada.

    Args:
        user_id (UUID): Quem pagou.
        usage (TokenUsage | None): O que o provedor reportou.
    """
    await store.record(subject_id=user_id, service="summary", usage=usage)
```

E as agregações que uma tela de admin desenha:

```python
# src/services/ai_usage.py
from datetime import timedelta

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.genai import (
    AIUsageStore,
    BaseAIUsageModel,
    ServiceUsage,
    SubjectUsage,
    UsageTotals,
)


class AIUsageModel(BaseAIUsageModel):
    """Uma chamada de IA cobrada desta aplicação."""

    __tablename__ = "ai_usage"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
store: AIUsageStore[AIUsageModel] = AIUsageStore(db, model=AIUsageModel)


async def painel() -> tuple[UsageTotals, list[ServiceUsage], list[SubjectUsage]]:
    """Lê o que a tela de admin mostra.

    Returns:
        tuple[UsageTotals, list[ServiceUsage], list[SubjectUsage]]: Totais,
        distribuição por serviço e quem mais gastou.
    """
    janela = timedelta(days=14)
    return (
        await store.totals(janela),
        await store.by_service(janela),
        await store.top_subjects(janela, limit=20),
    )
```

!!! danger "Chamada sem preço do provedor não vira linha"
    `record(usage=None)` grava **nada**. "O provedor não disse" é diferente
    de "a chamada foi de graça": uma linha zerada contaria para o total de
    chamadas e para "usuários ativos" sem contribuir token nenhum, o que é
    pior do que não contar. `TokenUsage(0, 0, 0)` também não grava — é o
    que um atalho que nunca chamou o modelo produz.

!!! info "O preço nunca é gravado"
    O custo é calculado a partir dos tokens na hora da leitura, então
    corrigir o preço conserta o histórico inteiro — sem reprocessar nada,
    sem linhas discordando sobre quanto valia um token.

!!! warning "Custo não vem arredondado"
    Qualquer precisão fixa erra em alguma escala: preço de token vive perto
    de `0.0001` por 1000, então arredondar para centavos zera quase toda
    chamada isolada, enquanto um total mensal quer centavos. A formatação
    fica na borda, que sabe qual dos dois está mostrando. `cost is None`
    significa "não mostre custo", nunca zero.

!!! tip "Prefixo em cache tem preço próprio"
    `price_cache_hit_per_1k` é o preço reduzido dos tokens que o provedor
    serviu de um prefixo em cache, e `estimate_cost` cobra a fatia
    `cache_hit_tokens` por ele em vez do preço de entrada.

    O default é `None`, não `0.0`, e isso importa: `0.0` **é** um preço, e
    um store sem essa configuração passaria a dar o prefixo de graça,
    subestimando toda chamada repetida. `None` significa "não há tarifa
    separada" e deixa a fatia no preço de entrada normal — configuração
    ausente nunca deveria mexer num número.

    A coluna `cache_hit_tokens` é nova em `BaseAIUsageModel`: gere a
    migration (`tempest db revision`) antes de subir. É nullable de
    propósito — as linhas gravadas antes dela não sabem o próprio split, e
    afirmar zero ali cobraria uma chamada com desconto pelo preço cheio.

!!! tip "Inferência local se registra por duração"
    `record_duration(subject_id=..., seconds=...)` é para modelo que roda
    no seu hardware: não há conta de token, o que se consome é relógio.
    Essas linhas ficam com `service=NULL` e são excluídas das somas de
    token, para não virarem uma fatia de 0% em todo gráfico.

## Métricas de inferência (Prometheus)

`GenAIMetrics` empacota os contadores + histograma que todo serviço de
inferência acaba reimplementando — requests, latência e tokens in/out,
rotulados por modelo e operação, e requests + latência também pelo
resultado (`status="ok"` ou `"error"`). Reusa o `prometheus-client` (extra
`[prometheus]`) e aceita um `registry` explícito (compõe com o
`PrometheusMiddleware`/`/metrics` do SDK). É **opt-in**:

```python
import asyncio

from tempest_fastapi_sdk.genai import GenAIMetrics, OllamaGenerator

metrics = GenAIMetrics()
gen = OllamaGenerator("llama3.2", metrics=metrics)


async def main() -> None:
    """Run this example."""
    await gen.generate("Explique PIX.")   # registra request + latência + tokens


asyncio.run(main())
```

!!! warning "O rótulo `status` é novo"
    Antes, uma chamada que levantava contava como request comum — um
    backend falhando parecia tráfego saudável. Agora `genai_requests_total`
    e `genai_request_seconds` carregam `status`; seletor por
    `{model, op}` continua casando, mas query que compara a série inteira
    (ou `get_sample_value` com o conjunto exato de rótulos) precisa somar
    por `status` ou filtrar `status="ok"`.

`OllamaGenerator`, `TextGenerator` e `Embedder` aceitam `metrics=` e
registram request + latência (o Ollama também extrai
`prompt_eval_count`/`eval_count` da resposta pros contadores de token). Pra
qualquer outra chamada, envolva com o context manager e informe os tokens
que souber:

```python
import asyncio

from tempest_fastapi_sdk.genai import GenAIMetrics

metrics = GenAIMetrics()


async def main() -> None:
    """Run this example."""
    async with metrics.track("meu-modelo", "generate") as span:
        span.tokens_out = 128
        ...  # roda o modelo


asyncio.run(main())
```

## Tracing distribuído (OpenTelemetry)

Além das métricas, as chamadas genai emitem **spans OpenTelemetry** — e sem
configuração por gerador. Chame `setup_tracing` uma vez no startup (extra
`[otel]`) e os spans passam a fluir junto com os do FastAPI/SQLAlchemy/httpx:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.api.tracing import setup_tracing

app: FastAPI = FastAPI()
setup_tracing(app, service_name="meu-servico", otlp_endpoint="localhost:4317")
```

A partir daí, `TextGenerator`/`OllamaGenerator` (`generate`/`chat`),
`Embedder.embed` e o `Retriever.search`/`.retrieve` do RAG abrem um span
automaticamente. Os spans seguem as **convenções semânticas GenAI** do
OpenTelemetry (`gen_ai.system`, `gen_ai.operation.name`, `gen_ai.request.model`
e, no caminho Ollama, `gen_ai.usage.input_tokens`/`gen_ai.usage.output_tokens`);
uma exceção marca o span como `ERROR` e a registra.

!!! info "Zero-config, zero-custo por padrão"
    É **ambiente**: reusa o `TracerProvider` global do `setup_tracing`. Sem o
    extra `[otel]` o helper vira no-op; com o extra mas sem provider
    configurado, o tracer global gera spans não-gravados. Nada é injetado nos
    geradores.

Pra instrumentar uma chamada própria, use o mesmo context manager:

```python
import asyncio

from tempest_fastapi_sdk.genai import genai_span


async def main() -> None:
    """Run this example."""
    async with genai_span("generate", "meu-modelo") as span:
        span.tokens_out = 128
        ...  # roda o modelo


asyncio.run(main())
```

## Contagem de tokens e janela de contexto

Pra caber um chat na janela do modelo, conte tokens com o **tokenizer do
próprio modelo** (nunca heurística — BPE e SentencePiece divergem) e dropе
os turnos mais antigos quando estoura:

```python
from transformers import AutoTokenizer

from tempest_fastapi_sdk.genai import (
    TextModelEnum,
    count_tokens,
    truncate_messages,
)

messages = [{"role": "user", "content": "oi"}]
tokenizer = AutoTokenizer.from_pretrained(TextModelEnum.QWEN2_5_7B_INSTRUCT)


n = count_tokens("Explique PIX.", tokenizer)   # tokenizer do próprio modelo

fit = truncate_messages(
    messages, max_tokens=3000, tokenizer=tokenizer,
)   # mantém system + último turno, dropa os mais antigos
```

`count_message_tokens(messages, tokenizer, per_message_overhead=4)` soma o
custo do chat; `truncate_messages` preserva os `system` (movidos pra frente)
e o turno mais recente, dropando os antigos até caber. Um turno `assistant`
com `tool_calls` e os turnos `tool` que o respondem saem **juntos** — o
histórico nunca começa com um resultado de ferramenta cuja chamada foi
cortada. Funcionam sobre qualquer tokenizer com `encode(text) -> sequência`
(o `AutoTokenizer` serve).
## Cache de geração (prompt → completion)

Gerações **determinísticas** (greedy, ou `temperature=0`) produzem sempre o
mesmo texto pro mesmo prompt+params — então dá pra cachear e pular o modelo
numa repetição. Passe um cache no gerador; só chamadas determinísticas são
cacheadas (sampling nunca, pra não devolver amostra velha):

```python
import asyncio

from tempest_fastapi_sdk.genai import (
    GenerationConfig,
    InMemoryGenerationCache,
    OllamaGenerator,
)

gen = OllamaGenerator("llama3.2", generation_cache=InMemoryGenerationCache())
cfg = GenerationConfig(temperature=0)   # determinístico → cacheável


async def main() -> None:
    """Run this example."""
    await gen.generate("Explique PIX.", config=cfg)   # roda o modelo
    await gen.generate("Explique PIX.", config=cfg)   # servido do cache


asyncio.run(main())
```

`InMemoryGenerationCache` é local ao processo e um LRU de 1024 completions
por padrão (`max_entries=`; `None` desliga o limite); `RedisGenerationCache`
(cache `[cache]`) compartilha entre workers — o gerador dá `await` no
sync-ou-async no mesmo call site. Funciona igual no `TextGenerator`
(`generation_cache=...`) e no `chat` dos dois. Invalide removendo a chave
(ou via TTL no Redis).

A chave separa o tipo de chamada (`chat` não responde um `generate` cujo
prompt é o JSON das mesmas mensagens) e, no `TextGenerator`, a identidade
dos pesos: `revision` e `quantization` entram na chave, então duas
instâncias do mesmo `model_id` carregadas diferente nunca leem o cache uma
da outra. Um `generate` sem `revision` nem `quantization` mantém a chave de
antes, então um Redis já quente continua quente.
