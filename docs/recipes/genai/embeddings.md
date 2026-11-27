# Embeddings e escala

Use para gerar embeddings (torch ou ONNX), agrupar inferência concorrente em batch e compartilhar e inspecionar os modelos carregados.

Parte da receita [IA generativa self-hosted](../genai.md).

## Gerar embeddings

`Embedder` transforma texto em vetores no seu hardware (busca semântica,
RAG, clustering). Carrega o modelo uma vez, faz batch e (opcional) cacheia
vetor por texto — cache hit nem toca no modelo.

```python
import asyncio

from tempest_fastapi_sdk.genai import Embedder, InMemoryEmbeddingCache

emb = Embedder(
    "sentence-transformers/all-MiniLM-L6-v2",
    cache=InMemoryEmbeddingCache(),     # ou um wrapper Redis (get/set)
)


async def main() -> None:
    """Run this example."""
    vetores = await emb.embed(["o que é pix?", "como estornar?"])   # list[list[float]]


asyncio.run(main())
```

O `cache` é qualquer objeto com `get(key)->list|None` e `set(key, val)` —
passe um wrapper sobre o `AsyncRedisManager` pra compartilhar entre
workers. O `InMemoryEmbeddingCache` é um LRU de 1024 vetores por padrão
(`max_entries=`; `None` desliga o limite), então um worker de vida longa
não cresce pra sempre. `device`/`dtype`/`unload`/`unload_if_idle` funcionam como no
`TextGenerator`.

Pra busca semântica, use `normalize=True` (vetores unitários) + a função
`cosine_similarity`:

```python
import asyncio

from tempest_fastapi_sdk.genai import Embedder, cosine_similarity


emb = Embedder("sentence-transformers/all-MiniLM-L6-v2", normalize=True)


async def main() -> None:
    """Run this example."""
    q, *docs = await emb.embed(["pergunta", "doc a", "doc b"])
    ranked = sorted(docs, key=lambda d: cosine_similarity(q, d), reverse=True)


asyncio.run(main())
```

## Embeddings ONNX (sem torch)

Se você não quer a stack pesada do `torch`/`transformers` só pra embeddar,
`OnnxEmbedder` roda um modelo de embedding exportado pra ONNX via ONNX
Runtime — dependências leves (`onnxruntime` + `tokenizers`, extra
`[genai-onnx]`), CPU-barato. Satisfaz o mesmo `SupportsEmbed`, então entra
no `Retriever` / `make_genai_router` sem mudar nada.

```python
import asyncio

from tempest_fastapi_sdk.genai import OnnxEmbedder

emb = OnnxEmbedder(
    "all-MiniLM-L6-v2.onnx",
    tokenizer="sentence-transformers/all-MiniLM-L6-v2",
    normalize=True,
)


async def main() -> None:
    """Run this example."""
    vectors = await emb.embed(["pergunta", "doc a"])


asyncio.run(main())
```

O pooling default é a **média ponderada pela attention mask** dos embeddings
de token (não uma média ingênua sobre padding), então os vetores batem com os
do `Embedder` torch (cosseno ≈ 1.0 pro mesmo modelo). Modelos da família BGE
são treinados no token `[CLS]` — passe `pooling="cls"` pra eles, conforme o
model card. Um grafo exportado já com a cabeça de pooling (saída 2-D
`sentence_embedding`) é usado como veio, e o padding usa o `<pad>` do próprio
tokenizer (id 1 no RoBERTa/XLM-R), não o id 0 que o `tokenizers` assume. Exporte o modelo em ambiente descartável — `optimum` **não** é dependência
deste pacote, porque ela prende `transformers<4.58` no lock de quem instalar:

```bash
uvx --from "optimum[onnxruntime]" optimum-cli export onnx \
    --model sentence-transformers/all-MiniLM-L6-v2 \
    --task feature-extraction exports/minilm
```

Depois aponte `model_path` para o `.onnx` gerado.

## Batch de inferência concorrente

