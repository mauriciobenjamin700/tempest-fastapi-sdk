"""A model format that needs no inference runtime to read.

ONNX in the browser costs a **25.6 MB WebAssembly runtime** (6.0 MB
gzipped) before the first prediction. Against that, the model itself is
noise: a 50-tree forest is 266 KB, a logistic regression is 631 bytes. For
an app whose only model is tabular, the runtime *is* the download.

So this format drops the runtime instead of the model. A linear model is a
dot product; a tree is a chain of comparisons. Both fit in about two
kilobytes of JavaScript — `tempest-react-sdk/tabular` ships the reader —
and this module writes what that reader consumes.

    from tempest_fastapi_sdk.modelops import export_sklearn_to_compact

    export = export_sklearn_to_compact(model, X_test, "dist/risk.tmc")
    print(export.kind, export.size_bytes, export.verified)

**It is not a replacement for ONNX, it is a trade.** ONNX covers every
estimator; this covers linear models and tree ensembles. ONNX is verified
by a runtime the whole industry tests; this is verified by comparing
against scikit-learn's own predictions at export time, and refusing to
write a file that disagrees.

**The file is data, never code.** The alternative approach — emitting
JavaScript with the thresholds baked into `if` statements — produces
something a page has to `eval`, which a strict CSP forbids and a reviewer
cannot read. Here the reader is fixed and audited, and the model is arrays.

Layout (``TMC1``):

* bytes 0-3: the ASCII magic ``TMC1``
* bytes 4-7: little-endian ``uint32``, the JSON header's length in bytes
* then the UTF-8 JSON header, describing the sections that follow, padded
  with spaces so the first section starts on an 8-byte boundary
* then each section's raw little-endian values, in header order

The padding is not cosmetic: a JavaScript ``Float32Array`` cannot be created
over an unaligned offset, so without it the browser reader would have to
copy every section instead of mapping it. JSON ignores the trailing spaces,
so both readers stay simple.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from tempest_fastapi_sdk.core.enums import BaseStrEnum
from tempest_fastapi_sdk.schemas.base import BaseSchema

if TYPE_CHECKING:
    from collections.abc import Sequence

COMPACT_MAGIC: bytes = b"TMC1"
"""Magic bytes opening every compact model file."""

COMPACT_SCHEMA_VERSION: int = 1
"""Version of the compact layout.

Read by `tempest-react-sdk/tabular` in another language, so a change here
breaks a consumer that this repository does not build. Bump only for a
breaking layout change.
"""

COMPACT_SUFFIX: str = ".tmc"
"""Conventional file extension."""

_SECTION_ALIGNMENT: int = 8
"""Byte boundary the first section starts on.

Eight, so every dtype the format uses lands aligned. A JavaScript typed
array cannot view an unaligned offset, and copying the arrays instead would
undo the point of a format meant to be mapped.
"""

_LEAF_MARKER: int = -1
"""``children_left`` value scikit-learn uses to mark a leaf."""

DEFAULT_TOLERANCE: float = 1e-5
"""Largest probability difference accepted when verifying an export.

