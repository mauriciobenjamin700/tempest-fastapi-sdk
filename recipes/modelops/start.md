# Comece por aqui

Use na primeira vez: um exemplo que roda como está, qual é o seu caso e as cinco palavras do módulo.

Parte da receita [Modelops (export, bench, quantização)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/modelops.md).

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

## Qual é o meu caso?

| Você tem | Vá para |
| --- | --- |
| Um modelo treinado na memória | O bloco acima |
| Um arquivo `.pkl` do time de treino | [Vim de um `.pkl`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/modelops/sklearn.md#vim-de-um-pkl-e-agora) |
| O modelo precisa rodar num **navegador** | [Sem runtime nenhum](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/modelops/sklearn.md#sem-runtime-nenhum-o-formato-compacto) + [tempest-react-sdk/tabular](https://mauriciobenjamin700.github.io/tempest-react-sdk/tabular/) |
| O modelo vai virar um endpoint HTTP | [Servir o modelo na borda](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/modelops/edge.md#servir-o-modelo-na-borda) |
| Já está no ar e você quer saber se ainda funciona | [Saber se o modelo ainda funciona](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/modelops/monitoring.md) |
| Está lento ou grande demais | [Playbook](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/modelops/playbook.md) |
| É um modelo de deep learning (torch, transformers) | [Medir antes de otimizar](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/modelops/measure.md#medir-antes-de-otimizar) e as seções seguintes |

## Cinco palavras que aparecem o tempo todo

| Palavra | O que quer dizer aqui |
| --- | --- |
| **ONNX** | Formato de arquivo que descreve um modelo já treinado. Vários programas sabem executá-lo, em várias linguagens — por isso ele tira o Python do dispositivo. |
| **Grafo** | O modelo dentro do arquivo ONNX: as contas e a ordem delas. "Grafo" e "modelo exportado" são a mesma coisa nestas páginas. |
| **Quantizar** | Guardar os números do modelo com menos precisão (8 bits em vez de 32) para o arquivo ficar menor. Muda as respostas um pouco — por isso sempre se mede depois. |
| **Deriva (drift)** | Os dados que chegam hoje deixaram de se parecer com os do treino. O modelo continua respondendo; as respostas é que passam a valer menos. |
| **Baseline** | Um retrato de como eram os dados de treino, guardado junto do modelo, para dar com o que comparar depois. |