Numa GPU, rodar um item por vez desperdiça o device. `BatchScheduler`
coalesce chamadas concorrentes num lote só — cada chamador ainda dá
`await` no seu próprio resultado:

```python
import asyncio

from tempest_fastapi_sdk.genai import BatchScheduler, Embedder, EmbeddingModelEnum

emb = Embedder(EmbeddingModelEnum.ALL_MINILM_L6_V2)


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a whole batch in one forward pass."""
    return await emb.embed(texts)


sched = BatchScheduler(embed_batch, max_batch=32, max_wait_ms=10)


async def main() -> None:
    """Run this example."""
    # N requests concorrentes viram 1 forward pass:
    vetor = await sched.submit("texto")
    await sched.aclose()


asyncio.run(main())
```

Forma um lote quando junta `max_batch` itens **ou** passa `max_wait_ms`
desde o primeiro — o que vier antes. Erro do handler propaga pra todos os
chamadores do lote.

Todo caminho resolve todo chamador: um handler que devolve algo sem tamanho
(`None`, um gerador) ou com o número errado de resultados falha o lote com
`RuntimeError` e o worker segue pro próximo; um `CancelledError` levantado
pelo próprio handler cancela só aquele lote; e cancelar o worker cancela o
lote em curso **e** tudo que ainda estava na fila — nenhum `submit` fica
esperando pra sempre.

## Compartilhar modelos carregados

`ModelRegistry` mantém modelos carregados por id (LRU) — dois call sites
pedindo o mesmo modelo reusam a instância, e o menos usado é descarregado
(`unload()`) quando passa de `max_models`:

```python
from tempest_fastapi_sdk.genai import Embedder, ModelRegistry

registry = ModelRegistry(max_models=2)

def get_embedder(model_id: str) -> Embedder:
    return registry.get(model_id, lambda: Embedder(model_id))
```

!!! note "Handle guardado continua contando no `max_models`"
    Guardar o objeto que o `get()` devolveu (`embedder = registry.get(...)`
    no startup, usado em todo request) é seguro com os loaders do SDK.
    Depois de uma evicção, a próxima chamada nesse handle **não** recarrega
    por fora: ela registra o modelo de novo na mesma chave (substituindo o
    objeto que um `get()` posterior tenha criado ali), despeja o menos usado
    e só então carrega os pesos. Com `max_models=1`, guardar `A`, pedir `B`
    e voltar a usar `A` dá três loads e dois unloads, nunca os dois modelos
    residentes ao mesmo tempo.

    Se o modelo despejado ainda tem chamadas rodando, quem entra no lugar
    dele espera elas terminarem antes de carregar — vale para o handle
    readmitido e para o modelo novo de um `get()`. A exceção é a chamada
    feita **de dentro** da chamada de outro modelo: ela não espera (a
    chamada de fora pode ser justamente o que o despejado aguarda), então
    ali o teto é excedido até a chamada de fora acabar. Um objeto de
    terceiro que só implementa `unload()` não tem esse gancho — para esses,
    chame `get()` por request.

## O que está carregado agora

Um serviço self-hosted pode segurar vários modelos ao mesmo tempo, cada um
ocupando gigabytes de VRAM enquanto ficar carregado. `runtime_report`
responde a pergunta operacional — *o que está residente agora?*:

```python
from tempest_fastapi_sdk.genai import Embedder, TextGenerator, runtime_report

report = runtime_report(
    {
        "chat": TextGenerator("Qwen/Qwen2.5-0.5B-Instruct"),
        "embed": Embedder("sentence-transformers/all-MiniLM-L6-v2"),
    },
)
for model in report.models:
    print(model.key, model.kind, model.loaded, model.seconds_idle)
print(report.loaded_count, "de", report.total_count)
```

```text
chat TextGenerator True 612.4
embed Embedder False None
1 de 2
```

A ordem não é acidental: **carregados primeiro, e entre eles o mais ocioso
primeiro** — é a ordem que alguém lê quando a placa encheu e precisa decidir
o que liberar. `model.idle_past_threshold` diz quais já passaram do próprio
limite.

Para um handle só, `describe_model`:

```python
from tempest_fastapi_sdk.genai import TextGenerator, describe_model

info = describe_model(TextGenerator("Qwen/Qwen2.5-0.5B-Instruct"), key="chat")
print(info.kind, info.model_id, info.device, info.loaded)
```

!!! check "Ler nunca carrega"
    `describe_model` só lê atributos — chamá-lo num gerador que nunca
    carregou devolve `loaded=False` e o deixa descarregado. É o que torna
    seguro chamar isso num serviço vivo, inclusive dentro de um health
    check.

!!! note "Campo ausente vira `None`, não chute"
    Cada loader expõe uma superfície um pouco diferente, e objetos de
    terceiros expõem menos ainda. Um handle que só implementa `is_loaded`
    ainda aparece no relatório, com o resto `None` — melhor que sumir de
    uma auditoria de memória.

O registry sabe se descrever sozinho, e sabe liberar o que envelheceu:

```python
from tempest_fastapi_sdk.genai import ModelRegistry

registry = ModelRegistry(max_models=3)
report = registry.inventory(probe=False)
freed = registry.unload_idle()
print(freed)
```

`unload_idle()` chama o `unload_if_idle()` de cada handle e devolve as
chaves que liberou. **As entradas continuam registradas** — um
`TextGenerator` que soltou os pesos ainda é o objeto certo pra entregar, e
ele recarrega no próximo uso. Para esquecer a entrada de vez, use `evict()`
/ `evict_all()`.

E por HTTP:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.genai import ModelRegistry, make_genai_router

registry = ModelRegistry(max_models=3)
app = FastAPI()
app.include_router(make_genai_router(models=registry))
```

```bash
curl "http://127.0.0.1:8000/api/genai/models?probe=false"
```

O `probe=false` pula a sonda do host — a única parte do endpoint que custa
alguma coisa. Com o `pynvml` instalado (extra `[metrics]`), a sonda lê nome e
memória de cada GPU pelo NVML, sem criar contexto CUDA no processo web; sem
ele, cai no `torch.cuda.mem_get_info`, que cria — medido em +209 MiB de VRAM
numa RTX 4070 Ti SUPER. Com o default, o relatório vem junto do retrato de memória do
host, então uma chamada responde "o que está carregado" e "quanto ainda
cabe" de uma vez.

## No Prometheus e no admin

O mesmo inventário alimenta as duas superfícies onde alguém já olha.

```python
from tempest_fastapi_sdk.genai import GenAIMetrics, ModelRegistry

registry = ModelRegistry(max_models=3)
metrics = GenAIMetrics()

def publish() -> None:
    metrics.observe_inventory(registry.inventory(probe=False))
```

Chame `publish()` numa tarefa periódica e o `/metrics` passa a expor
`genai_models_loaded{kind,device}`, `genai_models_known` e — quando o
relatório trouxe hardware — `genai_gpu_vram_free_bytes{index}`.

!!! note "Gauge é retrato, e por isso é limpo antes"
    Os contadores respondem "quanto trabalho passou"; estes respondem "o que
    está residente **agora**", que é a pergunta que explica um OOM. As séries
    com label são limpas a cada chamada: um modelo descarregado entre dois
    snapshots precisa **parar** de ser reportado, senão o valor velho fica
    parecendo residência.

No admin, os mesmos números viram cards:

```python
from tempest_fastapi_sdk.admin import AdminSite
from tempest_fastapi_sdk.genai import ModelRegistry, make_model_cards

registry = ModelRegistry(max_models=3)
site = AdminSite(dashboard_cards=make_model_cards(registry))
```

São três: **Models resident** (`2 of 5`), **Resident by device**
(`cuda: 2, cpu: 1`) e **VRAM free**. Só o último sonda o host, então passe
`include_vram=False` numa máquina sem GPU ou quando o dashboard não puder
ler NVML.

!!! tip "Os handles são lidos na hora de renderizar"
    Um registry vazio quando você monta o site continua reportando certo
    depois que os modelos carregam — os cards consultam o inventário a cada
    render, não na construção.
