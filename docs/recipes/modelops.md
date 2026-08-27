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

## Comece por aqui

Se você nunca usou este módulo, comece por este bloco. Ele **roda como
está** — não precisa de dados seus, o dataset vem dentro do scikit-learn.

```bash
uv add "tempest-fastapi-sdk[modelops-onnx,modelops-sklearn]"
```

```python
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import edge_pipeline, load_edge_package

# 1. Um modelo qualquer, treinado com o dataset que vem no scikit-learn.
data = load_iris()
X_train, X_test, y_train, y_test = train_test_split(
    data.data, data.target, test_size=0.3, random_state=0
)
model = RandomForestClassifier(n_estimators=20, max_depth=4, random_state=0)
model.fit(X_train, y_train)

# 2. Empacotar: vira um diretório pronto para publicar.
package = edge_pipeline(
    model,
    X_train,
    "dist/flores",
    name="flores",
    labels=y_train,
    feature_names=list(data.feature_names),
    compact=True,
)
print("versão:", package.manifest.version)
print("confere com o sklearn:", package.manifest.verified)

# 3. Carregar e prever, como um dispositivo faria.
loaded = load_edge_package("dist/flores")
resultado = loaded.predictor.predict(X_test[:3])

print("previsto:", resultado.labels)
print("esperado:", model.predict(X_test[:3]).tolist())
```

```text
versão: 5ab558270e27
confere com o sklearn: True
previsto: [2, 1, 0]
esperado: [2, 1, 0]
```

Pronto: o modelo saiu do Python. O diretório `dist/flores/` é o que você
publica — um dispositivo ou um navegador carrega dali.

!!! tip "O que acabou de acontecer"
    `edge_pipeline` treinou nada e otimizou nada. Ele **converteu** o
    estimador para um formato que roda sem Python, **conferiu** que a
    conversão responde igual ao seu modelo, e escreveu o diretório com o
    modelo, a descrição dele e uma referência para detectar mudança de
    dados depois. Se a conferência falhar, ele **levanta erro** em vez de
    gravar — conversão errada não sai daqui em silêncio.

### Qual é o meu caso?

