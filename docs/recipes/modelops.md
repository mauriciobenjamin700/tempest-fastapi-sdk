# Modelops (export, bench, quantização)

Três trabalhos que sempre andam juntos: você **quantiza** pra deixar o
modelo mais barato, **mede** pra descobrir se ficou mesmo, e **exporta**
pro formato que o dispositivo alvo roda.

O módulo `tempest_fastapi_sdk.modelops` cobre os três, medindo CPU, RAM,
GPU e **energia** na mesma janela — o número de "quão rápido" e o de
"quanto consome" saem da mesma medição, não de duas execuções sem relação.

```bash
uv add "tempest-fastapi-sdk[modelops]"        # só benchmark
uv add "tempest-fastapi-sdk[modelops-onnx]"   # + ONNX, .ort, quantização
uv add "tempest-fastapi-sdk[modelops-quant]"  # + HuggingFace via optimum
```

!!! info "Submódulo, não top-level"
    Como `genai`/`vision`, isso é ferramental pesado e fica no submódulo:
    `from tempest_fastapi_sdk.modelops import benchmark_onnx`. O módulo
    importa **sem nenhum extra** — cada dependência é resolvida dentro da
    função que precisa dela, e a ausência levanta um `ImportError` dizendo
    qual extra instalar.

## Medir antes de otimizar

A primeira coisa é medir. Sem `tempest model bench` você não tem base de
comparação pra dizer se a quantização ajudou.

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
    `energy_source` junto do número — `EnergySource.NVML_COUNTER` e
    `EnergySource.RAPL` não são a mesma grandeza. Para consumo real de
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
from tempest_fastapi_sdk.modelops import rank

report = rank(
    profiles,
    weights={"latency_ms_p99": 0.7, "rss_peak_mb": 0.3},
    quality={"n": 0.802, "s": 0.841},
)
```

A **fronteira de Pareto** não tem opinião. Um modelo está nela quando
nenhum outro é ao menos tão barato em todos os eixos *e* ao menos tão bom.
O que sobra é o conjunto de escolhas defensáveis:

```python
from tempest_fastapi_sdk.modelops import pareto_points

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

## Quantizar

### Dinâmica: sem dado de calibração

Pesos quantizados na frente, faixas de ativação calculadas na hora. É a
opção sem atrito e normalmente a primeira tentativa certa em transformers
e modelos densos, onde o ganho está nos pesos:

```python
from tempest_fastapi_sdk.modelops import quantize_onnx_dynamic

result = quantize_onnx_dynamic(
    "models/classify.onnx",
    "models/classify.int8.onnx",
)
print(result.compression_ratio)
print(result.backend)
```

```bash
tempest model quantize models/classify.onnx models/classify.int8.onnx
```

### Estática: com amostras representativas

Uma passada de calibração roda o modelo sobre entradas reais pra aprender
a faixa que cada ativação de fato ocupa. Pesos **e** ativações viram
inteiro, o que libera os kernels int8 fundidos — ganho maior de velocidade,
risco maior de acurácia:

```python
import numpy as np

from tempest_fastapi_sdk.modelops import quantize_onnx_static

batches = [
    {"images": np.load(f"calib/{index:03d}.npy")} for index in range(128)
]
result = quantize_onnx_static(
    "models/classify.onnx",
    "models/classify.qdq.onnx",
    calibration_inputs=batches,
    per_channel=True,
)
print(result.notes)
```

!!! tip "Poucas centenas de amostras reais > dezenas de milhares sintéticas"
    A faixa aprendida a partir de ruído vai cortar ativações reais. Se
    `MINMAX` derrubar a acurácia, tente `CalibrationMethod.ENTROPY` ou
    `PERCENTILE`: um único outlier estica a faixa min/max até todo o resto
    quantizar em meia dúzia de níveis.

!!! danger "Quantização é lossy — re-meça a acurácia"
    Quanto o int8 custa é propriedade do seu modelo, e nada neste módulo
    consegue prever. Rode seu conjunto de avaliação no artefato quantizado
    antes de subir. Quando uma camada específica desaba, use
    `nodes_to_exclude=` pra deixar só ela em float.

## HuggingFace: exportar, otimizar, quantizar

```bash
uv add "tempest-fastapi-sdk[modelops-quant]"
```

O caminho completo, do Hub ao artefato int8:

```python
from tempest_fastapi_sdk.modelops import (
    export_hf_to_onnx,
    optimize_hf_onnx,
    quantize_hf_onnx,
)

exported = export_hf_to_onnx(
    "distilbert-base-uncased",
    "exports/distilbert",
    task="text-classification",
)
optimized = optimize_hf_onnx("exports/distilbert", "exports/distilbert-o2")
quantized = quantize_hf_onnx(
    "exports/distilbert-o2",
    "exports/distilbert-int8",
    target="avx512_vnni",
)
print(exported.output_size_mb, quantized.compression_ratio)
```

`optimize_hf_onnx` é **lossless em precisão** nos níveis `O1`/`O2`: funde
atenção, layer norm e afins em kernels únicos sem mudar o que o grafo
calcula. `O3` troca por uma GELU aproximada e `O4` converte pra float16 —
esses dois mexem nos números, e `O4` é só GPU.

`target` escolhe o conjunto de instruções: `arm64` (celular, Raspberry Pi,
Apple silicon, Graviton), `avx2`, `avx512`, `avx512_vnni` (o caminho int8
mais rápido em x86) ou `tensorrt`. Escolher o errado ainda produz um modelo
válido, só que lento.

!!! warning "`optimum` prende `transformers` abaixo da 5.x"
    O `optimum-onnx` de hoje declara `transformers<5`. Instalar
    `[modelops-quant]` junto de `[genai]` no mesmo ambiente resolve
    `transformers` para a série 4.x. Se o seu serviço precisa de
    `transformers` 5.x em runtime, mantenha a quantização num ambiente de
    build separado e faça o deploy só do `.onnx` gerado.

Para modelos generativos que continuam em PyTorch existe o caminho
bitsandbytes, que salva os pesos int4/int8 recarregáveis por
`AutoModelForCausalLM` — e portanto por
[`TextGenerator`](genai.md):

```python
from tempest_fastapi_sdk.modelops import quantize_hf_bnb

result = quantize_hf_bnb(
    "Qwen/Qwen2.5-0.5B-Instruct",
    "models/qwen-int4",
    bits=4,
    quant_type="nf4",
)
print(result.notes)
```

Precisa de `[genai]` + `[genai-quant]` e de uma GPU CUDA: o bitsandbytes
não tem kernel de CPU pra conversão.

!!! danger "`trust_remote_code=True` executa Python remoto"
    `export_hf_to_onnx` e `quantize_hf_bnb` aceitam a flag porque algumas
    arquiteturas do Hub exigem. Ela roda código arbitrário do repositório
    remoto na sua máquina — só ligue para um repositório que você auditou.

## Levar pro edge: `.onnx` para `.ort`

`.ort` é o formato serializado do próprio ONNX Runtime. Importa em mobile e
embarcado por dois motivos: as otimizações de grafo já vêm aplicadas, então
o start-up não paga por elas, e a conversão emite um
`.required_operators.config` listando exatamente quais kernels o modelo
usa — alimente isso num build mínimo do ONNX Runtime e o binário cai de
dezenas de megabytes para poucos.

```python
from tempest_fastapi_sdk.modelops import export_onnx_to_ort

results = export_onnx_to_ort(
    "models/classify.int8.onnx",
    "dist/mobile",
    target_platform="arm",
    enable_type_reduction=True,
)
for result in results:
    print(result.output_path, result.output_size_mb)
    print(result.extra_files)
```

```bash
tempest model export-ort models/classify.int8.onnx -o dist/mobile -t arm
```

| Parâmetro | Efeito |
| --- | --- |
| `optimization_style` | `FIXED` cozinha as otimizações no arquivo (menor, carrega mais rápido — o default de mobile); `RUNTIME` deixa o grafo re-otimizável no dispositivo. |
| `target_platform` | `"amd64"` ou `"arm"` — restringe às otimizações válidas naquela plataforma. Defina sempre que a máquina que converte e o alvo diferem, o que num build mobile é sempre. |
| `enable_type_reduction` | Registra também **quais tipos** cada operador precisa, para o build mínimo descartar implementações não usadas. |

Passando um diretório em vez de um arquivo, a conversão é recursiva e você
recebe um `ExportResult` por `.ort` escrito.

### Saindo do PyTorch

```python
import torch

from tempest_fastapi_sdk.modelops import export_torch_to_onnx

result = export_torch_to_onnx(
    torch.nn.Linear(128, 10),
    "models/linear.onnx",
    example_input=torch.randn(1, 128),
    opset=17,
    input_names=["features"],
    output_names=["logits"],
    dynamic_axes={"features": {0: "batch"}},
)
print(result.opset, result.output_size_mb)
```