Tighter than the ONNX export's tolerance because nothing here changes
precision on purpose: the arithmetic is the same float32 dot products and
comparisons scikit-learn performs, so a real disagreement means the format
lost information rather than rounded it.
"""


class CompactKind(BaseStrEnum):
    """Which reader a compact file needs.

    Attributes:
        LINEAR: Coefficients and intercepts; the reader does a dot product.
        TREE_ENSEMBLE: One or more trees averaged; the reader walks them.
    """

    LINEAR = "linear"
    TREE_ENSEMBLE = "tree_ensemble"


class CompactTask(BaseStrEnum):
    """What the model answers.

    Attributes:
        CLASSIFICATION: Class label plus per-class scores.
        REGRESSION: A single value per row.
    """

    CLASSIFICATION = "classification"
    REGRESSION = "regression"


class CompactLink(BaseStrEnum):
    """How raw scores become probabilities.

    Attributes:
        SOFTMAX: Multinomial logistic — exponentiate and normalise.
        SIGMOID: Binary logistic — one score, mirrored into two classes.
        NORMALIZE: Already non-negative and summing to one per tree; the
            ensemble average only needs renormalising.
        IDENTITY: Regression; the score is the answer.
    """

    SOFTMAX = "softmax"
    SIGMOID = "sigmoid"
    NORMALIZE = "normalize"
    IDENTITY = "identity"


class CompactExport(BaseSchema):
    """What :func:`export_sklearn_to_compact` produced.

    Attributes:
        path (str): The written file.
        size_bytes (int): Its size.
        kind (CompactKind): Which reader it needs.
        task (CompactTask): Classification or regression.
        n_features (int): Values expected per row.
        n_trees (int): Trees in the ensemble; ``0`` for a linear model.
        classes (list[str]): Class labels in score-column order.
        estimator (str): Class name of the exported estimator.
        verified (bool | None): Whether the written file reproduces the
            estimator. ``None`` when the check was skipped — which is
            worth seeing rather than assuming.
        max_abs_diff (float | None): Largest probability difference found
            during that check.
    """

    path: str = Field(
        title="Path",
        description="The written file.",
        examples=["dist/risk.tmc"],
    )
    size_bytes: int = Field(
        default=0,
        title="Size",
        description="Size of the written file.",
        examples=[148_400],
    )
    kind: CompactKind = Field(
        default=CompactKind.LINEAR,
        title="Kind",
        description="Which reader the file needs.",
    )
    task: CompactTask = Field(
        default=CompactTask.CLASSIFICATION,
        title="Task",
        description="Classification or regression.",
    )
    n_features: int = Field(
        default=0,
        title="Features",
        description="Values expected per row.",
        examples=[20],
    )
    n_trees: int = Field(
        default=0,
        title="Trees",
        description="Trees in the ensemble.",
        examples=[50],
    )
    classes: list[str] = Field(
        default_factory=list,
        title="Classes",
        description="Class labels in score-column order.",
    )
    estimator: str = Field(
        default="",
        title="Estimator",
        description="Class name of the exported estimator.",
        examples=["RandomForestClassifier"],
    )
    verified: bool | None = Field(
        default=None,
        title="Verified",
        description="Whether the file reproduces the estimator.",
    )
    max_abs_diff: float | None = Field(
        default=None,
        title="Max abs diff",
        description="Largest probability difference during verification.",
    )


class UnsupportedEstimatorError(TypeError):
    """The estimator has no compact representation.

    Raised instead of writing something approximate: a format that silently
    dropped a step would produce a model that runs and answers wrongly,
    which is worse than an error naming the ONNX route.
    """


def _final_step(estimator: Any) -> Any:
    """Return the estimator that actually predicts.

    Args:
        estimator (Any): An estimator or a Pipeline.

    Returns:
        Any: The last step of a Pipeline, or the estimator itself.
    """
    steps = getattr(estimator, "steps", None)
    return steps[-1][1] if steps else estimator


def _preprocessing(estimator: Any, n_features: int) -> dict[str, list[float]] | None:
    """Fold a Pipeline's scaler into mean/scale vectors.

    Only the two scalers whose transform is exactly ``(x - offset) / scale``
    are representable. Anything else — an imputer, an encoder, a
    transformer with learned structure — is refused rather than ignored,
    because ignoring it would produce a model that predicts on the wrong
    numbers.

    Args:
        estimator (Any): The estimator or Pipeline.
        n_features (int): Expected feature count.

    Returns:
        dict[str, list[float]] | None: The offsets and scales, or ``None``
        when there is no preprocessing.

    Raises:
        UnsupportedEstimatorError: When a step cannot be folded.
    """
    steps = getattr(estimator, "steps", None)
    if not steps:
        return None

    offset = [0.0] * n_features
    scale = [1.0] * n_features
    for _name, step in steps[:-1]:
        kind = type(step).__name__
        if kind == "StandardScaler":
            mean = getattr(step, "mean_", None)
            deviation = getattr(step, "scale_", None)
            offset = [float(value) for value in mean] if mean is not None else offset
            scale = (
                [float(value) for value in deviation]
                if deviation is not None
                else scale
            )
        elif kind == "MinMaxScaler":
            minimum = step.data_min_
            span = step.data_range_
            offset = [float(value) for value in minimum]
            scale = [float(value) if value else 1.0 for value in span]
        else:
            raise UnsupportedEstimatorError(
                f"the compact format cannot fold a {kind} step. Supported "
                "preprocessing is StandardScaler and MinMaxScaler; export this "
                "pipeline with export_sklearn_to_onnx instead.",
            )
    return {"offset": offset, "scale": scale}


def _class_type(model: Any) -> str:
    """Name the type scikit-learn used for this model's class labels.

    Recorded so the browser reader can hand back ``0`` where scikit-learn
    had an integer and ``"spam"`` where it had a string — the ONNX route
    does exactly that, and two routes over the same model that disagree on
    the type of a label is a bug waiting for the day someone switches.

    Args:
        model (Any): The fitted estimator.

    Returns:
        str: ``"int"``, ``"float"`` or ``"str"``.
    """
    classes = getattr(model, "classes_", None)
    if classes is None:
        return "str"
    kind = getattr(getattr(classes, "dtype", None), "kind", "U")
    if kind in "iu":
        return "int"
    if kind == "f":
        return "float"
    return "str"


def _linear_sections(model: Any, numpy: Any) -> tuple[dict[str, Any], list[Any]]:
    """Build the header fields and arrays for a linear model.

    Args:
        model (Any): The fitted linear estimator.
        numpy (Any): The imported numpy module.

    Returns:
        tuple[dict[str, Any], list[Any]]: Header fragment and the arrays.
    """
    coefficients = numpy.atleast_2d(numpy.asarray(model.coef_, dtype="float32"))
    intercept = numpy.atleast_1d(numpy.asarray(model.intercept_, dtype="float32"))

    classes = [str(value) for value in getattr(model, "classes_", [])]
    if classes:
        binary = len(classes) == 2 and coefficients.shape[0] == 1
        link = CompactLink.SIGMOID if binary else CompactLink.SOFTMAX
        task = CompactTask.CLASSIFICATION
    else:
        link = CompactLink.IDENTITY
        task = CompactTask.REGRESSION

    header = {
        "kind": CompactKind.LINEAR.value,
        "task": task.value,
        "link": link.value,
        "classes": classes,
        "class_type": _class_type(model),
        "n_outputs": int(coefficients.shape[0]),
    }
    return header, [
        ("coef", "float32", coefficients.reshape(-1)),
        ("intercept", "float32", intercept),
    ]


def _tree_sections(model: Any, numpy: Any) -> tuple[dict[str, Any], list[Any]]:
    """Build the header fields and arrays for a tree or forest.

    Leaves are packed rather than stored per node: a leaf's ``feature``
    entry holds ``-1 - slot``, so the reader tells leaf from split by the
    sign and finds its values without a second index. On a 50-tree forest
    that removes about half the value array.

    Classifier leaves are normalised to a distribution per tree, which is
    what scikit-learn averages when a forest predicts — storing raw counts
    would weight big leaves twice.

    Args:
        model (Any): The fitted tree or ensemble.
        numpy (Any): The imported numpy module.

    Returns:
        tuple[dict[str, Any], list[Any]]: Header fragment and the arrays.
    """
    trees = list(getattr(model, "estimators_", [model]))
    flat_trees = []
    for entry in trees:
        flat_trees.extend(entry) if isinstance(
            entry, numpy.ndarray
        ) else flat_trees.append(entry)

    classes = [str(value) for value in getattr(model, "classes_", [])]
    task = CompactTask.CLASSIFICATION if classes else CompactTask.REGRESSION
    n_outputs = len(classes) if classes else 1

    features: list[int] = []
    thresholds: list[float] = []
    lefts: list[int] = []
    rights: list[int] = []
    values: list[float] = []
    offsets: list[int] = [0]

    for fitted in flat_trees:
        tree = fitted.tree_
        base = len(features)
        for node in range(tree.node_count):
            left = int(tree.children_left[node])
            right = int(tree.children_right[node])
            if left == _LEAF_MARKER:
                slot = len(values) // n_outputs
                features.append(-1 - slot)
                thresholds.append(0.0)
                lefts.append(-1)
                rights.append(-1)
                raw = numpy.asarray(tree.value[node], dtype="float64").reshape(-1)
                if task is CompactTask.CLASSIFICATION:
                    total = float(raw.sum())
                    raw = raw / total if total else raw
                values.extend(float(value) for value in raw[:n_outputs])
            else:
                features.append(int(tree.feature[node]))
                thresholds.append(float(tree.threshold[node]))
                lefts.append(base + left)
                rights.append(base + right)
        offsets.append(len(features))

    header = {
        "kind": CompactKind.TREE_ENSEMBLE.value,
        "task": task.value,
        "link": (
            CompactLink.NORMALIZE.value
            if task is CompactTask.CLASSIFICATION
            else CompactLink.IDENTITY.value
        ),
        "classes": classes,
        "class_type": _class_type(model),
        "n_outputs": n_outputs,
        "n_trees": len(flat_trees),
    }
    return header, [
        ("node_feature", "int32", numpy.asarray(features, dtype="int32")),
        ("node_threshold", "float32", numpy.asarray(thresholds, dtype="float32")),
        ("node_left", "int32", numpy.asarray(lefts, dtype="int32")),
        ("node_right", "int32", numpy.asarray(rights, dtype="int32")),
        ("leaf_value", "float32", numpy.asarray(values, dtype="float32")),
        ("tree_offset", "int32", numpy.asarray(offsets, dtype="int32")),
    ]


_LINEAR_TYPES: frozenset[str] = frozenset(
    {
        "LogisticRegression",
        "LinearRegression",
        "Ridge",
        "RidgeClassifier",
        "SGDClassifier",
        "SGDRegressor",
        "LinearSVC",
        "LinearSVR",
        "Perceptron",
    },
)
"""Estimators whose decision function is a plain dot product."""

_TREE_TYPES: frozenset[str] = frozenset(
    {
        "DecisionTreeClassifier",
        "DecisionTreeRegressor",
        "ExtraTreeClassifier",
        "ExtraTreeRegressor",
        "RandomForestClassifier",
        "RandomForestRegressor",
        "ExtraTreesClassifier",
        "ExtraTreesRegressor",
    },
)
"""Estimators the tree reader can walk.

