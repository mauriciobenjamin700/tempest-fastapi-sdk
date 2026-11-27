---
description: "Índice do módulo `modelops` para quantizar, medir e exportar modelos; contém instalação dos extras e a tabela que leva à subpágina de cada tarefa."
---

# Modelops (export, bench, quantização)

Três trabalhos que sempre andam juntos: você **quantiza** pra deixar o
modelo mais barato, **mede** pra descobrir se ficou mesmo, e **exporta**
pro formato que o dispositivo alvo roda.

O módulo `tempest_fastapi_sdk.modelops` cobre os três, medindo CPU, RAM,
GPU e **energia** na mesma janela — o número de "quão rápido" e o de
"quanto consome" saem da mesma medição, não de duas execuções sem relação.

```bash
uv add "tempest-fastapi-sdk[modelops]"        # benchmark, monitoramento, leitor compacto
uv add "tempest-fastapi-sdk[modelops-onnx]"   # + ONNX, .ort, quantização
```

!!! check "Nada aqui limita a sua versão de `transformers`"
    Dois extras, e nenhum deles carrega `optimum` — que hoje declara
    `transformers<4.58`. O caminho HuggingFace (otimizar e quantizar um
    export) roda sobre o `onnxruntime` do `[modelops-onnx]`, então o seu
    serviço fica livre para usar a série 5.x. O único passo que exige
    `optimum` é gerar o export, e ele vira um comando `uvx` descartável —
    veja "HuggingFace: otimizar e quantizar um export".

!!! info "Submódulo, não top-level"
    Como `genai`/`vision`, isso é ferramental pesado e fica no submódulo:
    `from tempest_fastapi_sdk.modelops import benchmark_onnx`. O módulo
    importa **sem nenhum extra** — cada dependência é resolvida dentro da
    função que precisa dela, e a ausência levanta um `ImportError` dizendo
    qual extra instalar.

## Onde está cada tarefa

Use esta tabela para abrir só a subpágina da tarefa que você tem; elas estão na ordem de leitura.

| Subpágina | O que resolve |
| --- | --- |
| [Comece por aqui](modelops/start.md) | Use na primeira vez: um exemplo que roda como está, qual é o seu caso e as cinco palavras do módulo. |
| [Exportar scikit-learn e `.pkl`](modelops/sklearn.md) | Use para exportar um modelo scikit-learn (inclusive vindo de `.pkl`) para ONNX ou para o formato compacto sem runtime. |
| [Servir na borda e publicar o pacote](modelops/edge.md) | Use para servir o modelo (com GPU ou por HTTP), trocá-lo sem redeploy, publicar o pacote de borda e saber o que vale otimizar. |
| [Saber se o modelo ainda funciona](modelops/monitoring.md) | Use para monitorar drift em produção: três sinais, baseline do treino, PSI, memória constante, HTTP e Prometheus. |
| [Playbook: onde o tempo e os bytes vão](modelops/playbook.md) | Consulte as decisões medidas, em ordem de ataque: estimador, transporte, custo do monitoramento e cold start. |
| [Medir: tempo, CPU, GPU, RAM, energia e comparação](modelops/measure.md) | Use para medir antes de otimizar, cronometrar qualquer callable, amostrar recursos e energia e ranquear modelos por score e Pareto. |
| [Quantizar (dinâmica, estática e HuggingFace)](modelops/quantize.md) | Use para quantizar dinâmica ou estaticamente e otimizar e quantizar um export do HuggingFace sem depender do `optimum`. |
| [Fundir, `.ort`, inspecionar, API e CLI](modelops/formats.md) | Use para fundir detector e classificador, converter `.onnx` em `.ort`, inspecionar sem executar e expor o relatório numa API ou pela CLI. |

## Recapitulando

- Meça **antes** de otimizar, com warm-up e repetições — `tempest model
  bench` ou `benchmark_onnx`.
- Reporte **mediana + IQR**, o hardware e o `energy_source`. Nenhuma medida
  aqui é wall-plug.
- Compare com **score composto** (pesos publicados) **e** fronteira de
  Pareto; `quality` é sua, o SDK não inventa.
- Quantize dinâmico primeiro, estático quando tiver dados de calibração —
  e **re-meça a acurácia** nos dois casos.
- Para HuggingFace: exporte com `optimum-cli` via `uvx` (fora do projeto,
  para o teto de `transformers` não entrar), depois `optimize_hf_onnx` →
  `quantize_hf_onnx` com o `onnxruntime` que você já tem.
- Para edge: `.onnx` → `.ort` com `target_platform` e o
  `.required_operators.config` do build mínimo.