| Você tem | Vá para |
| --- | --- |
| Um modelo treinado na memória | O bloco acima |
| Um arquivo `.pkl` do time de treino | [Vim de um `.pkl`](#vim-de-um-pkl-e-agora) |
| O modelo precisa rodar num **navegador** | [Sem runtime nenhum](#sem-runtime-nenhum-o-formato-compacto) + [tempest-react-sdk/tabular](https://mauriciobenjamin700.github.io/tempest-react-sdk/tabular/) |
| O modelo vai virar um endpoint HTTP | [Servir o modelo na borda](#servir-o-modelo-na-borda) |
| Já está no ar e você quer saber se ainda funciona | [Saber se o modelo ainda funciona](#saber-se-o-modelo-ainda-funciona) |
| Está lento ou grande demais | [Playbook](#playbook-onde-o-tempo-e-os-bytes-realmente-vao) |
| É um modelo de deep learning (torch, transformers) | [Medir antes de otimizar](#medir-antes-de-otimizar) e as seções seguintes |

### Cinco palavras que aparecem o tempo todo

| Palavra | O que quer dizer aqui |
| --- | --- |
| **ONNX** | Formato de arquivo que descreve um modelo já treinado. Vários programas sabem executá-lo, em várias linguagens — por isso ele tira o Python do dispositivo. |
| **Grafo** | O modelo dentro do arquivo ONNX: as contas e a ordem delas. "Grafo" e "modelo exportado" são a mesma coisa nestas páginas. |
| **Quantizar** | Guardar os números do modelo com menos precisão (8 bits em vez de 32) para o arquivo ficar menor. Muda as respostas um pouco — por isso sempre se mede depois. |
| **Deriva (drift)** | Os dados que chegam hoje deixaram de se parecer com os do treino. O modelo continua respondendo; as respostas é que passam a valer menos. |
| **Baseline** | Um retrato de como eram os dados de treino, guardado junto do modelo, para dar com o que comparar depois. |

## scikit-learn para a borda

Modelos clássicos do sklearn são o caso mais comum de embarcado: pequenos,
rápidos, e presos ao Python enquanto vivem como `.pkl`. Exportar para ONNX
tira Python, NumPy e o próprio scikit-learn do dispositivo.

```bash
uv add "tempest-fastapi-sdk[modelops-onnx,modelops-sklearn]"
```

```python
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import edge_bundle

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)
model = RandomForestClassifier(n_estimators=20, random_state=0).fit(
    X_train, y_train
)


bundle = edge_bundle(
    model,                       # estimador ou Pipeline já treinado
    X_train[:50],                # só para dar forma ao grafo
    "dist/",
    name="classifier",
    verify_samples=X_test,       # dados de validação, não os de export
)

print(bundle.deployable)
print(bundle.verification.passed, bundle.verification.label_agreement)
```

### Três decisões que o SDK toma por você

| Decisão | Por quê |
| --- | --- |
| **float32**, não float64 | O sklearn trabalha em precisão dupla; runtime de borda quer simples. Metade da memória, e é a precisão que os aceleradores implementam. **Muda os números** — por isso a verificação. |
| **ZipMap desligado** | Por padrão o `skl2onnx` embrulha as probabilidades num `ZipMap`: um **dicionário por linha**. Cômodo em Python, inutilizável num runtime mínimo que não implementa o operador. |
| **Verificar sempre** | Um export que discorda em silêncio do modelo treinado é pior que um que falha, porque você o coloca em produção. |

### O que a medição mostrou

Rodando contra estimadores de verdade, três resultados que a doc prefere
dizer a deixar você descobrir:

!!! warning "Quantização int8 não se aplica à maioria dos modelos sklearn"
    Árvores, modelos lineares e scalers convertem para operadores
    `ai.onnx.ml`, cujos parâmetros são atributos de nó, não tensores de peso.
    Não há matriz para requantizar, e o quantizador recusa com `Failed to
    find proper ai.onnx domain`. O `edge_bundle` detecta e **pula com a
    razão**, em vez de falhar de forma opaca.

!!! warning "Otimizar e converter para `.ort` costuma **aumentar** o arquivo"
    Esses grafos têm kilobytes; os metadados adicionados superam o que é
    economizado. Por isso o `edge_bundle` entrega o **menor** artefato
    produzido, não o último — devolver um arquivo maior chamando de
    otimizado seria uma mentira que a ferramenta conta sozinha.

!!! danger "Árvore + classificação binária converte errado hoje"
    Com `skl2onnx` 1.20 e scikit-learn 1.9, um `RandomForestClassifier`
    binário gera um grafo cuja saída de probabilidade é um score em
    `[-1, 1]` em vez de `[0, 1]`, e os rótulos previstos discordam do
    estimador numa fração significativa das linhas. Multiclasse e modelos
    lineares estão corretos.

    Nenhuma opção do conversor resolve — `zipmap`, `raw_scores` e quatro
    opsets diferentes foram testados. O `export.warnings` sinaliza a
    combinação, e a verificação pega:

    ```python
    export = export_sklearn_to_onnx(model, X[:10], "m.onnx")
    if export.needs_verification:
        print(export.warnings[0])
    ```

    Alternativas: usar uma formulação multiclasse, um modelo linear, ou
    fixar versões que você validou.

### Com e sem GPU

**CPU (o caso comum de borda):** o ganho é dispensar Python e linkar um
runtime mínimo do ONNX Runtime — não a quantização, que como visto acima
raramente se aplica aqui.

**Com GPU:** mantenha o `.onnx` e escolha o provider ao carregar:

```python
import onnxruntime

session = onnxruntime.InferenceSession(
    "dist/classifier.onnx",
    providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
)
```

Meça antes de assumir que ajudou — modelos sklearn são pequenos, e o custo
de transferir para a GPU pode superar o ganho de computá-los lá:

```python
from tempest_fastapi_sdk.modelops import benchmark_onnx

profile = benchmark_onnx("dist/classifier.onnx", providers=["CUDAExecutionProvider"])
print(profile.runtime.latency_ms_median)
```


## Vim de um `.pkl`. E agora?

O time de treino entrega `joblib.dump(...)`, porque é o que todo notebook
escreve. O navegador não tem como usar isso: **pickle é um programa Python**,
não um dado. Rodar exigiria um runtime Python inteiro no browser — o que
mata offline, tamanho e o motivo do módulo existir.

A ponte é de **build**, não de request:

```python
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import edge_pipeline_from_pickle

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)


package = edge_pipeline_from_pickle(
    "artifacts/risk.pkl",
    X_train,
    "dist/risk",
    labels=y_train,
)
print(package.manifest.source.file, package.manifest.verified)
```

```text
risk.pkl True
```

O `.pkl` fica na esteira. O que vai para dispositivo e navegador é o pacote
ONNX — que o `edge_pipeline` já verifica contra as predições do próprio
objeto carregado.

!!! danger "Carregar pickle executa código arbitrário"
    `joblib.load` e `pickle.load` não são parsers: eles **executam
    instruções do arquivo**. Pickle de origem não confiável é execução
    remota de código, não é risco a ponderar.

    Por isso `load_sklearn_artifact` aceita **caminho local** e recusa URL
    com erro explícito — não como proteção (quem quiser baixa antes), mas
    para que não exista no código a forma "carregue o modelo desta URL",
    que é o que transforma um registry em superfície de RCE.

    Regra prática: `.pkl` só de artefato que a **sua** esteira gerou, em
    ambiente de build. Nunca de upload, nunca baixado por dispositivo. É
    exatamente a assimetria que a conversão resolve — **ONNX é dado,
    pickle é programa**.

!!! warning "Pickle não tem contrato de versão em que dá para confiar"
    Medido no scikit-learn 1.9: modelo serializado por uma versão e lido por
    outra **não emite aviso nenhum** e não guarda campo de versão — a
    divergência, quando importa, é silenciosa.

    Ler uma vez no build e publicar ONNX troca essa classe de problema por
    uma conversão que ou verifica ou recusa. O manifesto registra a versão
    do scikit-learn **que fez a conversão**, que é o único fato de versão
    que a cadeia tem para oferecer.

### O que a ponte faz além de `joblib.load`

**Recupera a ordem das colunas.** Modelo treinado com DataFrame guarda
`feature_names_in_`; a ponte lê e escreve no manifesto. É o campo que evita
o erro que nenhuma checagem de runtime pega — features certas na ordem
errada.

**Acha o modelo dentro do dict.** Esteira quase sempre despeja
`{"model": est, "auc": 0.91, ...}`. Com um só estimador dentro, resolve
sozinho; com dois, **recusa e lista o que achou** em vez de chutar:

```python
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import edge_pipeline_from_pickle

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)

X = X_test


package = edge_pipeline_from_pickle("bundle.pkl", X, "dist/", key="challenger")
```

**Grava a procedência.** Nome, SHA-256 e tamanho do `.pkl` vão para
`manifest.source`, então um modelo rodando num dispositivo daqui a seis
meses ainda responde "qual arquivo me gerou".

**Rejeita o que não prediz** com mensagem direta, em vez de deixar o erro
aparecer lá no conversor.

## Sem runtime nenhum: o formato compacto

ONNX no navegador custa **25,6 MB de WebAssembly** (6,0 MB gzipped) antes da
primeira predição. Contra isso, o modelo é ruído: floresta de 12 árvores são
20 KB.

Para um app cujo único modelo é tabular, o runtime **é** o download. Então
existe uma saída que descarta o runtime em vez do modelo:

```python
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import export_sklearn_to_compact

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)
model = RandomForestClassifier(n_estimators=20, random_state=0).fit(
    X_train, y_train
)


export = export_sklearn_to_compact(model, X_test, "dist/risk.tmc")
print(export.kind, export.size_bytes, export.verified)
```

```text
tree_ensemble 7476 True
```

!!! note "O tamanho depende da versão do conversor"
    Medido com `scikit-learn` 1.9.0 e `skl2onnx` 1.20.0. O número muda quando
    qualquer um dos dois muda — a versão anterior desta página dizia `13124`,
    43% acima. Trate como ordem de grandeza, não como constante.

Modelo linear é produto escalar. Árvore é comparação encadeada. Os dois cabem
em **1,49 KB** de JavaScript — o leitor mora no
[`tempest-react-sdk/tabular`](https://mauriciobenjamin700.github.io/tempest-react-sdk/tabular/),
e este exportador escreve o que ele lê.

No pacote de borda, sai junto:

```python
from pathlib import Path

from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import edge_pipeline

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)
model = RandomForestClassifier(n_estimators=20, random_state=0).fit(
    X_train, y_train
)


package = edge_pipeline(model, X_train, "dist/risk", labels=y_train, compact=True)
print([(r.kind, r.bytes) for r in package.manifest.runtimes])
```

```text
[('onnx', 19941), ('compact', 9608)]
```

O navegador escolhe a rota pelo manifesto.

### O que cobre, e o que recusa

| Cobre | Não cobre |
| --- | --- |
| Logística, linear, ridge, SGD, SVC linear | Gradient boosting (soma contribuições por link) |
| Árvore, floresta, extra-trees | MLP |
| Regressores dos mesmos | Qualquer transform que não seja `(x - offset) / escala` |
| `StandardScaler` / `MinMaxScaler` em Pipeline | Imputer, encoder, PCA |

!!! danger "Recusa é a feature"
    Um formato que ignorasse silenciosamente um passo do Pipeline geraria um
    modelo que roda e responde errado — pior que erro. Estimador ou
    transform fora da cobertura levanta `UnsupportedEstimatorError`
    **nomeando o `export_sklearn_to_onnx`**, que cobre tudo que este não
    cobre.

!!! check "Verificado contra o scikit-learn, e só isso vale"
    Reimplementar a aritmética de outra biblioteca só é defensável com a
    comparação: o exportador roda o arquivo escrito pelo decodificador de
    referência e compara com `predict`/`predict_proba` do próprio estimador.
    Discordou, **não grava** — levanta com a diferença medida.

    Do lado do navegador, os testes rodam contra fixtures geradas aqui junto
    das saídas do scikit-learn: 7 famílias, rótulos idênticos, probabilidades
    batendo em 5 casas.

### O arquivo é dado, nunca código

A alternativa clássica é gerar JavaScript com os limiares embutidos em `if`.
Isso produz algo que a página precisa avaliar — CSP estrita proíbe, e
revisor nenhum lê. Aqui o leitor é fixo e auditado; o modelo são arrays.

Layout `TMC1`: magic, `uint32` com o tamanho do header, header JSON e as
seções em arrays tipados. O header é preenchido até múltiplo de 8 bytes de
propósito — `Float32Array` no JavaScript não aceita offset desalinhado, e sem
isso o navegador teria que copiar cada seção em vez de mapear.

## Servir o modelo na borda

Exportar produz o arquivo. Isto é tudo entre o arquivo e uma resposta —
código que todo consumidor reescreve igual e erra do mesmo jeito.

```python
from tempest_fastapi_sdk.modelops import OnnxPredictor

predictor = OnnxPredictor("dist/classifier.onnx")
result = predictor.predict([[5.1, 3.5, 1.4, 0.2]])

print(result.labels, result.probabilities[0])
```

```text
[0] [0.98, 0.02, 0.0]
```

O predictor resolve o que você teria que resolver à mão: **qual é o input**
(o nome não é constante entre exportadores), **qual output é rótulo e qual é
score** (indexar `[1]` funciona até você servir um regressor), coerção de
dtype, e o *warmup* — a primeira chamada paga alocação e seleção de kernel.

!!! danger "Threads: a regra é o formato da carga, não o tamanho do aparelho"
    O default aqui é `intra_op_threads=1`, e o motivo não é o que parece.
    Não é que threads "atrapalham num aparelho pequeno" — é que num serviço
    com N requisições concorrentes, threads por requisição sobrecarregam a
    CPU e **todas** ficam mais lentas.

    Medido numa máquina de 12 cores, floresta de 300 árvores sobre 20
    features:

    | Threads | 1 linha | 1000 linhas |
    | --- | --- | --- |
    | 1 | 0,019 ms | 16,6 ms |
    | 2 | 0,013 ms | 8,2 ms |
    | 4 | 0,012 ms | 4,2 ms |
    | 8 | 0,010 ms | 2,3 ms |

    Lote escala quase linearmente. Grafo pequeno não: a mesma medição numa
    regressão logística deu 0,213 ms para 1000 linhas com 1 thread e 0,214 ms
    com 8 — não há trabalho para dividir.

    Então: **lote ou ensemble grande quer threads**; uma linha por vez num
    grafo pequeno é indiferente; serviço atendendo muita gente ao mesmo tempo
    quer este default. Meça no aparelho alvo:

    ```python
    from tempest_fastapi_sdk.modelops import benchmark_onnx

    profile = benchmark_onnx("dist/classifier.onnx", n_repetitions=200)
    print(profile.runtime.latency_ms_median)
    ```

### Com GPU

```python
from tempest_fastapi_sdk.modelops import OnnxPredictor


predictor = OnnxPredictor(
    "dist/classifier.onnx",
    providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
)
print(predictor.info.providers)
```

!!! warning "Sempre inclua o fallback de CPU"
    Sem ele, um problema de driver vira falha de carga em vez de resposta
    mais lenta. E cheque `info.providers`: o ONNX Runtime **cai para CPU em
    silêncio**, então o dispositivo que você acha que está na GPU pode não
    estar.

### Servir por HTTP

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.modelops import OnnxPredictor, make_prediction_router

app = FastAPI()
app.include_router(make_prediction_router(OnnxPredictor("dist/classifier.onnx")))
```

| Rota | O que faz |
| --- | --- |
| `POST /api/predict/` | Prediz para um lote de linhas |
| `GET /api/predict/model` | O que está carregado, providers **em uso**, threads |
| `POST /api/predict/model/sync` | Recarrega da registry (só com `source`) |

Linha de largura errada vira **422**, não 500 — é erro do cliente.

### Trocar o modelo sem redeploy

```python
from pathlib import Path

from fastapi import FastAPI

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk import ArtifactRegistry
from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.artifacts import ArtifactRegistry
from tempest_fastapi_sdk.modelops import (
    OnnxPredictor,
    RegistryModelSource,
    make_prediction_router,
)

from src.db.models import ModelVersion

# Num serviço, a sessão real vem de `db.get_session_context()`; aqui, do SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

predictor = OnnxPredictor("model.onnx")
registry = ArtifactRegistry(BaseRepository(session, model=ModelVersion))
app = FastAPI()


source = RegistryModelSource(registry, "fraud-classifier", cache_dir="models/")
app.include_router(make_prediction_router(predictor, source=source))
```

O dispositivo pergunta à `ArtifactRegistry` qual versão está ativa, baixa se
ainda não tiver, e recarrega. Chame `source.sync(predictor)` numa tarefa
periódica — é no-op quando já está na versão certa.

!!! check "Rollout ruim degrada para a versão anterior, nunca para nada"
    A sessão nova é construída **antes** de a antiga ser descartada. Um
    arquivo corrompido deixa o predictor servindo o modelo anterior em vez
    de tirar o dispositivo do ar. Uma frota que pode ficar muda por causa de
    um deploy é pior que uma que ocasionalmente fica desatualizada.

    Uma versão por arquivo no `cache_dir`, então rollback é um reload, não um
    novo download. Nada é apagado sozinho — num disco pequeno você quer
    decidir quando as versões antigas saem.


## O pacote de borda: um diretório, dois runtimes

`edge_bundle` responde "quanto cada estágio de otimização custa no meu
modelo". A pergunta seguinte é: **o que eu de fato publico, e como quem roda
sabe o que recebeu.**

```python
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import edge_pipeline

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)
model = RandomForestClassifier(n_estimators=20, random_state=0).fit(
    X_train, y_train
)


package = edge_pipeline(
    model,
    X_train,
    "dist/risk",
    name="risk",
    labels=y_train,
    feature_names=["idade", "renda", "tempo_casa", "score", "visitas"],
)
print(package.manifest.version, package.manifest.verified)
```

```text
cc17b06c76d4 True
```

Saem quatro arquivos, e você publica o diretório inteiro:

```text
dist/risk/
├── risk.onnx          o grafo
├── risk.onnx.gz       o mesmo, 10-13% do tamanho
├── baseline.json      referência de deriva, tirada do treino
└── manifest.json      o contrato
```

### Por que manifesto

Modelo publicado nunca é um arquivo só. Quem roda precisa da **ordem das
colunas** que gerou o treino, das classes que ele sabe responder, do digest
para saber se o download veio inteiro e da versão para saber se já tem essa.

Sem manifesto, isso tudo vive numa página de wiki que envelhece — e a falha
é silenciosa: modelo servido com duas colunas trocadas responde com
confiança e errado.

```python
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import load_edge_package

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)

rows = X_test.tolist()


loaded = load_edge_package("dist/risk")
result = loaded.predictor.predict(rows)
loaded.monitor.observe(rows, result)
```

Uma linha entrega predictor + monitor já ligado à baseline do pacote, com a
versão carimbada em todo relatório. O `manifest.json` é JSON puro e tem
`schema_version`: o mesmo diretório é servido como asset estático para o
[`tempest-react-sdk/tabular`](https://mauriciobenjamin700.github.io/tempest-react-sdk/tabular/),
que lê o mesmo arquivo no navegador.

!!! check "Download truncado falha como digest, não como erro de parse"
    `load_edge_package` confere o SHA-256 antes de carregar. Meio modelo
    vira uma mensagem dizendo isso — em vez de um erro de protobuf, ou pior,
    de nada.

!!! danger "Export que não reproduz o estimador não passa"
    O pipeline verifica contra as predições do próprio estimador e
    **levanta** se discordarem. É o único desfecho que ele se recusa a
    deixar passar quieto: um classificador de árvore **binário**
    respondia errado — probabilidade vinha como score em `[-1, 1]` — e o
    grafo rodava liso mesmo assim. Medindo com `skl2onnx` 1.20.0,
    `sklearn` 1.9.0 e `onnx` 1.22.0 fixos, movendo só o runtime, o culpado
    ficou claro: **`onnxruntime`**, não o conversor. Erro de 1.0 contra
    `predict_proba` na 1.27.0, 9.5e-08 na 1.28.0. O SDK exige
    `onnxruntime>=1.28`, e o export ainda avisa se encontrar um runtime
    antigo instalado à força.

## O que otimizar de verdade (medido)

Rodei os estágios em florestas reais de 10 a 300 árvores, exportadas do
scikit-learn. Três dos quatro não pagam:

| Estágio | 10 árvores | 50 árvores | 300 árvores |
| --- | --- | --- | --- |
| `.onnx` exportado | 381 KB | 1.955 KB | 12.061 KB |
| Otimização de grafo | 381 KB | 1.955 KB | 12.061 KB |
| Conversão `.ort` | 878 KB | 4.497 KB | 26.970 KB |
| **gzip** | **51 KB** | **226 KB** | **1.266 KB** |

- **Otimizar grafo não muda nada** (0,1 KB): operadores `ai.onnx.ml` são nós
  únicos, não há o que fundir.
- **`.ort` mais que dobra**, em toda escala. É formato de carregamento, não
  de compressão.
- **Quantização int8 não se aplica**: parâmetros de árvore e de linear são
  atributos de nó, não tensores.
- **gzip leva a 10-13%** e custa um header `Content-Encoding`.

Por isso `edge_pipeline` roda export → verify → baseline → manifest + gzip, e
só. Use `edge_bundle` quando quiser ver esses estágios medidos no **seu**
modelo em vez de confiar na tabela.

### O tamanho se decide antes de exportar

O estimador é o lever, não o pós-processamento. Floresta de 50 árvores, 20
features, 3 classes, acurácia em teste separado:

| `max_depth` | Tamanho | Acurácia | 1 linha |
| --- | --- | --- | --- |
| 3 | 36 KB | 0,797 | 0,0073 ms |
| 6 | 257 KB | 0,881 | 0,0075 ms |
| 12 | 1.275 KB | 0,918 | 0,0078 ms |
| sem limite | 1.444 KB | 0,922 | 0,0079 ms |

`max_depth=6` cabe em 1/5,6 do espaço por 4 pontos de acurácia. E repare na
última coluna: **latência não é o que você está trocando** — ela mal se move.
Na borda o que dói é byte, não milissegundo.

Mesma lição no número de árvores: 10 → 300 árvores multiplica o arquivo por
32 (381 KB → 12 MB) e a latência por 2,7 (0,0073 → 0,0199 ms/linha).

!!! tip "Entrada: passe array, não lista de listas"
    Medido em 1000 linhas: `float32` 2,65 ms, `float64` 2,66 ms (converter
    não custa nada mensurável), lista de listas do Python 2,99 ms. Só o
    último aparece, e em ~12%.

## Saber se o modelo ainda funciona

O dispositivo responde em 3 ms. Isso não diz nada sobre as respostas estarem
certas.

Em produção não há rótulo — ninguém avisa o dispositivo que ele acabou de
classificar errado —, então **acurácia não é medível ali**. O que dá para
medir é se o mundo ainda se parece com aquele do treino, e se a saída do
modelo mudou. São proxies, e a implementação diz isso em vez de fingir o
contrário.

```python
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import (
    OnnxPredictor,
    PredictionMonitor,
    baseline_from_samples,
)

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)
predictor = OnnxPredictor("model.onnx")
rows = X_test.tolist()


baseline = baseline_from_samples(X_train, labels=y_train)
monitor = PredictionMonitor(baseline=baseline)

result = predictor.predict(rows)
monitor.observe(rows, result)

report = monitor.report()
print(report.drift.verdict, report.drift.worst_psi)
```

```text
significant 3.95
```

### Três sinais, porque separam falhas diferentes

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

### A baseline sai do treino, não da produção

`baseline_from_samples` guarda **bordas de bin e proporções**, nunca as
linhas. São poucos kilobytes e nenhum registro — dá para versionar junto do
modelo.

```python
from pathlib import Path

from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)

baseline = X_train


Path("dist/baseline.json").write_text(baseline.model_dump_json())
```

!!! danger "Construir a baseline com tráfego de produção anula a medição"
    Ela passaria a descrever a população **já derivada** como normal. A
    baseline sai do conjunto de treino, no momento do treino.

### PSI, e o que ele não é

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

### Memória constante

Linhas são contadas nos bins e descartadas. O custo é
`n_features x n_bins` contadores, independentemente do tráfego — nada
acumula cópia das requisições, o que também significa que nenhum valor de
feature fica em memória para vazar em log ou dump.

A deriva é medida **por janela** (`DEFAULT_WINDOW_ROWS`, 1000 linhas). Ao
fechar, a janela vira a última medição completa e os contadores zeram, então
os números descrevem tráfego recente em vez de tudo desde o boot — que
levaria dias para reagir a uma mudança real.

### No HTTP e no Prometheus

```python
from fastapi import FastAPI
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import (
    PredictionMonitor,
    OnnxPredictor,
    PredictionMetrics,
    make_prediction_router,
)

baseline, _, _, _ = train_test_split(  # a matriz de treino que serve de referência para a deriva
    *load_iris(return_X_y=True), random_state=0
)
monitor = PredictionMonitor(baseline=baseline)
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

## Playbook: onde o tempo e os bytes realmente vão

Esta seção não é teoria — são medições feitas **com os instrumentos deste
SDK** (`benchmark_models`, `analyze_onnx`, `rank`, `PredictionMonitor`) numa
máquina de 12 cores, sobre 7 candidatos treinados no mesmo dataset (20
features, 3 classes, 6000 amostras).

### 1. Escolha o estimador, não o pós-processamento

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

### 2. Latência não é o eixo — o transporte é

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

### 3. Monitoramento tem custo, e ele foi medido

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

### 4. Cold start: o que custa ao subir

| Passo | Custo |
| --- | --- |
| `read_manifest` | 0,15 ms |
| `load_edge_package` (266 KB, com SHA-256) | 2,16 ms |
| mesma coisa, `verify_digest=False` | 2,00 ms |

Conferir o digest custa **0,16 ms** — mantenha ligado. E `read_manifest`
sendo 14x mais barato que carregar é o que torna viável perguntar "tem versão
nova?" em loop sem tocar no grafo.

### 5. Ordem de ataque

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

profiles = []  # results collected from a previous benchmark run


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

profiles = []  # results collected from a previous benchmark run


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
    HFQuantizationTarget,
    optimize_hf_onnx,
    quantize_hf_onnx,
)

optimized = optimize_hf_onnx("exports/distilbert", "exports/distilbert-o2")
quantized = quantize_hf_onnx(
    "exports/distilbert-o2",
    "exports/distilbert-int8",
    target=HFQuantizationTarget.AVX512_VNNI,
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
    `quantize_hf_bnb` aceita a flag porque algumas arquiteturas do Hub
    exigem. Ela roda código arbitrário do repositório remoto na sua máquina —
    só ligue para um repositório que você auditou.

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
- Para HuggingFace: exporte com `optimum-cli` via `uvx` (fora do projeto,
  para o teto de `transformers` não entrar), depois `optimize_hf_onnx` →
  `quantize_hf_onnx` com o `onnxruntime` que você já tem.
- Para edge: `.onnx` → `.ort` com `target_platform` e o
  `.required_operators.config` do build mínimo.
