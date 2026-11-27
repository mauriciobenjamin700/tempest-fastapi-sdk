---
description: "Use para quantizar um modelo ONNX de forma dinâmica ou estática com calibração; contém otimizar e quantizar um export do HuggingFace sem depender do `optimum`."
---

# Quantizar (dinâmica, estática e HuggingFace)

Use para quantizar dinâmica ou estaticamente e otimizar e quantizar um export do HuggingFace sem depender do `optimum`.

Parte da receita [Modelops (export, bench, quantização)](../modelops.md).

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
    `MINMAX` derrubar a acurácia, tente `CalibrationMethodEnum.ENTROPY` ou
    `PERCENTILE`: um único outlier estica a faixa min/max até todo o resto
    quantizar em meia dúzia de níveis.

!!! danger "Quantização é lossy — re-meça a acurácia"
    Quanto o int8 custa é propriedade do seu modelo, e nada neste módulo
    consegue prever. Rode seu conjunto de avaliação no artefato quantizado
    antes de subir. Quando uma camada específica desaba, use
    `nodes_to_exclude=` pra deixar só ela em float.

## HuggingFace: otimizar e quantizar um export

```bash
uv add "tempest-fastapi-sdk[modelops-onnx]"
```

### Passo 0: o export sai do SDK, de propósito

Transformar uma arquitetura arbitrária em ONNX exige uma descrição de grafo
por arquitetura, e o único registro mantido disso vive no `optimum` da
HuggingFace — que declara `transformers<4.58`. Um teto desses viaja para
**todo mundo** que instala o SDK, então ele não entra aqui. O export vira um
passo de build que você roda num ambiente descartável:

```bash
uvx --from "optimum[onnxruntime]" optimum-cli export onnx \
    --model distilbert-base-uncased --task text-classification \
    exports/distilbert
```

!!! tip "Por que `uvx` e não um extra"
    O `uvx` resolve o `optimum` num ambiente temporário e joga fora depois.
    O teto de `transformers` fica lá dentro e nunca toca o seu projeto — você
    continua livre para rodar `transformers` 5.x em runtime. É a mesma
    capacidade, sem o custo de amarrar o pacote.

### Passo 1 e 2: fundir e quantizar

O diretório que aquele comando escreveu é a entrada das duas funções abaixo.
Nenhuma delas toca no `optimum`: ambas rodam sobre o `onnxruntime` que o
`[modelops-onnx]` já traz.

```python
from tempest_fastapi_sdk.modelops import (
    HFQuantizationTargetEnum,
    optimize_hf_onnx,
    quantize_hf_onnx,
)

optimized = optimize_hf_onnx("exports/distilbert", "exports/distilbert-o2")
quantized = quantize_hf_onnx(
    "exports/distilbert-o2",
    "exports/distilbert-int8",
    target=HFQuantizationTargetEnum.AVX512_VNNI,
)
print(optimized.size_ratio, quantized.compression_ratio)
```

`optimize_hf_onnx` é **lossless em precisão** nos níveis `O1`/`O2`: funde
atenção, layer norm e afins em kernels únicos sem mudar o que o grafo
calcula. `O3` troca por uma GELU aproximada e `O4` converte pra float16 —
esses dois mexem nos números, e `O4` é só GPU.

O tipo de fusão sai do `config.json` do export. Arquitetura fora do mapa é
**reportada, nunca chutada** — fundir um grafo com a forma errada gera um
modelo que carrega e devolve números errados. Quando isso acontecer, escolha
você:

```python
from tempest_fastapi_sdk.modelops import optimize_hf_onnx


optimized = optimize_hf_onnx(
    "exports/minha-arquitetura",
    "exports/minha-arquitetura-o2",
    model_type="bert",
)
```

`model_type=` também serve para otimizar um grafo solto, sem `config.json` do
lado.

!!! note "Export com vários grafos"
    Modelos encoder-decoder exportam vários `.onnx` no mesmo diretório
    (`encoder_model.onnx`, `decoder_model.onnx`…). Nesse caso passe
    `file_name=` para escolher qual processar — cada um vai separado. Sem
    isso as funções levantam `ValueError` listando o que encontraram, em vez
    de pegar um ao acaso.

`target` escolhe o conjunto de instruções: `arm64` (celular, Raspberry Pi,
Apple silicon, Graviton), `avx2`, `avx512` ou `avx512_vnni` (o caminho int8
mais rápido em x86). Escolher o errado ainda produz um modelo válido, só que
lento.

!!! info "`reduce_range` só existe onde faz sentido"
    AVX2 e AVX512 sem VNNI podem saturar acumulando int8, e cair para 7 bits
    evita isso. ARM64 e VNNI não têm o problema — ali `reduce_range=True`
    seria só perda de acurácia, então é recusado com `ValueError` em vez de
    aceito e ignorado.

Não há alvo `tensorrt`: aquele perfil é de quantização **estática**, e
`quantize_hf_onnx` é o caminho dinâmico. Para um artefato TensorRT use
`quantize_onnx_static` (a seção "Estática: com amostras representativas"
acima) com os seus próprios dados de calibração.

Os dois passos copiam os arquivos não-grafo do export (`config.json`,
tokenizer, preprocessador) para o diretório de saída, então o resultado
continua carregável por `AutoTokenizer`.

Para modelos generativos que continuam em PyTorch existe o caminho
bitsandbytes, que salva os pesos int4/int8 recarregáveis por
`AutoModelForCausalLM` — e portanto por
[`TextGenerator`](../genai.md):

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
    `quantize_hf_bnb` aceita a flag porque algumas arquiteturas do Hub
    exigem. Ela roda código arbitrário do repositório remoto na sua máquina —
    só ligue para um repositório que você auditou.
