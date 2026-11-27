---
description: "Use para exportar scikit-learn, inclusive de um `.pkl`, para ONNX ou para o formato compacto sem runtime; contém GPU, pinagem de hash da release e o que o formato recusa."
---

# Exportar scikit-learn e `.pkl`

Use para exportar um modelo scikit-learn (inclusive vindo de `.pkl`) para ONNX ou para o formato compacto sem runtime.

Parte da receita [Modelops (export, bench, quantização)](../modelops.md).

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

!!! note "Árvore + classificação binária: defeito antigo do runtime, ainda vigiado"
    Um `RandomForestClassifier` **binário** exportado já respondeu errado:
    a probabilidade vinha como score em `[-1, 1]` em vez de `[0, 1]`, e os
    rótulos discordavam do estimador. A causa era o `onnxruntime` < 1.28,
    não o conversor (a medição está em
    [Por que manifesto](edge.md#por-que-manifesto)). O
    piso do SDK é `onnxruntime>=1.29.0`, então uma instalação normal não
    reproduz mais: com `onnxruntime` 1.29.0, `skl2onnx` 1.20.0 e
    scikit-learn 1.9.0, uma floresta binária de 50 árvores no
    `load_breast_cancer` exporta sem aviso e verifica com
    `label_agreement == 1.0` e diferença máxima de probabilidade `1.8e-07`.

    O aviso continua no código para o ambiente montado à força abaixo do
    piso: `export.warnings` só ganha a mensagem quando o estimador final é
    um ensemble de árvore binário **e** o `onnxruntime` instalado é anterior
    a `BINARY_TREE_FIXED_IN_ONNXRUNTIME` (hoje `(1, 28)`):

    ```python
    from sklearn.datasets import load_breast_cancer
    from sklearn.ensemble import RandomForestClassifier

    from tempest_fastapi_sdk.modelops import export_sklearn_to_onnx

    X, y = load_breast_cancer(return_X_y=True)
    model: RandomForestClassifier = RandomForestClassifier(
        n_estimators=50, random_state=0
    ).fit(X, y)
    export = export_sklearn_to_onnx(model, X[:10], "m.onnx")
    if export.needs_verification:
        print(export.warnings[0])
    ```

    Com o piso instalado, isso não imprime nada.

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

### Pinar o hash da release

Gravar o SHA-256 no manifesto responde "qual arquivo me gerou" **depois** do
fato. Para recusar um `.pkl` trocado **antes** de ele rodar, passe o hash que a
release registrou em `expected_sha256`:

```python
from sklearn.datasets import load_iris

from tempest_fastapi_sdk.modelops import (
    ArtifactDigestMismatchError,
    EdgePackage,
    LoadedArtifact,
    edge_pipeline_from_pickle,
    load_sklearn_artifact,
)

RELEASED_SHA256: str = (
    "sha256:e7b7c9febd99888341148a95224c21d846e040c50a83d3252d5ca390447e988b"
)

X_train, y_train = load_iris(return_X_y=True)

artifact: LoadedArtifact = load_sklearn_artifact(
    "artifacts/risk.pkl",
    expected_sha256=RELEASED_SHA256,
)
print(artifact.estimator_type)

try:
    package: EdgePackage = edge_pipeline_from_pickle(
        "artifacts/risk.pkl",
        X_train,
        "dist/risk",
        labels=y_train,
        expected_sha256=RELEASED_SHA256,
    )
except ArtifactDigestMismatchError as exc:
    print(exc.expected, exc.actual)
    raise
```

A comparação acontece **antes** do `joblib.load`. O arquivo é aberto uma vez
só: o hash é calculado por esse handle e o unpickle lê do mesmo handle, então
os bytes conferidos são os bytes que rodam. Com o arquivo divergente, a
chamada levanta `ArtifactDigestMismatchError` (subclasse de `ValueError`,
como a checagem de digest do `load_edge_package`) e nada é desserializado:

```text
refusing to load artifacts/risk.pkl: its SHA-256 is e7b7c9febd99888341148a95224c21d846e040c50a83d3252d5ca390447e988b, expected 0000000000000000000000000000000000000000000000000000000000000000. The file was replaced, truncated or is not the released artifact; it was not unpickled.
```

No `edge_pipeline_from_pickle` o parâmetro só é repassado ao
`load_sklearn_artifact`, então em divergência nenhum diretório de pacote é
criado.

**Formato aceito:** 64 dígitos hexadecimais, maiúsculos ou minúsculos, com
prefixo `sha256:` opcional (em qualquer caixa) e espaço nas pontas ignorado.
`E7B7...`, `sha256:e7b7...` e `SHA256:E7B7...` valem o mesmo. Qualquer outra
coisa é recusada antes de abrir o arquivo:

```text
expected_sha256 must be a 64-character hex digest, optionally prefixed with 'sha256:'; got 'abc'
```

!!! tip "De onde vem o hash"
    Calcule no mesmo passo da esteira que fez o `joblib.dump`
    (`sha256sum artifacts/risk.pkl`) e publique junto da release do modelo —
    na configuração do serviço, no registry, na nota da release. O hash só
    protege se vier de um lugar diferente do arquivo que ele confere.

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
[('onnx', 15324), ('compact', 7476)]
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
    Discordou, **levanta** com a diferença medida; o arquivo fica no disco só
    para inspeção, e a mensagem diz que ele não pode ser enviado.

    Os limiares de split viram float32 arredondados **para baixo**, não para
    o mais próximo. O scikit-learn guarda o limiar como ponto médio float64
    entre dois valores float32; quando os dois são vizinhos (`1.6999999` e
    `1.7000000` no iris), o arredondamento comum cai em cima do maior, e a
    linha de treino com exatamente esse valor trocava de lado. A floresta
    acima, sem `max_depth`, verificada nos próprios dados de treino, era
    recusada por isso até a 0.302.0.

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
