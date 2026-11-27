# A máquina aguenta? Hardware e estimativa

Use antes de baixar pesos: sonda CPU, RAM e GPU (inclusive em container) e estima se o modelo cabe.

Parte da receita [IA generativa self-hosted](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai.md).

## "A máquina aguenta?"

Carregar um modelo grande demais termina num OOM minutos depois do
download começar. `can_run` responde antes:

```python
from tempest_fastapi_sdk.genai import can_run

report = can_run(model_id="Qwen/Qwen2.5-7B-Instruct")

if report.fits:
    print(f"OK em {report.device} — {report.headroom_pct:.0f}% de folga")
else:
    print(report.reason)
    print("Sugestão:", report.suggestion)   # ex.: "Quantize to int4 ..."
```

O `CapacityReport` traz: `fits`, `device` (`cuda`/`mps`/`cpu`),
`estimated_bytes` vs `available_bytes`, `headroom_pct`, `reason`, uma
`suggestion` concreta quando não cabe (quantizar, offload pra CPU, ou
trocar de modelo) e `memory_measured`, que diz se a memória livre do device
foi de fato lida (veja [Sem `psutil`](#sem-psutil)).

Sem `dtype=`, o `can_run` dimensiona na precisão que o
`TextGenerator(dtype="auto")` carrega no device escolhido: `bfloat16` em
CUDA/MPS, `float32` em CPU. Passe `dtype=` para planejar outra coisa — um
`dtype=` explícito no gerador, ou `ModelDtypeEnum.INT8`/`INT4` para
`quantization=`.

!!! tip "Deixe o SDK escolher a precisão"
    `recommend(...)` tenta a precisão nativa do melhor device disponível
    (`bfloat16` em GPU, `float32` em CPU) → `int8` → `int4` e devolve a
    **primeira** config que cabe:

    ```python
    from tempest_fastapi_sdk.genai import recommend

    best = recommend(model_id="meta-llama/Llama-3.1-8B")
    print(best.device, best.dtype, best.fits)   # ex.: cuda int8 True
    ```

### Em CPU

Até a 0.303.0 o planejador dimensionava todo load sem quantização em
`bfloat16`, mas em CPU o `TextGenerator` carrega em `float32` — o dobro
de bytes. O `recommend()` dizia "cabe" para um modelo que não cabia.
Agora a CPU é dimensionada em `float32`, e o degrau abaixo é `int8` (nunca
`bfloat16`, que não é quantização).

Medido com o GPU escondido (`CUDA_VISIBLE_DEVICES=`), i9-13900F, 62 GB de
RAM, WSL2, torch 2.14, bitsandbytes 0.50.2, N=3 por linha. "Medido" é o
crescimento do RSS do processo entre antes e depois do `load()`, na
mediana; transformers 4.57.6 (o piso) / 5.18.0 (a mais nova no dia):

| Modelo | Precisão | Estimativa | Medido (4.57.6 / 5.18.0) |
| --- | --- | --- | --- |
| Qwen2.5-0.5B-Instruct | `float32` (antes: `bfloat16`) | 2,47 GB (antes: 1,24 GB) | 2,13 GB / 2,12 GB |
| Qwen2.5-0.5B-Instruct | `int8` | 0,62 GB | 0,89 GB / 1,29 GB |
| Qwen2.5-0.5B-Instruct | `int4` | 0,37 GB | 1,02 GB / 1,62 GB |
| Qwen2.5-3B-Instruct | `float32` (antes: `bfloat16`) | 15,43 GB (antes: 7,71 GB) | 12,51 GB / 12,48 GB |
| Qwen2.5-3B-Instruct | `int8` | 3,86 GB | 3,67 GB / 8,54 GB |
| Qwen2.5-3B-Instruct | `int4` | 2,31 GB | 3,25 GB / 8,16 GB |

O que a tabela diz:

- **`float32` é o que a CPU carrega**, e a estimativa nova cobre o RSS
  depois do load. O pico **durante** o load passa dela: 3,12 GB no 0,5B
  e 14,71 GB / 18,66 GB no 3B.
- **int8/int4 via bitsandbytes carregam e geram em CPU** com o
  bitsandbytes 0.50.2 — nenhum erro nas 24 execuções quantizadas da
  tabela.
- **A estimativa quantizada pode ser otimista.** No 0,5B o RSS medido do int4
  ficou 2,7× a 4,4× acima dela (os embeddings não são quantizados, e
  pesam mais num modelo pequeno). No 3B, no transformers 4.57.6, o int8
  ficou abaixo da estimativa (3,67 contra 3,86 GB) e o int4 acima (3,25
  contra 2,31 GB). No transformers 5.18.0 o load
  quantizado do 3B deixa o processo com 5,65 GB de RSS **mapeado de
  arquivo** (`RssFile`, contra 0,36 GB no 4.57.6); a memória anônima do
  processo inteiro ficou em 3,33 GB (int8) e 2,95 GB (int4).
- **bitsandbytes em CPU é mais lento que `float32`.** No 3B, um
  `generate()` limitado a 16 tokens levou ~9 s em int8/int4 contra ~3 s
  em `float32`. Por isso a `suggestion` de quantizar em CPU aponta o
  caminho de CPU que já está no SDK:

!!! tip "Em CPU, GGUF pelo Ollama"
    O llama.cpp (build 687e778, `-ngl 0`, contexto 4096) servindo o GGUF
    Q4_K_M que o Ollama 0.30.11 baixa para `qwen2.5:3b` ficou em 3,60 GB
    de RSS (1,61 GB anônima + 1,99 GB de arquivo) e gerou 24 a 27
    tokens/s; o `qwen2.5:0.5b` ficou em 0,66 GB e 107 a 134 tokens/s
    (N=3 cada). Use o [`OllamaGenerator`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/ollama.md) — a memória é
    da mesma ordem do int4 do bitsandbytes; a vantagem medida é a
    velocidade. Para um agente, quanto isso rende por passo (e onde não
    rende: o prefill) está em
    [Agente em CPU](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/agents.md#agente-em-cpu-backend-tamanho-e-orcamento).

## Sondando o hardware

```python
from tempest_fastapi_sdk.genai import probe_hardware

hw = probe_hardware()
print(hw.cpu_cores, hw.ram_available_bytes)
print(hw.has_cuda, [g.name for g in hw.gpus])   # VRAM por GPU quando há CUDA
```

`HardwareInfo` reporta CPU, RAM total/disponível, GPUs CUDA (nome +
VRAM total/livre), MPS (Apple) e espaço livre em disco. Sem `torch`,
nenhuma GPU aparece (`has_cuda=False`, `gpus=[]`). A RAM vem do `psutil`
e, no Linux, do `/proc/meminfo` quando o `psutil` falta — veja
[Sem `psutil`](#sem-psutil) —, limitada pelo cgroup em container com
`--memory` (veja [Em container com limite de
memória](#em-container-com-limite-de-memoria)). Leitura que não pôde ser feita fica marcada
como **não medida**, e não como vazia: os campos de RAM ficam em `0` com
`ram_measured=False`; se o disco não responde, `disk_free_bytes=0` com
`disk_measured=False`. Os campos continuam `int`, então conta feita com
eles não quebra — mas confira a flag antes de ler `0` como "nada livre".
Snapshot montado à mão (`HardwareInfo(...)`) tem as duas flags `True` por
padrão.

### Avisos do ambiente

O `HardwareInfo.warnings` lista o problema de ambiente que a sonda consegue
ver **antes** de um load falhar — cada item é uma frase com a causa e a
correção. Lista vazia quer dizer que nada foi detectado, não que todo load vai
dar certo. Hoje há um aviso: `torch` build CUDA 13 (`torch.version.cuda`
começando em `13.`) com CUDA disponível e o `ctranslate2` do faster-whisper
instalado. As wheels do `ctranslate2` procuram a `libcublas.so.12`, e a
primeira transcrição em `device="cuda"` falharia
([GPU com `[genai-audio]`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/installation.md#genai-audio-gpu)). O
`ctranslate2` é procurado com `importlib.util.find_spec`, sem importar: a sonda
não carrega biblioteca CUDA nenhuma por isso.

```python
from tempest_fastapi_sdk.genai import probe_hardware

for warning in probe_hardware().warnings:
    print(warning)
```

Num container `python:3.12-slim` com RTX 4070 Ti SUPER, `torch` 2.15.0+cu132
do PyPI e `ctranslate2` 4.8.5:

```text
torch is a CUDA 13.2 build, but ctranslate2 (faster-whisper) links against CUDA 12 and looks for libcublas.so.12, which this torch does not install: transcribing on device='cuda' raises 'Library libcublas.so.12 is not found or cannot be loaded' unless the system provides CUDA 12 libraries. Install torch from the CUDA 12 index (https://download.pytorch.org/whl/cu126) instead; see 'GPU with [genai-audio]' in the installation docs.
```

Com o `torch` 2.14.1+cu126 do índice cu126, na mesma máquina, a lista veio
vazia. O aviso não enxerga o CUDA 12 instalado no sistema — que faz o par
funcionar —, por isso o texto diz "unless the system provides CUDA 12
libraries".

### Sem `psutil`

O `psutil` vem no extra `[metrics]`, **não** no `[genai]`. O que acontece
sem ele depende da plataforma:

- **Linux (WSL e container incluídos): a RAM é medida mesmo assim.** O
  `probe_hardware()` lê `MemTotal` e `MemAvailable` do `/proc/meminfo` — o
  mesmo arquivo que o `psutil` lê no Linux. Medido numa venv limpa só com o
  pacote base (WSL2, 62 GB, `CUDA_VISIBLE_DEVICES=`, sem `torch`): total
  de 67 430 916 096 bytes, igual ao `free -b`; `MemAvailable` a 516 KB do
  `free -b` na mesma janela (as duas leituras são instantes diferentes).
  Lendo o arquivo e chamando o `psutil` 7.2.2 no mesmo processo, os dois
  bytes bateram exatamente. O `recommend(num_params=500_000_000)` dá
  `fits=True` em `float32`.
- **Kernel sem `MemAvailable`** (anterior ao 3.14) ou com `MemAvailable: 0`:
  a RAM conta como **não medida**. O `psutil` estima nesse caso; somar
  `MemFree + Buffers + Cached` superestimaria o que um load pode pegar, e o
  trabalho do planejador é justamente não prometer memória que não existe.
- **Windows e macOS: sem `psutil` a RAM não é medida**, e o relatório diz
  isso com todas as letras.

Na 0.303.1, RAM não medida era lida como zero byte livre: na máquina acima,
o `recommend(num_params=500_000_000)` devolvia `fits=False` em `int4` com
*"Model is too large for this host even quantized; use a smaller model or
add memory."*. Agora, com memória desconhecida (saída abaixo com
`sys.platform` trocado para `darwin` depois do import, sem `psutil`):

```python
from tempest_fastapi_sdk.genai import recommend

report = recommend(num_params=500_000_000)
print(report.memory_measured, report.fits, report.dtype)
print(report.suggestion)
```

```text
False False float32
Free RAM was not measured: on darwin measuring RAM requires psutil (pip install 'tempest-fastapi-sdk[metrics]'). Then check again.
```

No Linux essa mensagem só aparece se o `/proc/meminfo` também falhar, e
aí ela diz que o `psutil` e o `/proc/meminfo` falharam. Com memória
desconhecida:

- **`fits` é `False`** porque nada foi verificado — um `if report.fits:`
  que decide carregar continua conservador. Quem precisa distinguir
  "não cabe" de "não sei" lê `memory_measured`.
- **`dtype` é a precisão nativa** (`float32` em CPU, `bfloat16` em MPS), a
  que o `TextGenerator(dtype="auto")` carrega. O `recommend()` não desce
  para `int8`/`int4`: sem um número para comparar, não há o que justifique
  quantizar, e quantizar custa qualidade e, em CPU, velocidade (veja
  [Em CPU](#em-cpu)).
- **`available_bytes` e `headroom_pct` ficam em `0`** e não carregam
  informação; `reason` diz que a RAM não foi medida.
- **Em GPU o veredito continua medido** — a VRAM vem do `torch`/NVML. Só
  a sugestão de offload para CPU, que depende da RAM, vira "a RAM não foi
  medida" em vez de "o host é pequeno demais".

### Em container com limite de memória

O `/proc/meminfo` de um container mostra a memória do **host**, e o
`psutil.virtual_memory()` lê o mesmo arquivo. Até a 0.303.2, num
`docker run --memory=512m` em host de 62 GB, os dois reportavam os
67 430 916 096 bytes do host, e o planejador respondia `fits=True` para um
load de 2,5 GB que não cabe em 512 MiB.

Agora, no Linux, o `probe_hardware()` lê também o limite de memória do
cgroup do processo — `memory.max` no cgroup v2, `memory.limit_in_bytes` no
v1, achado pelo `/proc/self/cgroup` e pelo `/proc/self/mountinfo`, subindo
pelos cgroups pais. Quando esse limite fica abaixo da RAM do host, ele vira
o `ram_total_bytes`, o `ram_available_bytes` passa a ser o menor entre o do
host e o que sobra no cgroup, e `ram_cgroup_limited=True`. Vale com e sem
`psutil`:

```python
from tempest_fastapi_sdk.genai import can_run, probe_hardware, recommend

info = probe_hardware()
print(info.ram_total_bytes, info.ram_cgroup_limited)

native = can_run(num_params=500_000_000)
print(native.fits, native.dtype)
print(native.reason)

best = recommend(num_params=500_000_000)
print(best.fits, best.dtype)
```

Saída num `python:3.12-slim` com `--memory=512m` (WSL2, Docker 29.6.1,
cgroup v2), com a wheel desta versão e sem `psutil`:

```text
536870912 True
False float32
~2.5 GB needed at float32 exceeds the ~0.4 GB free on cpu. RAM is limited by the container's cgroup to 512 MiB.
True int4
```

Com o `psutil` 7.2.2 instalado no mesmo container a saída é a mesma. Sem
`--memory` (`memory.max` = `max`) nada muda: o total continua sendo os
67 430 916 096 bytes do host e `ram_cgroup_limited=False`.

**Quanto sobra no cgroup.** O `memory.current` (`memory.usage_in_bytes` no
v1) conta o page cache, que o kernel descarta antes de chamar o OOM killer.
Por isso a conta é `limite - (uso - (file - shmem))`: page cache volta, mas
`tmpfs`/memória compartilhada (`shmem`) não, sem swap. Medido no mesmo
container com `--memory-swap=512m` (sem swap), alocando de 8 em 8 MiB até
o OOM killer:

| Cenário | `limite - uso` | `limite - (uso - (file - shmem))` | Alocou de fato |
| --- | --- | --- | --- |
| 300 MiB de page cache, lidos uma vez | ~183 MiB | ~483 MiB | 504 MiB |
| os mesmos 300 MiB lidos 3 vezes (`active_file`) | ~183 MiB | ~483 MiB | 504 MiB |
| 300 MiB em `/dev/shm` (`shmem`) | ~191 MiB | ~191 MiB | 200 MiB |

Descontar só o `inactive_file` (a conta do `docker stats`) teria dado
~203 MiB no segundo cenário, e descontar o `file` inteiro teria dado
~491 MiB no terceiro. Com a wheel, o próprio `probe_hardware()` reportou
417 MiB livres e o processo alocou 432 MiB (3 de 3 execuções); com 300 MiB
de page cache ativo, reportou 409 MiB e o processo alocou 416 ou 432 MiB.

!!! note "Swap fica de fora"
    Com `--memory=512m` e sem `--memory-swap`, o Docker deixa o container
    usar mais 512 MiB de swap: no mesmo host o processo alocou 944 MiB antes
    do OOM killer. O planejador conta só a RAM — modelo paginado para o swap
    não é modelo rodando.

O cgroup v1 é coberto por teste com arquivos falsos (limite, valor
sentinela de "sem limite", raiz de montagem `/docker/<id>`), mas não foi
medido num host v1: o Docker sobre WSL2 usa cgroup v2.

## Estimativa sem baixar pesos

A conta é `nº de parâmetros × bytes por parâmetro × overhead`. Os bytes
por parâmetro vêm da precisão (`float32`=4, `float16`/`bfloat16`=2,
`int8`=1, `int4`≈0.6); o overhead (×1.25) cobre ativações, KV-cache e
contexto de runtime.

```python
from tempest_fastapi_sdk.genai import estimate_model_bytes, ModelDtypeEnum

gb = estimate_model_bytes(7_000_000_000, ModelDtypeEnum.INT4) / 1e9
print(f"~{gb:.1f} GB")   # 7B em int4
```

O número de parâmetros pode vir explícito (`num_params=`) ou ser lido do
Hub por `model_id` (via `huggingface_hub`, sem baixar os pesos —
metadados safetensors).
