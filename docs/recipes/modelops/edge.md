---
description: "Use para servir o modelo na borda com GPU ou HTTP e trocá-lo sem redeploy; contém o pacote de borda com manifesto e o que vale otimizar, medido."
---

# Servir na borda e publicar o pacote

Use para servir o modelo (com GPU ou por HTTP), trocá-lo sem redeploy, publicar o pacote de borda e saber o que vale otimizar.

Parte da receita [Modelops (export, bench, quantização)](../modelops.md).

## Servir o modelo na borda

Exportar produz o arquivo. Isto é tudo entre o arquivo e uma resposta —
código que todo consumidor reescreve igual e erra do mesmo jeito.

```python
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import OnnxPredictor, export_sklearn_to_onnx

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)
model = RandomForestClassifier(n_estimators=20, random_state=0).fit(
    X_train, y_train
)
export_sklearn_to_onnx(model, X_train[:10], "dist/classifier.onnx")


predictor = OnnxPredictor("dist/classifier.onnx")
result = predictor.predict([[5.1, 3.5, 1.4, 0.2]])

print(result.labels, result.probabilities[0])
```

```text
[0] [1.0000001192092896, 0.0, 0.0]
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

Linha de largura errada vira **422**, não 500 — é erro do cliente. Lote
maior que `max_rows` também (`DEFAULT_MAX_PREDICT_ROWS`, 10 000 linhas; passe
`max_rows=None` para tirar o limite): sem teto, uma única requisição decide
quanta memória e quantos segundos de inferência o dispositivo gasta com ela.

A inferência e o reload rodam numa thread (`asyncio.to_thread`), não no event
loop. Medido com um predictor que segura a thread por 1 s: na 0.299.0 o loop
ficava parado 0,992 s — nenhuma outra requisição era atendida nesse tempo —;
agora o atraso de agendamento fica abaixo de 1 ms.

!!! warning "O router não traz autenticação"
    `GET /model`, `POST /model/sync` e `GET /monitor` são rotas de operação:
    a segunda faz o dispositivo baixar e recarregar modelo. Proteja-as com
    `admin_dependencies` — `POST /` continua tão aberto quanto o resto do
    serviço — ou todas as rotas com `dependencies`:

    ```python
    import hmac

    from fastapi import Depends, FastAPI, Header, HTTPException

    from tempest_fastapi_sdk.modelops import OnnxPredictor, make_prediction_router

    OPERATOR_TOKEN: str = "troque-me"


    def require_operator(x_token: str = Header(default="")) -> None:
        if not hmac.compare_digest(x_token, OPERATOR_TOKEN):
            raise HTTPException(status_code=401, detail="operator token required")


    app = FastAPI()
    app.include_router(
        make_prediction_router(
            OnnxPredictor("dist/classifier.onnx"),
            admin_dependencies=[Depends(require_operator)],
        ),
    )
    ```

    `GET /model` devolve só o **nome** do arquivo em `path` (`classifier.onnx`),
    não o caminho absoluto, que descreve o disco do dispositivo e não serve ao
    cliente. `expose_model_path=True` volta a mostrar o caminho inteiro.

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
    A sessão nova é construída **e aquecida** (uma inferência descartável)
    **antes** de a antiga ser descartada. Um arquivo corrompido — ou um que
    carrega mas não consegue responder à entrada que o próprio grafo
    declara — faz `reload` levantar e deixa o predictor servindo o modelo
    anterior em vez de tirar o dispositivo do ar. Uma frota que pode ficar
    muda por causa de um deploy é pior que uma que ocasionalmente fica
    desatualizada.

    Uma versão por arquivo no `cache_dir`, então rollback é um reload, não um
    novo download. Nada é apagado sozinho — num disco pequeno você quer
    decidir quando as versões antigas saem.

O download cai num arquivo `.part` e só é renomeado para o nome final quando
termina, então uma conexão que cai no meio é baixada de novo no próximo sync
em vez de virar "versão em cache". `sync` segura um lock do fetch ao reload:
a tarefa periódica e um `POST /model/sync` ao mesmo tempo baixam e recarregam
a versão **uma** vez.

!!! tip "Verificar o que chegou: coluna `sha256`"
    O `ArtifactVersionMixin` não declara digest. Declare uma coluna `sha256`
    no seu model de versão, preenchida por quem publica o arquivo, e todo
    download é conferido antes de ser cacheado ou carregado — arquivo que não
    bate é apagado e o sync falha com `503`, com o modelo anterior no ar.
    Linha sem o atributo (ou vazia) baixa sem verificação; outro nome de
    coluna vai em `RegistryModelSource(..., checksum_field="digest")`, e
    `checksum_field=None` desliga a checagem.


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
    feature_names=["sepal_length", "sepal_width", "petal_length", "petal_width"],
)
print(package.manifest.version, package.manifest.verified)
```

```text
55a78cb2a1d4 True
```

`feature_names` precisa de um nome por coluna do grafo. Passar cinco nomes
para os quatro do iris levanta antes de escrever o manifesto:
`ValueError: 5 feature names for a model of 4 features: the manifest would
record a column order no reader can apply.`

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
    `predict_proba` na 1.27.0, 9.5e-08 na 1.28.0. O piso do SDK é
    `onnxruntime>=1.29.0` — acima da 1.28.0 que corrigiu o defeito —, e o
    export ainda avisa se encontrar um runtime antigo instalado à força.

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
