# Medir: tempo, CPU, GPU, RAM, energia e comparação

Use para medir antes de otimizar, cronometrar qualquer callable, amostrar recursos e energia e ranquear modelos por score e Pareto.

Parte da receita [Modelops (export, bench, quantização)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/modelops.md).

## Medir antes de otimizar

A primeira coisa é medir. Sem `tempest model bench` você não tem base de
comparação pra dizer se a quantização ajudou.

!!! info "O comando `tempest` pede o extra `[cli]` (ou `[cli-ops]` no servidor)"
    Sem um dos dois, `tempest` sai com código 2 e imprime como instalar. Em
    desenvolvimento: `uv add --dev "tempest-fastapi-sdk[cli]"`. Detalhes em
    [Instalação → CLI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/installation.md#cli).

```bash
tempest model bench models/classify.onnx --repetitions 50 --warmup 10
```

```text
classify  [cpu / CPUExecutionProvider]
  latency ms : median 12.412  iqr 0.804  p95 14.108  p99 15.902
  throughput : 79.4/s  (50 reps, 10 warm-up, batch 1)
  memory     : rss peak 412.50 MB  gpu peak -
  energy     : -  (unavailable)
  static     : 3,180,000 params  6.20 MB
```

Em Python, o mesmo:

```python
from tempest_fastapi_sdk.modelops import benchmark_onnx

profile = benchmark_onnx(
    "models/classify.onnx",
    n_warmup=10,
    n_repetitions=50,
)
print(profile.runtime.latency_ms_median)
print(profile.runtime.throughput_per_s)
print(profile.static.n_parameters if profile.static else 0)
```

Três coisas que o laço faz e que um `time.perf_counter()` em volta da
chamada não faz:

| O quê | Por quê |
| --- | --- |
| **Warm-up** | As primeiras chamadas pagam seleção de kernel, crescimento do alocador e autotune do cuDNN. São executadas e descartadas. |
| **Mediana + IQR** | Latência tem cauda pesada. Média sozinha esconde justamente a cauda que o seu p99 se importa. |
| **Energia junto** | Um sampler de GPU e um de CPU rodam durante a janela cronometrada. |

!!! warning "Entrada sintética mede só o custo de forma"
    Sem `feeds`, as entradas são sintetizadas a partir das formas
    declaradas. Isso é exato pra um classificador de imagem, cujo custo
    depende só do shape — e **enganoso** pra um detector ou um decoder
    autorregressivo, onde o trabalho depende do conteúdo. Nesses casos
    passe entradas reais.

### Dimensões simbólicas

Um grafo com `["batch", 3, "height", "width"]` não tem como ser executado
até você dizer quanto vale `height` e `width`. O SDK **não chuta** — dar
1x1 pra uma CNN produz um número confiantemente errado:

```bash
tempest model bench models/detect.onnx --dim height=640 --dim width=640
```

```python
from tempest_fastapi_sdk.modelops import benchmark_onnx

profile = benchmark_onnx(
    "models/detect.onnx",
    dynamic_dims={"height": 640, "width": 640},
    batch_size=1,
)
```

A dimensão inicial sem nome cai pra `batch_size` automaticamente; qualquer
outra que sobrar levanta `ValueError` dizendo o nome que falta.

### Entradas reais

```python
import numpy as np

from tempest_fastapi_sdk.modelops import benchmark_onnx

batch = {"images": np.load("samples/real_batch.npy")}
profile = benchmark_onnx("models/detect.onnx", feeds=batch, n_repetitions=100)
```

## Medir qualquer coisa

`benchmark` cronometra um callable sem argumentos. Todo o resto do módulo
é construído em cima dele, então uma sessão ONNX, um módulo torch e um
closure escrito à mão produzem o mesmo `BenchmarkProfile`:

```python
from tempest_fastapi_sdk.modelops import benchmark


def encode() -> int:
    """Uma unidade de trabalho — o que você quer medir."""
    return sum(index * index for index in range(50_000))


profile = benchmark(encode, name="encode", n_warmup=5, n_repetitions=30)
print(profile.runtime.latency_ms_p99)
```

!!! tip "Construa as entradas fora do callable"
    Tudo que acontece dentro dele é medido como parte do modelo. Se você
    carregar a imagem ali dentro, está cronometrando o disco também.

Para PyTorch existe o atalho tipado, que já coloca o módulo em `eval()`,
roda sob `torch.no_grad()` e — em CUDA — envolve cada timer em
`torch.cuda.synchronize()`:

```python
import torch

from tempest_fastapi_sdk.modelops import benchmark_torch

profile = benchmark_torch(
    torch.nn.Linear(512, 10),
    torch.randn(1, 512),
    n_warmup=10,
    n_repetitions=50,
)
```

!!! danger "Sem sincronizar, um benchmark de CUDA mede o nada"
    Lançamento de kernel é assíncrono. Cronometrar sem
    `torch.cuda.synchronize()` mede o tempo de *enfileirar* o trabalho —
    perto de zero, e completamente errado. `benchmark_torch` faz isso por
    você; se você chamar `benchmark` direto com um backend assíncrono,
    passe `sync=`.

## CPU, GPU, RAM e energia

Quatro samplers atrás de um protocolo `PowerSampler`, para o laço de
benchmark nunca precisar saber em que máquina está rodando:

| Sampler | Mede | Quando funciona |
| --- | --- | --- |
| `NvmlPowerSampler` | GPU NVIDIA, via `pynvml` | Driver NVIDIA presente. Prefere o contador de energia total do driver (Volta+), cai pra integração de potência em placas mais antigas. |
| `NvidiaSmiPowerSampler` | GPU NVIDIA, via binário | Driver presente mas sem `pynvml`. |
| `RaplEnergySampler` | Energia do pacote de CPU | Linux bare metal com `/sys/class/powercap` legível. |
| `NullPowerSampler` | Nada, e diz isso | Sempre. É o fallback de todos os outros. |

```python
from tempest_fastapi_sdk.modelops import (
    resolve_cpu_energy_sampler,
    resolve_power_sampler,
)

gpu = resolve_power_sampler()
cpu = resolve_cpu_energy_sampler()
print(type(gpu).__name__, gpu.available)
print(type(cpu).__name__, cpu.available)
```

O jeito rápido de saber o que esta máquina consegue medir:

```bash
tempest model hardware
```

```text
hardware
  cpu cores  : 12
  ram total  : 67.4 GB
  cuda       : False
energy measurement
  gpu        : NvmlPowerSampler (available)
  cpu        : NullPowerSampler (unavailable)
```

!!! danger "Nenhuma dessas medidas é wall-plug"
    Uma leitura de GPU exclui CPU, RAM, perdas da fonte e refrigeração;
    uma leitura RAPL cobre só o pacote de CPU. Sempre publique o
    `energy_source` junto do número — `EnergySourceEnum.NVML_COUNTER` e
    `EnergySourceEnum.RAPL` não são a mesma grandeza. Para consumo real de
    tomada, wattímetro externo.

??? note "Por que o RAPL costuma vir indisponível"
    Depois do CVE-2020-8694 a maioria das distros entrega `energy_uj` como
    `0400 root`, porque um traço de energia de alta resolução vaza
    informação sobre o que a CPU está fazendo. Além disso WSL2, containers
    e a maior parte das VMs de nuvem não expõem `powercap` de jeito nenhum.
    Nos dois casos o sampler degrada em silêncio para `UNAVAILABLE` — nunca
    levanta exceção no meio do seu benchmark.

Uma execução em CPU **não** resolve sampler de GPU por padrão: atribuir o
consumo ocioso de uma placa compartilhada e a VRAM de outros processos a um
modelo que está rodando na CPU seria pior do que não reportar nada. Passe
`power_sampler=` explicitamente se quiser medir a GPU mesmo assim.

## Comparar modelos: score composto e Pareto

Medir um modelo é fácil; escolher entre cinco é o problema real.
`benchmark_models` mede todos nas mesmas condições e ranqueia:

```python
from tempest_fastapi_sdk.modelops import benchmark_models

report = benchmark_models(
    ["models/n.onnx", "models/s.onnx", "models/m.onnx"],
    quality={"n": 0.802, "s": 0.841, "m": 0.856},
    n_warmup=10,
    n_repetitions=50,
)
for profile in report.profiles:
    print(profile.name, profile.composite_score, profile.is_pareto)
print(report.weights)
```

Duas leituras, de propósito lado a lado.

O **score composto** achata vários eixos de custo num número só. Isso é
conveniente e também é uma opinião: os pesos codificam um cenário de
deploy. O default é afinado pra edge/mobile:

```python
from tempest_fastapi_sdk.modelops import DEFAULT_COST_WEIGHTS

print(DEFAULT_COST_WEIGHTS)
```

```text
{'latency_ms_median': 0.4, 'energy_per_inference_j': 0.25,
 'rss_peak_mb': 0.2, 'disk_size_mb': 0.15}
```

Um servidor com SLO de throughput deve repesar — é exatamente pra isso que
o parâmetro existe:

```python
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier

from tempest_fastapi_sdk.modelops import (
    BenchmarkProfile,
    benchmark_onnx,
    export_sklearn_to_onnx,
    rank,
)

X, y = load_iris(return_X_y=True)
profiles: list[BenchmarkProfile] = []
for name, trees in [("n", 5), ("s", 50)]:
    model = RandomForestClassifier(n_estimators=trees, random_state=0).fit(X, y)
    export_sklearn_to_onnx(model, X[:10], f"models/{name}.onnx")
    profiles.append(benchmark_onnx(f"models/{name}.onnx", n_repetitions=50))


report = rank(
    profiles,
    weights={"latency_ms_p99": 0.7, "rss_peak_mb": 0.3},
    quality={"n": 0.802, "s": 0.841},
)
for profile in report.profiles:
    print(profile.name, profile.composite_score, profile.is_pareto)
```

A **fronteira de Pareto** não tem opinião. Um modelo está nela quando
nenhum outro é ao menos tão barato em todos os eixos *e* ao menos tão bom.
O que sobra é o conjunto de escolhas defensáveis:

```python
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier

from tempest_fastapi_sdk.modelops import (
    BenchmarkProfile,
    benchmark_onnx,
    export_sklearn_to_onnx,
    pareto_points,
)

X, y = load_iris(return_X_y=True)
profiles: list[BenchmarkProfile] = []
for name, trees in [("n", 5), ("s", 50)]:
    model = RandomForestClassifier(n_estimators=trees, random_state=0).fit(X, y)
    export_sklearn_to_onnx(model, X[:10], f"models/{name}.onnx")
    profile = benchmark_onnx(f"models/{name}.onnx", n_repetitions=50)
    quality = {"n": 0.802, "s": 0.841}[name]
    profiles.append(profile.model_copy(update={"quality": quality}))


for point in pareto_points(profiles):
    if point.is_pareto:
        print(point.name, point.latency_ms, point.quality)
```

!!! tip "Publique os pesos, e mostre o Pareto junto"
    Score escalar resume; Pareto preserva o trade-off. Um artigo ou um ADR
    que mostra só o score está escondendo a ponderação que decidiu o
    resultado.

!!! note "Medições faltando não distorcem o ranking"
    Uma dimensão que **nenhum** perfil mediu é descartada e os pesos
    restantes são renormalizados para somar 1 — medir num notebook sem
    contador de energia compara latência, memória e tamanho nos próprios
    termos, em vez de dar 25% de graça pra todo mundo. Uma dimensão que
    **algum** perfil não tem é pulada só para aquele perfil.

`quality` nunca é medido pelo SDK: ele não tem como saber o que "bom"
significa na sua tarefa. Sem ele a fronteira degrada para custo puro — útil
pra dizer quais modelos nunca valem a pena, incapaz de dizer qual é o
melhor.