Gradient boosting is deliberately absent: its trees sum raw contributions
through a link function and an init estimator, which is a different reader.
Export those to ONNX until this format grows one.
"""


def export_sklearn_to_compact(
    estimator: Any,
    verify_samples: Any = None,
    output_path: str | Path = "model.tmc",
    *,
    feature_names: Sequence[str] | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
) -> CompactExport:
    """Write a fitted estimator as a runtime-free compact model.

    Example:

        >>> export = export_sklearn_to_compact(model, X_test, "dist/risk.tmc")
        >>> export.kind, export.verified
        (<CompactKind.TREE_ENSEMBLE: 'tree_ensemble'>, True)

    The result is read by `tempest-react-sdk/tabular`'s `CompactPredictor`,
    which needs no WebAssembly and no ONNX Runtime — the whole reader is
    about two kilobytes.

    Args:
        estimator (Any): A fitted linear model, tree, forest, or a Pipeline
            of ``StandardScaler``/``MinMaxScaler`` plus one of those.
        verify_samples (Any): Rows to check the written file against the
            estimator's own predictions. Strongly recommended: this format
            is verified by that comparison and nothing else. Pass ``False``
            to skip deliberately.
        output_path (str | Path): Where to write.
        feature_names (Sequence[str] | None): Column order to record.
            Taken from ``feature_names_in_`` when the estimator has one.
        tolerance (float): Largest probability difference accepted.

    Returns:
        CompactExport: Where it was written, what it holds, and whether it
        reproduces the estimator.

    Raises:
        UnsupportedEstimatorError: When the estimator (or a pipeline step)
            has no compact representation. The message names the ONNX
            route, which covers everything this does not.
        ValueError: When the written file disagrees with the estimator.
    """
    import numpy

    final = _final_step(estimator)
    kind_name = type(final).__name__

    if kind_name in _LINEAR_TYPES:
        header, sections = _linear_sections(final, numpy)
        n_features = int(numpy.atleast_2d(final.coef_).shape[1])
    elif kind_name in _TREE_TYPES:
        header, sections = _tree_sections(final, numpy)
        n_features = int(final.n_features_in_)
    else:
        raise UnsupportedEstimatorError(
            f"{kind_name} has no compact representation. This format covers "
            "linear models and tree ensembles; export it with "
            "export_sklearn_to_onnx and serve it through TabularPredictor "
            "instead.",
        )

    recorded = getattr(estimator, "feature_names_in_", None)
    names = (
        [str(name) for name in feature_names]
        if feature_names is not None
        else [str(name) for name in recorded]
        if recorded is not None
        else []
    )

    header.update(
        {
            "schema_version": COMPACT_SCHEMA_VERSION,
            "n_features": n_features,
            "feature_names": names,
            "estimator": kind_name,
            "preprocess": _preprocessing(estimator, n_features),
            "sections": [
                {"name": name, "dtype": dtype, "length": int(array.size)}
                for name, dtype, array in sections
            ],
        },
    )

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
    padding = (-(len(COMPACT_MAGIC) + 4 + len(encoded))) % _SECTION_ALIGNMENT
    encoded += b" " * padding
    with path.open("wb") as handle:
        handle.write(COMPACT_MAGIC)
        handle.write(struct.pack("<I", len(encoded)))
        handle.write(encoded)
        for _, _, array in sections:
            handle.write(numpy.ascontiguousarray(array).tobytes())

    verified: bool | None = None
    difference: float | None = None
    if verify_samples is not False and verify_samples is not None:
        verified, difference = _verify(estimator, path, verify_samples, tolerance)
        if not verified:
            raise ValueError(
                f"the compact export of {kind_name} does not reproduce the "
                f"estimator (largest probability difference {difference:.2e} "
                f"against a tolerance of {tolerance:.0e}). The file was written "
                "for inspection but must not be shipped.",
            )

    return CompactExport(
        path=str(path),
        size_bytes=path.stat().st_size,
        kind=CompactKind(header["kind"]),
        task=CompactTask(header["task"]),
        n_features=n_features,
        n_trees=int(header.get("n_trees", 0)),
        classes=list(header["classes"]),
        estimator=kind_name,
        verified=verified,
        max_abs_diff=difference,
    )


def read_compact(path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read a compact file back into its header and arrays.

    The reference decoder: the browser has its own, and these two agreeing
    is what makes the format a contract rather than a convention.

    Args:
        path (str | Path): The file to read.

    Returns:
        tuple[dict[str, Any], dict[str, Any]]: The header, and the sections
        as numpy arrays keyed by name.

    Raises:
        ValueError: When the magic bytes or schema version do not match.
    """
    import numpy

    raw = Path(path).read_bytes()
    if raw[:4] != COMPACT_MAGIC:
        raise ValueError(
            f"{path} is not a compact model file (magic was {raw[:4]!r})",
        )
    (length,) = struct.unpack("<I", raw[4:8])
    header = json.loads(raw[8 : 8 + length].decode("utf-8"))
    if header.get("schema_version") != COMPACT_SCHEMA_VERSION:
        raise ValueError(
            f"{path} uses compact schema {header.get('schema_version')}, "
            f"this reader understands {COMPACT_SCHEMA_VERSION}",
        )

    arrays: dict[str, Any] = {}
    cursor = 8 + length
    for section in header["sections"]:
        dtype = numpy.dtype(section["dtype"]).newbyteorder("<")
        size = section["length"] * dtype.itemsize
        arrays[section["name"]] = numpy.frombuffer(
            raw[cursor : cursor + size],
            dtype=dtype,
        )
        cursor += size
    return header, arrays


