# Playbook: onde o tempo e os bytes vão

Consulte as decisões medidas, em ordem de ataque: estimador, transporte, custo do monitoramento e cold start.

Parte da receita [Modelops (export, bench, quantização)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/modelops.md).

Esta seção não é teoria — são medições feitas **com os instrumentos deste
SDK** (`benchmark_models`, `analyze_onnx`, `rank`, `PredictionMonitor`) numa
máquina de 12 cores, sobre 7 candidatos treinados no mesmo dataset (20
features, 3 classes, 6000 amostras).

## 1. Escolha o estimador, não o pós-processamento

```python
from tempest_fastapi_sdk.modelops import benchmark_models

report = benchmark_models(
    ["dist/logreg/logreg.onnx", "dist/mlp/mlp.onnx", "dist/forest/forest.onnx"],
    quality={"logreg": 0.787, "mlp": 0.966, "forest": 0.931},
    n_repetitions=200,
)
for profile in report.profiles:
    print(profile.name, profile.composite_score, profile.is_pareto)
```

O que saiu:

| Modelo | Acurácia | `.onnx` | gzip | p50 |
| --- | --- | --- | --- | --- |
| logreg | 0,787 | 1,0 KB | 0,9 KB (84%) | 0,0039 ms |
| tree d8 | 0,833 | 16,6 KB | 3,8 KB (23%) | 0,0032 ms |
| forest 50 d6 | 0,887 | 265,8 KB | 40,5 KB (15%) | 0,0046 ms |
| forest 300 | 0,931 | 13.464 KB | — | 0,0082 ms |
| hist gb | 0,951 | 669,1 KB | — | 0,0064 ms |
| **MLP (64,32)** | **0,966** | **15,3 KB** | — | 0,0062 ms |

!!! tip "O reflexo 'tabular = floresta' custa 880x o tamanho por acurácia pior"
    A floresta de 300 árvores entrega 0,931 em 13,4 MB. O MLP pequeno entrega
    **0,966 em 15,3 KB**, na mesma faixa de latência. Numa frota que baixa
    modelo por rede, isso é a diferença entre uma atualização e um incidente.

    Não é lei — é o resultado neste dataset. A questão é que só a medição
    diz, e `benchmark_models` custa três linhas.

!!! warning "gzip não rende igual em todo modelo"
    Medido: floresta 15%, árvore 23%, **regressão logística 84%**. Modelo
    denso e pequeno não tem redundância para comprimir. A regra dos "10-13%"
    vale para ensembles de árvore, que é onde o tamanho dói.

## 2. Latência não é o eixo — o transporte é

Medido no `forest_50_d6` com `TestClient` em processo (sem rede, ou seja, é o
**piso**):

| Lote | Inferência | Monitor | HTTP total | µs por linha (HTTP) |
| --- | --- | --- | --- | --- |
| 1 | 0,0075 ms | 0,0061 ms | 1,22 ms | 1.223 |
| 8 | 0,0147 ms | 0,0112 ms | 1,37 ms | 171 |
| 64 | 0,0988 ms | 0,0493 ms | 2,16 ms | 34 |
| 512 | 0,7708 ms | 0,3626 ms | 8,42 ms | 16 |

!!! danger "Uma requisição por linha gasta 99,4% do tempo fora do modelo"
    Para uma linha, o HTTP custa **1,2 ms contra 0,0075 ms** de inferência —
    160x. Trocar de modelo aqui não muda nada perceptível; **agrupar sim**:
    de lote 1 para 512 o custo por linha cai de 1.223 µs para 16 µs, 74x.

    Fluxo recomendado: acumule no cliente e mande lotes. Se a latência de
    resposta unitária for requisito, o lugar certo do modelo é **junto do
    chamador** — no dispositivo (`load_edge_package`) ou no navegador
    ([`tempest-react-sdk/tabular`](https://mauriciobenjamin700.github.io/tempest-react-sdk/tabular/)),
    onde a viagem simplesmente não existe.

A inferência por linha satura em ~1,5 µs a partir do lote 8 — acima disso
você está pagando serialização, não modelo.

## 3. Monitoramento tem custo, e ele foi medido

A primeira versão do `PredictionMonitor` binava deriva com um laço por
feature e por bin. Medido: **67 µs por chamada de 1 linha, contra 7,5 µs de
inferência** — monitorar custava 9x prever.

A v0.192.0 vetorizou: uma comparação contra a matriz de bordas resolve todas
as features de todas as linhas, e um `bincount` fecha o lote.

| | Antes | Depois |
| --- | --- | --- |
| `observe` 1 linha | 67,1 µs | **6,0 µs** |
| `observe` 64 linhas | ~104 µs | **49,7 µs** |

Sem baseline (só latência e distribuição de saída) custa 1,4 µs — se o
dispositivo não tem baseline, ligar o monitor mesmo assim é praticamente de
graça.

!!! tip "Meça o seu, não confie nesta tabela"
    ```python
    import statistics, time

    def median_us(fn, reps=1000):
        for _ in range(100):
            fn()
        samples = []
        for _ in range(reps):
            started = time.perf_counter()
            fn()
            samples.append((time.perf_counter() - started) * 1e6)
        return statistics.median(samples)

    print(median_us(lambda: predictor.predict(rows)))
    print(median_us(lambda: monitor.observe(rows, prediction)))
    ```

## 4. Cold start: o que custa ao subir

| Passo | Custo |
| --- | --- |
| `read_manifest` | 0,15 ms |
| `load_edge_package` (266 KB, com SHA-256) | 2,16 ms |
| mesma coisa, `verify_digest=False` | 2,00 ms |

Conferir o digest custa **0,16 ms** — mantenha ligado. E `read_manifest`
sendo 14x mais barato que carregar é o que torna viável perguntar "tem versão
nova?" em loop sem tocar no grafo.

## 5. Ordem de ataque

1. **Agrupe as requisições.** Ganho de 74x por linha, sem tocar no modelo.
2. **Meça candidatos com `benchmark_models` + `quality=`.** A fronteira de
   Pareto mostra o que é defensável; o `composite_score` (custo, **menor
   vence**) ordena dentro dela.
3. **Corte tamanho no estimador** — profundidade e número de árvores, ou
   troque a família. Ver a tabela de profundidade acima.
4. **Sirva gzip.** Um header, 85% a menos de rede em ensemble.
5. **Só então mexa em threads**, e conforme o formato da carga (seção
   anterior).
6. **Se latência unitária importa, tire o HTTP do caminho** — modelo no
   dispositivo ou no navegador.

!!! info "Energia não foi medida aqui, e o SDK diz isso"
    Este ambiente (WSL2) não expõe `powercap`, então
    `resolve_cpu_energy_sampler()` devolve `NullPowerSampler` e os relatórios
    trazem `energy_source: "unavailable"` — em vez de um número inventado.
    Num host com RAPL ou GPU NVIDIA, as mesmas chamadas passam a preencher
    `energy_per_inference_j`.
