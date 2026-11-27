# Start here

Use the first time: an example that runs as is, which case is yours and the module's five words.

Part of the [Modelops (export, bench, quantization)](../modelops.md) recipe.

If you have never used this module, start with this block. It **runs as
is** — no data of yours needed, the dataset ships inside scikit-learn.

```bash
uv add "tempest-fastapi-sdk[modelops-onnx,modelops-sklearn]"
```

```python
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import edge_pipeline, load_edge_package

# 1. Any model, trained on the dataset that ships with scikit-learn.
data = load_iris()
X_train, X_test, y_train, y_test = train_test_split(
    data.data, data.target, test_size=0.3, random_state=0
)
model = RandomForestClassifier(n_estimators=20, max_depth=4, random_state=0)
model.fit(X_train, y_train)

# 2. Package it: this becomes a directory you publish.
package = edge_pipeline(
    model,
    X_train,
    "dist/flowers",
    name="flowers",
    labels=y_train,
    feature_names=list(data.feature_names),
    compact=True,
)
print("version:", package.manifest.version)
print("matches scikit-learn:", package.manifest.verified)

# 3. Load and predict, the way a device would.
loaded = load_edge_package("dist/flowers")
result = loaded.predictor.predict(X_test[:3])

print("predicted:", result.labels)
print("expected: ", model.predict(X_test[:3]).tolist())
```

```text
version: 5ab558270e27
matches scikit-learn: True
predicted: [2, 1, 0]
expected:  [2, 1, 0]
```

That is it: the model left Python. The `dist/flowers/` directory is what you
publish — a device or a browser loads from there.

!!! tip "What just happened"
    `edge_pipeline` trained nothing and optimised nothing. It **converted**
    the estimator into a format that runs without Python, **checked** that
    the conversion answers exactly like your model, and wrote a directory
    holding the model, a description of it, and a reference for detecting
    changed data later. If the check fails it **raises** instead of
    writing — a wrong conversion never leaves here quietly.

## Which case is mine?

| You have | Go to |
| --- | --- |
| A trained model in memory | The block above |
| A `.pkl` file from the training team | [It arrived as a `.pkl`](sklearn.md#it-arrived-as-a-pkl-now-what) |
| The model must run in a **browser** | [No runtime at all](sklearn.md#no-runtime-at-all-the-compact-format) + [tempest-react-sdk/tabular](https://mauriciobenjamin700.github.io/tempest-react-sdk/en/tabular/) |
| The model becomes an HTTP endpoint | [Serving the model on the device](edge.md#serving-the-model-on-the-device) |
| It is already live and you want to know if it still works | [Knowing whether the model still works](monitoring.md) |
| It is too slow or too big | [Playbook](playbook.md) |
| It is a deep-learning model (torch, transformers) | [Measure before you optimize](measure.md#measure-before-you-optimize) and the sections after it |

## Five words that come up constantly

| Word | What it means here |
| --- | --- |
| **ONNX** | A file format describing an already-trained model. Many programs can execute it, in many languages — which is how it takes Python off the device. |
| **Graph** | The model inside the ONNX file: the arithmetic and its order. "Graph" and "exported model" mean the same thing on these pages. |
| **Quantise** | Storing the model's numbers with less precision (8 bits instead of 32) so the file gets smaller. It changes the answers slightly — which is why you always measure afterwards. |
| **Drift** | The data arriving today has stopped resembling the training data. The model keeps answering; the answers are what stop being worth much. |
| **Baseline** | A snapshot of what the training data looked like, shipped with the model, so there is something to compare against later. |