def predict_compact(path: str | Path, rows: Any) -> tuple[list[Any], list[list[float]]]:
    """Predict with the reference decoder.

    Mirrors what the browser reader does, step for step, so a disagreement
    between them is a format bug rather than a language difference.

    Args:
        path (str | Path): The compact file.
        rows (Any): A 2-D array of feature values.

    Returns:
        tuple[list[Any], list[list[float]]]: Labels and per-class scores;
        the scores are empty for a regression model.
    """
    import numpy

    header, arrays = read_compact(path)
    values = numpy.asarray(getattr(rows, "values", rows), dtype="float64")
    if values.ndim == 1:
        values = values.reshape(1, -1)

    preprocess = header.get("preprocess")
    if preprocess:
        offset = numpy.asarray(preprocess["offset"], dtype="float64")
        scale = numpy.asarray(preprocess["scale"], dtype="float64")
        values = (values - offset) / scale

    if header["kind"] == CompactKind.LINEAR.value:
        coefficients = arrays["coef"].reshape(header["n_outputs"], header["n_features"])
        scores = values @ coefficients.T + arrays["intercept"]
    else:
        scores = _walk_trees(header, arrays, values, numpy)

    return _finish(header, scores, numpy)


def _walk_trees(
    header: dict[str, Any],
    arrays: dict[str, Any],
    values: Any,
    numpy: Any,
) -> Any:
    """Average every tree's leaf values for every row.

    **The comparison happens in float32**, because that is what
    scikit-learn does: `sklearn.tree` casts its input to float32 before
    traversing, so a threshold like 5.099999904632568 (a float32 value
    widened for storage) and an input of 5.1 compare *equal* there and go
    left. Comparing in float64 sends that row right instead. On a 20-tree
    iris forest that single rule changed one tree's answer for one row —
    caught by the export verification, which is why this format has one.

    Args:
        header (dict[str, Any]): The parsed header.
        arrays (dict[str, Any]): The decoded sections.
        values (Any): Preprocessed rows.
        numpy (Any): The imported numpy module.

    Returns:
        Any: Score matrix, rows by outputs.
    """
    feature = arrays["node_feature"]
    threshold = arrays["node_threshold"]
    left = arrays["node_left"]
    right = arrays["node_right"]
    leaf = arrays["leaf_value"]
    offsets = arrays["tree_offset"]
    outputs = header["n_outputs"]

    totals = numpy.zeros((values.shape[0], outputs), dtype="float64")
    routed = values.astype("float32")
    for index in range(values.shape[0]):
        row = routed[index]
        for tree in range(len(offsets) - 1):
            node = int(offsets[tree])
            while feature[node] >= 0:
                node = int(
                    left[node]
                    if row[feature[node]] <= threshold[node]
                    else right[node],
                )
            slot = -1 - int(feature[node])
            totals[index] += leaf[slot * outputs : (slot + 1) * outputs]
    return totals / (len(offsets) - 1)


