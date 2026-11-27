# Saber se o modelo ainda funciona

Use para monitorar drift em produção: três sinais, baseline do treino, PSI, memória constante, HTTP e Prometheus.

Parte da receita [Modelops (export, bench, quantização)](../modelops.md).

O dispositivo responde em 3 ms. Isso não diz nada sobre as respostas estarem
certas.

Em produção não há rótulo — ninguém avisa o dispositivo que ele acabou de
classificar errado —, então **acurácia não é medível ali**. O que dá para
medir é se o mundo ainda se parece com aquele do treino, e se a saída do
modelo mudou. São proxies, e a implementação diz isso em vez de fingir o
contrário.

```python
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import (
    OnnxPredictor,
    PredictionMonitor,
    baseline_from_samples,
    export_sklearn_to_onnx,
)

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)
model = RandomForestClassifier(n_estimators=20, random_state=0).fit(
    X_train, y_train
)
export_sklearn_to_onnx(model, X_train[:10], "model.onnx")
predictor = OnnxPredictor("model.onnx")


baseline = baseline_from_samples(X_train, labels=y_train)

held_out = X_test.tolist()
monitor = PredictionMonitor(baseline=baseline)
monitor.observe(held_out, predictor.predict(held_out))
report = monitor.report()
print(report.drift.n_rows, report.drift.verdict, round(report.drift.worst_psi, 2))

shifted = [[value * 1.3 for value in row] for row in X_train.tolist()]
monitor = PredictionMonitor(baseline=baseline)
monitor.observe(shifted, predictor.predict(shifted))
report = monitor.report()
print(report.drift.n_rows, report.drift.verdict, round(report.drift.worst_psi, 2))
```

```text
38 insufficient_data 0.44
112 significant 5.09
```

As 38 linhas do teste vêm da **mesma** distribuição do treino e ainda assim
dão PSI 0,44, acima do limiar de `significant` (0,25): com tão poucas linhas,
bin vazio é ruído de amostragem. Por isso o veredito fica em
`insufficient_data` abaixo de `MIN_ROWS_FOR_DRIFT` (100 linhas). As 112
linhas multiplicadas por 1,3 simulam um sensor que mudou de escala, e essas
sim saem `significant`.

## Três sinais, porque separam falhas diferentes

| Sinal | Pega |
| --- | --- |
| Latência e volume | Dispositivo em throttling térmico, provider que caiu para CPU |
| Deriva de entrada | Sensor que mudou de unidade, formulário que mudou um default, estação do ano que o treino não viu |
| Distribuição das predições | Entradas nas faixas de sempre, mas combinadas de um jeito que joga tudo para uma classe |

Ler os dois últimos juntos é o que dá diagnóstico:

!!! tip "Como interpretar a combinação"
    - **Entrada mudou, saída estável** → *covariate shift* geralmente inócuo.
    - **Saída mudou, entrada estável** → o modelo está extrapolando.
    - **Os dois mudaram** → retreinar, não ajustar limiar.

## A baseline sai do treino, não da produção

`baseline_from_samples` guarda **bordas de bin e proporções**, nunca as
linhas. São poucos kilobytes e nenhum registro — dá para versionar junto do
modelo.

```python
from pathlib import Path

from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import baseline_from_samples

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)

baseline = baseline_from_samples(X_train, labels=y_train)


Path("dist/baseline.json").write_text(baseline.model_dump_json())
```

!!! danger "Construir a baseline com tráfego de produção anula a medição"
    Ela passaria a descrever a população **já derivada** como normal. A
    baseline sai do conjunto de treino, no momento do treino.

## PSI, e o que ele não é

A métrica é o **Population Stability Index**, padrão em *credit scoring* há
décadas: `< 0.1` estável, `0.1–0.25` mudou, `> 0.25` mudou o bastante para
não confiar na calibração.

!!! warning "Convenção, não teste estatístico"
    PSI não tem p-valor nem distribuição nula. Ele não diz que a mudança é
    significante — diz que é grande segundo uma regra de bolso que a
    indústria adotou. Cruzar o limiar é motivo para **olhar**, não para agir
    sozinho.

Abaixo de `MIN_ROWS_FOR_DRIFT` (100 linhas) o veredito é
`insufficient_data`, não `stable`: com 30 linhas em 10 bins, bin vazio é
resultado esperado da amostragem. "Ainda não temos tráfego" e "não há
deriva" são respostas diferentes, e a segunda mentiria no painel.

Rótulos são comparados **por valor**, não pelo texto: `0`, `0.0`,
`numpy.int64(0)` e `numpy.float32(0.0)` caem todos na chave `"0"`, dos dois
lados. Antes, uma baseline construída com alvo float (`"0.0"`) contra um
classificador que responde inteiro (`"0"`) dava PSI 26,24 — `significant` —
sobre distribuições idênticas; agora dá 0,0. Baselines já salvas com chaves
`"0.0"` são lidas do mesmo jeito, sem precisar regerar.

## Memória constante

Linhas são contadas nos bins e descartadas. O custo é
`n_features x n_bins` contadores, independentemente do tráfego — nada
acumula cópia das requisições, o que também significa que nenhum valor de
feature fica em memória para vazar em log ou dump.

Os rótulos previstos também têm teto: no máximo `MAX_TRACKED_LABELS` (64)
chaves próprias, mais as classes da baseline, que nunca perdem a delas; o
resto conta em `OTHER_LABEL` (`"__other__"`). É o que segura um regressor,
cuja saída é um valor novo por linha: medido com 50 000 valores distintos, o
relatório tinha 50 000 chaves e 889 270 bytes de JSON; com o teto, 65 chaves
e 1 350 bytes. O sinal de um regressor está em `mean`, `minimum` e `maximum`,
que continuam exatos. `PredictionMonitor(max_labels=...)` muda o teto.

A deriva é medida **por janela** (`DEFAULT_WINDOW_ROWS`, 1000 linhas). Ao
fechar, a janela vira a última medição completa e os contadores zeram, então
os números descrevem tráfego recente em vez de tudo desde o boot — que
levaria dias para reagir a uma mudança real.

## No HTTP e no Prometheus

```python
from fastapi import FastAPI
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import (
    PredictionMonitor,
    OnnxPredictor,
    PredictionMetrics,
    baseline_from_samples,
    make_prediction_router,
)

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)
monitor = PredictionMonitor(baseline=baseline_from_samples(X_train, labels=y_train))
predictor = OnnxPredictor("model.onnx")
app = FastAPI()


app.include_router(
    make_prediction_router(
        predictor,
        monitor=monitor,
        metrics=PredictionMetrics(),
    ),
)
```

`GET /api/predict/monitor` devolve o relatório inteiro; as métricas vão para
o mesmo registry do `/metrics` do SDK (`edge_model_predictions_total`,
`edge_model_prediction_seconds`, `edge_model_feature_drift_psi{feature}`,
`edge_model_prediction_share{label}`).

!!! check "Trocar de modelo zera o monitor"
    `POST /model/sync` chama `monitor.reset()` quando a versão muda de
    verdade. Misturar duas versões no mesmo percentil esconderia exatamente
    a regressão que a atualização de frota precisa pegar.

    Sem baseline o monitor ainda registra latência e distribuição de saída —
    um dispositivo sem baseline não deve ficar sem monitoramento nenhum.