!!! tip "Deixe fixo o que puder ficar fixo"
    Dimensão fixa deixa o runtime escolher kernels mais rápidos. Só declare
    em `dynamic_axes` o que precisa mesmo variar.

!!! note "Opset é compatibilidade, não recurso"
    Opset mais novo é mais expressivo; mais velho é mais portável. Runtimes
    mobile e conversores de terceiros costumam ficar para trás, e `12` segue
    sendo o piso mais seguro para esses.

### Otimizar o grafo sem sair de `.onnx`

Quando `.ort` não é uma opção mas o start-up dói, dá pra persistir as
mesmas fusões num `.onnx`:

```python
from tempest_fastapi_sdk.modelops import optimize_onnx_graph

result = optimize_onnx_graph(
    "models/classify.onnx",
    "models/classify.opt.onnx",
)
print(result.size_ratio)
```

!!! warning "Grafo otimizado é específico do provider"
    Um modelo fundido para CUDA pode ficar mais lento — ou não carregar —
    num host só de CPU. Otimize por alvo.

## Inspecionar sem executar

`analyze_onnx` lê o artefato e nada mais: instantâneo, e dá o mesmo número
em qualquer máquina — o que faz dele a coisa certa pra citar ao lado de uma
latência, que não é comparável entre máquinas nenhuma.

```python
from tempest_fastapi_sdk.modelops import analyze_onnx

metrics = analyze_onnx("models/classify.onnx")
print(metrics.n_parameters, metrics.disk_size_mb, metrics.opset)
for spec in metrics.inputs:
    print(spec.name, spec.shape, spec.dtype)
```

Os parâmetros são somados a partir das dimensões dos inicializadores, não
dos dados — um modelo de vários gigabytes é inspecionado sem carregar um
único peso.

`analyze_ort` faz o mesmo para `.ort`, com uma limitação honesta: o formato
serializado não expõe a tabela de inicializadores, então `n_parameters`
fica em `0`. Analise o `.onnx` de origem quando o número importar.

## Expondo o relatório numa API

O `BenchmarkReport` é um schema Pydantic — `None` em vez de `NaN`
justamente para poder virar JSON:

```python
# src/api/routers/models.py
from fastapi import APIRouter

from tempest_fastapi_sdk.modelops import BenchmarkReport, benchmark_models

router = APIRouter(prefix="/api/models", tags=["models"])


@router.post("/benchmark")
async def run_benchmark(paths: list[str]) -> BenchmarkReport:
    """Mede e ranqueia os modelos informados."""
    return benchmark_models(paths, n_warmup=5, n_repetitions=20)
```

!!! warning "Benchmark é CPU-bound e demorado"
    Não deixe um endpoint desses aberto sem autenticação nem sem limite:
    ele ocupa o worker por segundos e distorce a latência de todo mundo.
    Em produção, prefira rodar via [TaskIQ](queue-tasks.md) e devolver o
    relatório salvo.

## CLI

| Comando | O que faz |
| --- | --- |
| `tempest model analyze <modelo>` | Parâmetros, tamanho, opset e shapes, sem executar. |
| `tempest model bench <modelo>` | Latência, memória e energia sobre N repetições. |
| `tempest model quantize <in> <out>` | Quantização dinâmica int8. |
| `tempest model optimize <in> <out>` | Persiste as otimizações de grafo do ONNX Runtime. |
| `tempest model export-ort <modelo>` | Converte para `.ort` + config de operadores. |
| `tempest model hardware` | O que esta máquina roda e o que consegue medir. |

Todos aceitam `--json` (exceto `export-ort` e `optimize`, que já imprimem
os caminhos escritos), o que os torna utilizáveis num passo de CI:

```bash
tempest model bench models/classify.onnx --json > bench.json
```

## Recapitulando

- Meça **antes** de otimizar, com warm-up e repetições — `tempest model
  bench` ou `benchmark_onnx`.
- Reporte **mediana + IQR**, o hardware e o `energy_source`. Nenhuma medida
  aqui é wall-plug.
- Compare com **score composto** (pesos publicados) **e** fronteira de
  Pareto; `quality` é sua, o SDK não inventa.
- Quantize dinâmico primeiro, estático quando tiver dados de calibração —
  e **re-meça a acurácia** nos dois casos.
- Para HuggingFace: `export_hf_to_onnx` → `optimize_hf_onnx` →
  `quantize_hf_onnx`, ciente do teto de `transformers` que o `optimum`
  impõe.
- Para edge: `.onnx` → `.ort` com `target_platform` e o
  `.required_operators.config` do build mínimo.