def _finish(header: dict[str, Any], scores: Any, numpy: Any) -> tuple[Any, Any]:
    """Apply the link function and pick labels.

    Args:
        header (dict[str, Any]): The parsed header.
        scores (Any): Raw scores per row.
        numpy (Any): The imported numpy module.

    Returns:
        tuple[Any, Any]: Labels and probabilities.
    """
    link = header["link"]
    classes = header["classes"]

    if link == CompactLink.IDENTITY.value:
        return [float(value) for value in scores.reshape(-1)], []

    if link == CompactLink.SIGMOID.value:
        positive = 1.0 / (1.0 + numpy.exp(-scores.reshape(-1)))
        probabilities = numpy.stack([1.0 - positive, positive], axis=1)
    elif link == CompactLink.SOFTMAX.value:
        shifted = scores - scores.max(axis=1, keepdims=True)
        exponentiated = numpy.exp(shifted)
        probabilities = exponentiated / exponentiated.sum(axis=1, keepdims=True)
    else:
        totals = scores.sum(axis=1, keepdims=True)
        probabilities = numpy.divide(
            scores,
            numpy.where(totals == 0, 1.0, totals),
        )

    picked = probabilities.argmax(axis=1)
    cast = {"int": int, "float": float}.get(header.get("class_type", "str"), str)
    labels = [
        cast(classes[index]) if classes else int(index) for index in picked.tolist()
    ]
    return labels, probabilities.tolist()


