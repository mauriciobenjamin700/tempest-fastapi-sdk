# Pesos de modelos (ciclo no Hub)

Carregar um modelo pelo id é a metade fácil. A outra metade é tudo o que a
primeira chamada de `from_pretrained` esconde: **qual commit** você recebeu,
**quantos gigabytes** foram escritos em **qual diretório**, se o disco tinha
espaço, e como fazer o próximo boot reproduzir os mesmos pesos **sem rede**.

O módulo `tempest_fastapi_sdk.genai.hub` cobre essa metade.

```bash
uv add "tempest-fastapi-sdk[genai-hub]"   # só o ciclo de vida do peso
uv add "tempest-fastapi-sdk[genai]"       # já inclui o de cima + torch/transformers
```

!!! info "O módulo importa sem extra nenhum"
    `huggingface_hub` é resolvido dentro da função que precisa dele. Os
    schemas (`ModelRef`, `CachedModel`) são usáveis e testáveis num host que
    nunca vai baixar nada; a ausência da dependência levanta um `ImportError`
    dizendo qual extra instalar.

## O problema: `main` se move

Isto aqui é o que quase todo serviço self-hosted faz no primeiro dia:

```python
from tempest_fastapi_sdk.genai import TextGenerator

generator = TextGenerator("Qwen/Qwen2.5-0.5B-Instruct")
```

Funciona. E tem três buracos:

| Buraco | O que acontece na prática |
| --- | --- |
| **Revisão não fixada** | O autor faz push em `main`. O pod reinicia. Você está servindo outros pesos, sem ter mudado uma linha. |
| **Download dentro do request** | O primeiro `POST /generate` paga alguns GB de download com um cliente esperando no outro lado. |
| **Sem modo offline** | Um host air-gapped não tem como *garantir* que o load não vai tentar a rede — ele só descobre quando falha. |

Os três se resolvem com as mesmas três palavras-chave, e elas são iguais em
todos os loaders do SDK.

## Fixar a revisão

Primeiro descubra o commit que está por trás da branch:

```python
from tempest_fastapi_sdk.genai import resolve_revision

sha: str | None = resolve_revision("Qwen/Qwen2.5-0.5B-Instruct", revision="main")
print(sha)
```

```text
a8b602d5f1c9e0d3b7c1f4a2e9d8c7b6a5f4e3d2
```

Pelo terminal, o mesmo — junto com o download:

```bash
tempest model pull Qwen/Qwen2.5-0.5B-Instruct --pin
```

```text
Qwen/Qwen2.5-0.5B-Instruct
  revision   : default
  pin to     : a8b602d5f1c9e0d3b7c1f4a2e9d8c7b6a5f4e3d2
  path       : /home/u/.cache/huggingface/hub/models--Qwen--Qwen2.5-0.5B-Instruct/snapshots/a8b602d
  size       : 999.59 MB
  files      : 9
```

Guarde o sha na configuração do serviço e passe adiante:

```python
from tempest_fastapi_sdk.genai import TextGenerator

generator = TextGenerator(
    "Qwen/Qwen2.5-0.5B-Instruct",
    revision="a8b602d5f1c9e0d3b7c1f4a2e9d8c7b6a5f4e3d2",
)
```

!!! tip "`resolve_revision` devolve `None` quando não dá pra fixar"
    Hub fora do ar, repositório privado sem token, revisão inexistente — a
    função devolve `None` em vez de levantar. Quem chama decide se segue sem
    pin ou se aborta o deploy; a decisão é sua, não da biblioteca.

## Baixar antes de servir

O lugar de pagar o download é o build da imagem ou o passo de deploy — nunca
o caminho do request.

```python
from tempest_fastapi_sdk.genai import ModelSnapshot, download_model

snapshot: ModelSnapshot = download_model(
    "Qwen/Qwen2.5-0.5B-Instruct",
    revision="a8b602d5f1c9e0d3b7c1f4a2e9d8c7b6a5f4e3d2",
    cache_dir="/var/lib/models",
    allow_patterns=["*.json", "*.safetensors"],
)
print(snapshot.size_bytes, snapshot.file_count, snapshot.path)
```

