# Fundir, `.ort`, inspecionar, API e CLI

Use para fundir detector e classificador, converter `.onnx` em `.ort`, inspecionar sem executar e expor o relatório numa API ou pela CLI.

Parte da receita [Modelops (export, bench, quantização)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/modelops.md).

## Fundir detector e classificador

O resto desta página é o ciclo de vida de **um** modelo. Compor **dois** é
outro trabalho, e o que este passo cobre: um detector a 640 achando os
objetos, um classificador a 224 julgando cada recorte, e um `.onnx` só que
roda os dois.

```python
from pathlib import Path

from tempest_fastapi_sdk.modelops import fuse_detect_classify

fused: Path = Path("dist/fused.onnx")
fuse_detect_classify(
    "detector.onnx",
    "classifier.onnx",
    fused,
    conf_threshold=0.25,
    max_detections=20,
)
```

Servir o arquivo resultante é a receita
[Visão computacional](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/vision.md#deteccao-classificacao-num-grafo-so),
com `DetectClassify`.

!!! info "Por que `onnx.compose.merge_models` não resolve"
    Entre os dois estágios existe um recorte **dinâmico**: quantas caixas
    há, e onde, só se sabe depois que o detector rodou. Dois grafos não se
    concatenam através disso. A ponte é `RoiAlign`, e montá-la é
    exatamente o que `fuse_detect_classify` faz.

!!! note "Dois extras, porque são duas máquinas"
    Fundir precisa de `[modelops-compose]` (que traz `onnx`); servir o
    grafo fundido precisa só de `[vision]`. O serviço em produção nunca
    importa `onnx`.

    ```bash
    uv add "tempest-fastapi-sdk[modelops-compose]"   # na máquina de build
    uv add "tempest-fastapi-sdk[vision]"             # no serviço
    ```

!!! tip "É re-export, não wrapper"
    `fuse_detect_classify` é o símbolo do `ort-vision-sdk`, re-exportado
    lazy — tocar o nome importa a lib e levanta um `ImportError` nomeando
    o extra quando ela falta. A lista de argumentos nomeados **não é
    fixa**: medido, são 18 no `ort-vision-sdk` 0.8.0 (o piso que
    declaramos) e 19 na 0.9.0, com `normalization` entrando no meio. Um
    wrapper escrito contra o piso teria engolido esse argumento em
    silêncio para quem instalasse a versão nova — por isso a assinatura
    que você chama é sempre a de lá.

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
    Em produção, prefira rodar via [TaskIQ](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/queue-tasks.md) e devolver o
    relatório salvo.

## CLI

| Comando | O que faz |
| --- | --- |
| `tempest model analyze <modelo>` | Parâmetros, tamanho, opset e shapes, sem executar. |
| `tempest model bench <modelo>` | Latência, memória e energia sobre N repetições. |
| `tempest model export-ort <modelo>` | Converte para `.ort` + config de operadores. |
| `tempest model optimize <in> <out>` | Persiste as otimizações de grafo do ONNX Runtime. |
| `tempest model quantize <in> <out>` | Quantização dinâmica, int8 por padrão (`--weight-type` aceita `uint8`, `int16`, `uint16`, `int4`, `uint4`). |
| `tempest model hardware` | O que esta máquina roda e o que consegue medir. |
| `tempest model pull <model_id>` | Baixa os pesos de um modelo do HuggingFace antes da primeira requisição; `--pin` imprime o commit por trás da revisão. |
| `tempest model cache-list` | Lista o que o cache local de pesos guarda, do maior para o menor. |
| `tempest model cache-rm <model_id>` | Apaga um modelo do cache local; pede confirmação sem `--yes`, e `--dry-run` só mede. |

`--json` existe em `analyze`, `bench`, `hardware`, `pull` e `cache-list`;
`quantize`, `optimize`, `export-ort` e `cache-rm` não têm a flag. Com ela, o
comando vira um passo de CI:

```bash
tempest model bench models/classify.onnx --json > bench.json
```