def _verify(
    estimator: Any,
    path: Path,
    samples: Any,
    tolerance: float,
) -> tuple[bool, float | None]:
    """Compare the written file against the estimator's own predictions.

    Args:
        estimator (Any): The fitted estimator.
        path (Path): The written compact file.
        samples (Any): Rows to compare on.
        tolerance (float): Largest accepted probability difference.

    Returns:
        tuple[bool, float | None]: Whether it passed, and the largest
        difference seen.
    """
    import numpy

    labels, probabilities = predict_compact(path, samples)
    expected_labels = [str(value) for value in estimator.predict(samples)]

    if hasattr(estimator, "predict_proba") and probabilities:
        expected = numpy.asarray(estimator.predict_proba(samples), dtype="float64")
        difference = float(numpy.abs(expected - numpy.asarray(probabilities)).max())
    else:
        expected_values = numpy.asarray(estimator.predict(samples), dtype="float64")
        difference = float(
            numpy.abs(expected_values - numpy.asarray(labels, dtype="float64")).max(),
        )
        return difference <= max(tolerance, 1e-4) * max(
            1.0,
            float(numpy.abs(expected_values).max()),
        ), difference

    agreed = [str(value) for value in labels] == expected_labels
    return bool(agreed and difference <= tolerance), difference


__all__: list[str] = [
    "COMPACT_MAGIC",
    "COMPACT_SCHEMA_VERSION",
    "COMPACT_SUFFIX",
    "DEFAULT_TOLERANCE",
    "CompactExport",
    "CompactKind",
    "CompactLink",
    "CompactTask",
    "UnsupportedEstimatorError",
    "export_sklearn_to_compact",
    "predict_compact",
    "read_compact",
]