No `Dockerfile`, uma linha:

```dockerfile
RUN tempest model pull Qwen/Qwen2.5-0.5B-Instruct \
    --revision a8b602d5f1c9e0d3b7c1f4a2e9d8c7b6a5f4e3d2 \
    --cache-dir /var/lib/models \
    --allow "*.json" --allow "*.safetensors"
```

!!! check "O `--allow` não é detalhe"
    Muitos repositórios publicam os pesos **duas vezes** — `.bin` e
    `.safetensors`. Restringir aos formatos que você realmente carrega costuma
    cortar metade do download e metade do disco.

### Ele se recusa a começar o que não cabe

`download_model` mede o repositório no Hub antes de escrever qualquer coisa e
compara com o espaço livre:

```python
from tempest_fastapi_sdk.genai import download_model

try:
    download_model("meta-llama/Llama-3.1-70B", cache_dir="/var/lib/models")
except OSError as exc:
    print(exc)
```

```text
meta-llama/Llama-3.1-70B needs ~154.0 GB (estimate x1.1) but only 41.3 GB are free on /var/lib/models
```

Falhar em dois segundos com um número é melhor que falhar quarenta minutos
depois com um cache pela metade. Para medir sem baixar:

```python
from tempest_fastapi_sdk.genai import model_disk_bytes

needed: int | None = model_disk_bytes("Qwen/Qwen2.5-0.5B-Instruct")
print(needed)
```

!!! note "Disco e memória são perguntas diferentes"
    `model_disk_bytes` responde "cabe no volume?". Quem responde "cabe na
    RAM/VRAM?" é o
    [`can_run`](genai.md) — as duas checagens são independentes e um deploy
    saudável faz as duas.

## Rodar offline

Com os pesos já no cache, `local_files_only=True` transforma qualquer load
numa operação puramente local:

```python
from tempest_fastapi_sdk.genai import Embedder, TextGenerator

generator = TextGenerator(
    "Qwen/Qwen2.5-0.5B-Instruct",
    revision="a8b602d5f1c9e0d3b7c1f4a2e9d8c7b6a5f4e3d2",
    cache_dir="/var/lib/models",
    local_files_only=True,
)
embedder = Embedder(
    "sentence-transformers/all-MiniLM-L6-v2",
    cache_dir="/var/lib/models",
    local_files_only=True,
)
```

Se o peso não estiver lá, o load falha **na hora**, em vez de silenciosamente
buscar na rede de um host que deveria estar isolado.

## As mesmas três palavras-chave em todo lugar

Não há um "jeito de fixar" por classe:

| Classe | Módulo |
| --- | --- |
| `TextGenerator` | `tempest_fastapi_sdk.genai` |
| `Embedder` | `tempest_fastapi_sdk.genai` |
| `VisionTextGenerator` | `tempest_fastapi_sdk.genai` |
| `ClassifierModerator` | `tempest_fastapi_sdk.genai` |
| `Reranker` | `tempest_fastapi_sdk.genai.rag` |

Todas aceitam `revision=`, `local_files_only=` e `trust_remote_code=`, além
do `cache_dir=`/`hf_token=` que já existiam. Duas exceções, ambas por limite
da biblioteca de baixo:

- **`SpeechToText`** aceita `revision=`, `local_files_only=` e `hf_token=`,
  mas não `trust_remote_code` — o CTranslate2 carrega pesos, nunca Python do
  repositório.
- **`OnnxEmbedder`** tem o grafo em disco; só o tokenizer vem do Hub, então
  os parâmetros são `tokenizer_revision=` e `hf_token=`. Aponte `tokenizer`
  para um `tokenizer.json` local quando o host não puder tocar a rede.

### `trust_remote_code` é opt-in de propósito

```python
from tempest_fastapi_sdk.genai import VisionTextGenerator

generator = VisionTextGenerator(
    "Qwen/Qwen2-VL-2B-Instruct",
    trust_remote_code=True,
)
```

!!! warning "Isso executa Python do repositório"
    Algumas arquiteturas só carregam com `trust_remote_code=True`, e o
    `transformers` vai dizer isso na mensagem de erro. Ligar a chave executa
    código que você não revisou, do mesmo repositório de onde vieram os pesos
    — por isso ela é por modelo, e não um default do SDK. Se você ligar,
    fixe a revisão junto: o código auditado hoje e o código de amanhã são o
    mesmo `main`.

## Ver e limpar o cache

Peso é a maior coisa que um serviço self-hosted escreve em disco, e nada
poda: todo modelo já carregado fica lá até alguém remover.

```bash
tempest model cache-list --revisions
```

```text
   4.43 GB  Qwen/Qwen2-VL-2B-Instruct
   4.43 GB    a1b2c3d4e5f6  [main]
 999.59 MB  Qwen/Qwen2.5-0.5B-Instruct
 999.59 MB    a8b602d5f1c9  [main]
 181.97 MB  sentence-transformers/all-MiniLM-L6-v2
 181.97 MB    1110a243fdf4  [main]
   5.61 GB  total
```

Em Python, para expor num endpoint de operação:

```python
from tempest_fastapi_sdk.genai import CachedModel, cache_size_bytes, list_cached_models

models: list[CachedModel] = list_cached_models()
for model in models:
    print(model.model_id, model.size_bytes, len(model.revisions))
print(cache_size_bytes())
```

Para reclamar espaço:

```bash
tempest model cache-rm Qwen/Qwen2-VL-2B-Instruct --dry-run
```

```text
would free 4.43 GB by removing Qwen/Qwen2-VL-2B-Instruct
```

```python
from tempest_fastapi_sdk.genai import remove_cached_model

freed: int = remove_cached_model(
    "Qwen/Qwen2-VL-2B-Instruct",
    revision="a1b2c3d4e5f6",
)
print(freed)
```

!!! danger "Remover peso não tem desfazer"
    O único jeito de voltar atrás é baixar de novo. Por isso o comando pede
    confirmação (pule com `--yes`) e `--dry-run` mostra o tamanho sem tocar em
    nada. Um modelo que não está no cache devolve `0` — não é erro, é um no-op
    bem-sucedido.

## O objeto por trás: `ModelRef`

Todos os loaders montam um `ModelRef` e o repassam. Você raramente precisa
construir um à mão, mas é ele que explica o comportamento:

```python
from tempest_fastapi_sdk.genai import ModelRef

ref = ModelRef(
    model_id="Qwen/Qwen2.5-0.5B-Instruct",
    revision="a8b602d",
    local_files_only=True,
)
print(ref.loader_kwargs())
print(ref.download_kwargs())
```

```text
{'revision': 'a8b602d', 'local_files_only': True}
{'revision': 'a8b602d', 'local_files_only': True}
```

Só o que **difere do default** é emitido. Isso mantém a chamada idêntica ao
que o SDK enviava antes, quando nada está fixado, e mantém o mesmo dicionário
utilizável com loaders mais estreitos — o `tokenizers.Tokenizer.from_pretrained`
aceita `revision` mas não `trust_remote_code`. O `download_kwargs()` remove
`trust_remote_code`, que é decisão de load e não significa nada enquanto se
buscam arquivos.

## Recapitulando

- **`resolve_revision`** transforma `main` no sha imutável. Fixe-o e o boot
  para de depender do dia.
- **`download_model`** / **`tempest model pull`** paga o download no build ou
  no deploy, e se recusa a começar o que o disco não aguenta.
- **`local_files_only=True`** faz o load ser puramente local — o modo de um
  host air-gapped.
- **`trust_remote_code=True`** é opt-in por modelo, porque executa código do
  repositório.
- **`list_cached_models`** / **`remove_cached_model`** (e os `cache-list` /
  `cache-rm`) mostram e reclamam o que os pesos ocupam.

Onde continuar: [IA generativa self-hosted](genai.md) para o que fazer com os
pesos depois de carregados, e [Modelops](modelops.md) para medir, quantizar e
exportar.
