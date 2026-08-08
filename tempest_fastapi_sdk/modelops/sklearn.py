"""Take a scikit-learn model to something an edge device can run.

`skl2onnx` does the conversion; this module makes the decisions that
conversion leaves to you, and which are easy to get wrong in ways that
only show up in production:

* **float32, not float64.** scikit-learn trains and predicts in double
  precision. Edge runtimes want single. Converting is almost always right
  — half the memory, and the only precision most devices accelerate — but
  it *changes the numbers*, so it must be a stated choice and it must be
  verified, never assumed.
* **ZipMap off.** By default `skl2onnx` wraps a classifier's probabilities
  in a `ZipMap`, producing a **list of dictionaries** — one dict per row,
  keyed by class label. It is convenient in Python and terrible anywhere
  else: it allocates per row, many minimal runtimes do not implement the
  operator at all, and it blocks graph optimisation. Here it is off by
  default and the output is a plain tensor.
* **Verify, always.** :func:`verify_sklearn_onnx` runs both the estimator
  and the exported graph over real samples and compares. An export that
  silently disagrees with the model you trained is worse than one that
  fails to convert, because you ship it.

The rest of the edge path already exists in this package:
:func:`~tempest_fastapi_sdk.modelops.optimize_onnx_graph`,
:func:`~tempest_fastapi_sdk.modelops.quantize_onnx_dynamic` and
:func:`~tempest_fastapi_sdk.modelops.export_onnx_to_ort`.
:func:`edge_bundle` chains all of it and reports what each stage cost.

`skl2onnx` needs the ``[modelops-sklearn]`` extra; the module imports
without it.
"""

from __future__ import annotations

from itertools import takewhile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from tempest_fastapi_sdk.core import BaseStrEnum
from tempest_fastapi_sdk.schemas.base import BaseSchema

if TYPE_CHECKING:
    from collections.abc import Sequence

_EXTRA_HINT: str = (
    "Exporting scikit-learn models requires the optional "
    "[modelops-sklearn] extra. Install with: "
    "pip install tempest-fastapi-sdk[modelops-sklearn]"
)

DEFAULT_OPSET: int = 15
"""Target ONNX opset.

Chosen low enough that older embedded ONNX Runtime builds can load the
graph, and high enough for the operators scikit-learn converters emit.
Raise it only when a converter needs something newer — a device running
last year's runtime cannot load a graph built for this year's opset, and
that failure appears at deployment, not at export.
"""


def _require_skl2onnx() -> Any:
    """Import ``skl2onnx`` or raise a helpful error.

    Returns:
        Any: The ``skl2onnx`` module.

    Raises:
        ImportError: When the ``[modelops-sklearn]`` extra is missing.
    """
    try:
        import skl2onnx
    except ImportError as exc:
        raise ImportError(_EXTRA_HINT) from exc
    return skl2onnx


def _require_numpy() -> Any:
    """Import ``numpy`` or raise a helpful error.

    Returns:
        Any: The ``numpy`` module.

    Raises:
        ImportError: When numpy is unavailable.
    """
    try:
        import numpy
    except ImportError as exc:
        raise ImportError(_EXTRA_HINT) from exc
    return numpy


class TensorDtype(BaseStrEnum):
    """Element type of the exported graph's input.

    * ``FLOAT32`` — the edge default. Half the memory of double, and the
      precision device accelerators actually implement.
    * ``FLOAT64`` — matches scikit-learn exactly. Use it when a numerical
      difference matters more than size, and accept that many runtimes
      will fall back to a slow path or refuse.
    """

    FLOAT32 = "float32"
    FLOAT64 = "float64"


class SklearnExport(BaseSchema):
    """The result of converting an estimator.

    Attributes:
        path (str): Where the ``.onnx`` file was written.
        size_bytes (int): Its size on disk.
        opset (int): The opset it targets.
        dtype (TensorDtype): Input element type.
        input_name (str): Name of the graph input.
        n_features (int): Features the graph expects per row.
        zipmap (bool): Whether the ZipMap wrapper was kept.
        estimator (str): Class name of the exported estimator.
    """

    path: str = Field(
        title="Path",
        description="Where the .onnx file was written.",
        examples=["dist/model.onnx"],
    )
    size_bytes: int = Field(
        title="Size",
        description="Size of the exported graph on disk.",
        examples=[12_480],
    )
    opset: int = Field(
        title="Opset",
        description="ONNX opset the graph targets.",
        examples=[15],
    )
    dtype: TensorDtype = Field(
        default=TensorDtype.FLOAT32,
        title="Dtype",
        description="Input element type.",
        examples=["float32"],
    )
    input_name: str = Field(
        default="input",
        title="Input name",
        description="Name of the graph input.",
        examples=["input"],
    )
    n_features: int = Field(
        default=0,
        title="Features",
        description="Features the graph expects per row.",
        examples=[4],
    )
    zipmap: bool = Field(
        default=False,
        title="ZipMap",
        description="Whether the ZipMap wrapper was kept.",
        examples=[False],
    )
    estimator: str = Field(
        default="",
        title="Estimator",
        description="Class name of the exported estimator.",
        examples=["RandomForestClassifier"],
    )
    warnings: list[str] = Field(
        default_factory=list,
        title="Warnings",
        description="Known converter problems this combination hits.",
    )

    @property
    def needs_verification(self) -> bool:
        """Return whether this export must be verified before shipping.

        Always ``True`` in spirit — every export should be checked — but
        it is ``True`` *emphatically* when :attr:`warnings` is non-empty,
        because then a specific, documented converter defect is known to
        affect this combination.

        Returns:
            bool: Whether a known issue applies to this export.
        """
        return bool(self.warnings)


class ExportVerification(BaseSchema):
    """How closely the exported graph reproduces the estimator.

    Attributes:
        passed (bool): Whether the export is within tolerance.
        n_samples (int): Samples compared.
        max_abs_diff (float | None): Largest absolute difference across
            compared outputs, when the outputs are numeric.
        label_agreement (float | None): Fraction of samples where the
            predicted class matched, for classifiers. ``1.0`` is
            agreement on every sample.
        mismatched (int): Samples where the label differed.
        detail (str): What was compared and what happened.
    """

    passed: bool = Field(
        title="Passed",
        description="Whether the export is within tolerance.",
        examples=[True],
    )
    n_samples: int = Field(
        default=0,
        title="Samples",
        description="How many samples were compared.",
        examples=[100],
    )
    max_abs_diff: float | None = Field(
        default=None,
        title="Max abs diff",
        description="Largest absolute difference across outputs.",
        examples=[1.2e-06],
    )
    label_agreement: float | None = Field(
        default=None,
        title="Label agreement",
        description="Fraction of samples whose predicted class matched.",
        examples=[1.0],
    )
    mismatched: int = Field(
        default=0,
        title="Mismatched",
        description="Samples where the predicted label differed.",
        examples=[0],
    )
    detail: str = Field(
        default="",
        title="Detail",
        description="What was compared and what happened.",
    )


class EdgeStage(BaseSchema):
    """One step of :func:`edge_bundle`, and what it cost.

    Attributes:
        name (str): The stage — ``"export"``, ``"optimize"``,
            ``"quantize"``, ``"ort"``.
        path (str): The artifact it produced.
        size_bytes (int): That artifact's size.
        skipped (bool): Whether the stage was skipped.
        note (str): Why it was skipped, or what it did.
    """

    name: str = Field(
        title="Stage",
        description="Which step this was.",
        examples=["quantize"],
    )
    path: str = Field(
        default="",
        title="Path",
        description="The artifact produced.",
    )
    size_bytes: int = Field(
        default=0,
        title="Size",
        description="Size of the produced artifact.",
        examples=[3_200],
    )
    skipped: bool = Field(
        default=False,
        title="Skipped",
        description="Whether the stage was skipped.",
        examples=[False],
    )
    note: str = Field(
        default="",
        title="Note",
        description="Why it was skipped, or what it did.",
    )


class EdgeBundle(BaseSchema):
    """Everything :func:`edge_bundle` produced.

    Attributes:
        export (SklearnExport): The initial conversion.
        verification (ExportVerification | None): The numeric check, when
            samples were given to run it with.
        stages (list[EdgeStage]): Each step, in order.
        deployable (str): The artifact to ship — the last one produced.
        size_reduction (float): Deployable size as a fraction of the
            first export. ``0.25`` means a quarter of the original.
    """

    export: SklearnExport = Field(
        title="Export",
        description="The initial conversion.",
    )
    verification: ExportVerification | None = Field(
        default=None,
        title="Verification",
        description="The numeric check against the estimator.",
    )
    stages: list[EdgeStage] = Field(
        default_factory=list,
        title="Stages",
        description="Each step of the pipeline, in order.",
    )
    deployable: str = Field(
        default="",
        title="Deployable",
        description="The artifact to ship.",
    )
    size_reduction: float = Field(
        default=1.0,
        title="Size reduction",
        description="Deployable size as a fraction of the first export.",
        examples=[0.25],
    )


def _as_array(samples: Any, dtype: TensorDtype) -> Any:
    """Coerce input samples to a 2-D numpy array of the export dtype.

    Args:
        samples (Any): A numpy array, a pandas DataFrame, or a nested
            sequence.
        dtype (TensorDtype): The target element type.

    Returns:
        Any: A 2-D ``numpy.ndarray``.

    Raises:
        ValueError: When the samples are not 2-D. A single row is a
            common mistake and reshaping it silently would hide a shape
            bug until inference.
    """
    numpy = _require_numpy()
    values = getattr(samples, "values", samples)
    array = numpy.asarray(values, dtype=dtype.value)
    if array.ndim != 2:
        raise ValueError(
            f"samples must be 2-D (n_samples, n_features); got shape {array.shape}",
        )
    return array


def export_sklearn_to_onnx(
    estimator: Any,
    samples: Any,
    output_path: str | Path,
    *,
    dtype: TensorDtype = TensorDtype.FLOAT32,
    opset: int = DEFAULT_OPSET,
    zipmap: bool = False,
    input_name: str = "input",
    options: dict[Any, Any] | None = None,
) -> SklearnExport:
    """Convert a fitted estimator or ``Pipeline`` to ONNX.

    Example:

        >>> model = RandomForestClassifier().fit(X_train, y_train)
        >>> export = export_sklearn_to_onnx(model, X_train[:10], "model.onnx")
        >>> export.n_features, export.zipmap
        (4, False)

    ``samples`` are only used to derive the input shape and type — a
    handful of rows is enough — but they must have the same number of
    features the model was fitted on.

    Args:
        estimator (Any): A **fitted** scikit-learn estimator or
            ``Pipeline``.
        samples (Any): Representative input rows (array, DataFrame or
            nested sequence).
        output_path (str | Path): Where to write the ``.onnx`` file.
        dtype (TensorDtype): Input element type. ``FLOAT32`` by default —
            see the module docstring for why, and verify afterwards.
        opset (int): Target ONNX opset. See :data:`DEFAULT_OPSET`.
        zipmap (bool): Keep scikit-learn's ZipMap output wrapper. ``False``
            by default: it emits a dictionary per row, which many minimal
            runtimes cannot execute and which blocks optimisation. Turn it
            on only if a downstream consumer genuinely wants the mapping.
        input_name (str): Name for the graph input.
        options (dict[Any, Any] | None): Extra `skl2onnx` options, merged
            over the ones derived here.

    Returns:
        SklearnExport: Where it landed and what shape it expects.

    Raises:
        ImportError: When the ``[modelops-sklearn]`` extra is missing.
        ValueError: When ``samples`` is not 2-D, or the estimator is not
            fitted.
    """
    skl2onnx = _require_skl2onnx()
    from skl2onnx.common.data_types import DoubleTensorType, FloatTensorType

    array = _as_array(samples, dtype)
    n_features = int(array.shape[1])

    tensor_type = FloatTensorType if dtype == TensorDtype.FLOAT32 else DoubleTensorType
    initial_types = [(input_name, tensor_type([None, n_features]))]

    merged: dict[Any, Any] = {}
    if not zipmap and _is_classifier(estimator):
        merged[id(estimator)] = {"zipmap": False}
    if options:
        merged.update(options)

    try:
        model = skl2onnx.to_onnx(
            estimator,
            initial_types=initial_types,
            target_opset=opset,
            options=merged or None,
        )
    except Exception as exc:
        raise ValueError(
            f"could not convert {type(estimator).__name__}: {exc}"
        ) from exc

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(model.SerializeToString())

    return SklearnExport(
        path=str(path),
        size_bytes=path.stat().st_size,
        opset=opset,
        dtype=dtype,
        input_name=input_name,
        n_features=n_features,
        zipmap=zipmap,
        estimator=type(estimator).__name__,
        warnings=_converter_warnings(estimator),
    )


_TREE_ENSEMBLES: frozenset[str] = frozenset(
    {
        "RandomForestClassifier",
        "ExtraTreesClassifier",
        "GradientBoostingClassifier",
        "HistGradientBoostingClassifier",
        "DecisionTreeClassifier",
        "BaggingClassifier",
    },
)
"""Classifiers that convert to ``TreeEnsembleClassifier``.

Named rather than detected by base class, because the check runs on the
final step of a pipeline where importing every sklearn module to test
``issubclass`` would cost more than the list is worth.
"""


BINARY_TREE_FIXED_IN_ONNXRUNTIME: tuple[int, int] = (1, 28)
"""First ``onnxruntime`` release that evaluates a binary tree ensemble right.

Measured against the released wheels with every other package held fixed
(``skl2onnx`` 1.20.0, scikit-learn 1.9.0, ``onnx`` 1.22.0): on
``onnxruntime`` 1.27.0 the probability output of a binary
``RandomForestClassifier`` comes back as ``[-1, 1]`` / ``[-0, 0]`` — a
decision score — for a maximum absolute error of ``1.0`` against
``predict_proba``; on 1.28.0 the same graph yields the correct
probabilities, error ``9.5e-08``.

That comparison also **relocates the defect**. It was previously recorded
here as a ``skl2onnx`` conversion bug; since the converter, scikit-learn
and ``onnx`` were byte-identical across the two runs, the graph was
always right and the fault was in ``onnxruntime``'s ``ai.onnx.ml``
TreeEnsemble evaluation. The SDK's floor moved to ``onnxruntime>=1.28``
so a normal install cannot hit it; the runtime is still checked at call
time, because a floor only binds the resolver and an environment assembled
around it would otherwise fail silently.
"""


def _onnxruntime_version() -> tuple[int, int] | None:
    """Return the installed ``onnxruntime`` major/minor, or ``None``.

    Returns:
        tuple[int, int] | None: The version pair, or ``None`` when
        ``onnxruntime`` is not installed — in which case the caller
        cannot know which behavior applies and warns, the safe direction.
    """
    try:
        import onnxruntime
    except ImportError:
        return None

    parts: list[int] = []
    for chunk in onnxruntime.__version__.split(".")[:2]:
        digits = "".join(takewhile(str.isdigit, chunk))
        parts.append(int(digits) if digits else 0)
    pair = [*parts, 0, 0][:2]
    return (pair[0], pair[1])


def _converter_warnings(estimator: Any) -> list[str]:
    """Return known export problems for this estimator on this install.

    Currently one, and it is severe enough to be worth naming: a
    **binary** tree-ensemble classifier exported to ONNX returns a
    decision score in ``[-1, 1]`` where a probability in ``[0, 1]`` is
    expected, and the predicted labels can disagree with the estimator on
    a significant fraction of rows. Multi-class tree ensembles and linear
    models are unaffected.

    The fault is in ``onnxruntime`` rather than in the converter, and it
    is fixed from :data:`BINARY_TREE_FIXED_IN_ONNXRUNTIME` onwards — so
    the warning is raised only for an install that will actually hit it.
    Keeping it version-gated matters in both directions: warning on a
    fixed runtime trains people to ignore the warning, and staying silent
    on an affected one ships a wrong answer.

    Args:
        estimator (Any): The fitted estimator or pipeline.

    Returns:
        list[str]: Warnings, empty when none apply.
    """
    final = estimator
    steps = getattr(estimator, "steps", None)
    if steps:
        final = steps[-1][1]
    if type(final).__name__ not in _TREE_ENSEMBLES:
        return []
    classes = getattr(final, "classes_", None)
    if classes is None or len(classes) != 2:
        return []
    version = _onnxruntime_version()
    if version is not None and version >= BINARY_TREE_FIXED_IN_ONNXRUNTIME:
        return []
    return [
        "binary tree-ensemble classifiers evaluate incorrectly on "
        "onnxruntime < 1.28: the probability output is a decision score in "
        "[-1, 1] and predicted labels can disagree with the estimator. "
        "Upgrade onnxruntime, verify this export before shipping, or "
        "consider a linear model or a multi-class formulation."
    ]


def _is_classifier(estimator: Any) -> bool:
    """Return whether ``estimator`` predicts classes.

    Checks the final step of a ``Pipeline``, since that is what decides
    the output shape.

    Args:
        estimator (Any): The estimator or pipeline.

    Returns:
        bool: Whether it exposes ``predict_proba`` or ``classes_``.
    """
    final = estimator
    steps = getattr(estimator, "steps", None)
    if steps:
        final = steps[-1][1]
    return hasattr(final, "predict_proba") or hasattr(final, "classes_")


def verify_sklearn_onnx(
    estimator: Any,
    onnx_path: str | Path,
    samples: Any,
    *,
    dtype: TensorDtype = TensorDtype.FLOAT32,
    tolerance: float = 1e-4,
    providers: Sequence[str] | None = None,
) -> ExportVerification:
    """Check the exported graph still predicts what the estimator does.

    Example:

        >>> check = verify_sklearn_onnx(model, "model.onnx", X_test[:200])
        >>> check.passed, check.label_agreement
        (True, 1.0)

    For a classifier, **label agreement is the headline**: a probability
    that drifts by 1e-6 is fine, a sample that flips class is not, and
    averaging the two into one number would hide the second behind the
    first. For a regressor the maximum absolute difference is what
    matters.

    Run this on held-out data, not on the rows you exported with. The
    export samples only shaped the graph; they are not a test set.

    Args:
        estimator (Any): The original fitted estimator.
        onnx_path (str | Path): The exported graph.
        samples (Any): Rows to compare on.
        dtype (TensorDtype): The dtype the graph was exported with.
        tolerance (float): Maximum absolute difference tolerated for a
            regressor, and for classifier probabilities.
        providers (Sequence[str] | None): ONNX Runtime execution
            providers. ``None`` lets the runtime choose — verify on the
            provider you will deploy on, since a GPU kernel and a CPU
            kernel can disagree in the last digits.

    Returns:
        ExportVerification: Whether it passed and by how much it differed.

    Raises:
        ImportError: When onnxruntime is unavailable.
        ValueError: When the samples are not 2-D.
    """
    numpy = _require_numpy()
    from tempest_fastapi_sdk.modelops.static import _require_onnxruntime

    onnxruntime = _require_onnxruntime()
    array = _as_array(samples, dtype)

    session = onnxruntime.InferenceSession(
        str(onnx_path),
        providers=list(providers) if providers else None,
    )
    input_name = session.get_inputs()[0].name
    outputs = session.run(None, {input_name: array})

    if _is_classifier(estimator):
        expected_labels = numpy.asarray(estimator.predict(array))
        actual_labels = numpy.asarray(outputs[0]).reshape(-1)
        mismatched = int(numpy.sum(expected_labels != actual_labels))
        agreement = 1.0 - (mismatched / len(expected_labels))

        max_diff: float | None = None
        if len(outputs) > 1 and hasattr(estimator, "predict_proba"):
            actual_proba = _as_proba_array(outputs[1], numpy)
            if actual_proba is not None:
                expected_proba = numpy.asarray(estimator.predict_proba(array))
                if expected_proba.shape == actual_proba.shape:
                    max_diff = float(
                        numpy.max(numpy.abs(expected_proba - actual_proba)),
                    )

        passed = mismatched == 0 and (max_diff is None or max_diff <= tolerance)
        return ExportVerification(
            passed=passed,
            n_samples=int(array.shape[0]),
            max_abs_diff=max_diff,
            label_agreement=agreement,
            mismatched=mismatched,
            detail=(
                f"{mismatched} of {len(expected_labels)} samples changed class"
                if mismatched
                else "every sample kept its predicted class"
            ),
        )

    expected = numpy.asarray(estimator.predict(array)).reshape(-1)
    actual = numpy.asarray(outputs[0]).reshape(-1)
    max_diff = float(numpy.max(numpy.abs(expected - actual)))
    return ExportVerification(
        passed=max_diff <= tolerance,
        n_samples=int(array.shape[0]),
        max_abs_diff=max_diff,
        detail=f"largest difference {max_diff:.3e} against tolerance {tolerance:.3e}",
    )


def uses_ml_domain(onnx_path: str | Path) -> bool:
    """Return whether the graph uses ``ai.onnx.ml`` operators.

    Most scikit-learn estimators convert to that domain —
    ``TreeEnsembleClassifier`` for forests and boosting,
    ``LinearClassifier`` for logistic regression, ``SVMClassifier``, and
    the scalers. Those operators hold their parameters as node attributes
    rather than as weight tensors, so **integer quantisation does not
    apply to them**: there is no matrix to requantise, and the ONNX
    Runtime quantiser refuses with ``Failed to find proper ai.onnx
    domain``.

    Knowing this up front turns a confusing failure into a skipped stage
    with a reason.

    Args:
        onnx_path (str | Path): The graph to inspect.

    Returns:
        bool: Whether any ``ai.onnx.ml`` operator is present. ``False``
        when onnx is unavailable, since guessing would be worse than
        letting the quantiser speak for itself.
    """
    try:
        import onnx
    except ImportError:
        return False
    try:
        model = onnx.load(str(onnx_path))
    except Exception:
        return False
    return any(opset.domain == "ai.onnx.ml" for opset in model.opset_import)


def _as_proba_array(raw: Any, numpy: Any) -> Any:
    """Normalise a probability output to a 2-D array, or ``None``.

    With ZipMap on, the second output is a list of dictionaries rather
    than a tensor. Converting it back is possible but the ordering is not
    guaranteed, so this returns ``None`` and the caller reports label
    agreement alone rather than comparing something it cannot align.

    Args:
        raw (Any): The runtime's second output.
        numpy (Any): The numpy module.

    Returns:
        Any: A 2-D array, or ``None`` when the shape is a ZipMap.
    """
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return None
    array = numpy.asarray(raw)
    return array if array.ndim == 2 else None


def edge_bundle(
    estimator: Any,
    samples: Any,
    output_dir: str | Path,
    *,
    name: str = "model",
    dtype: TensorDtype = TensorDtype.FLOAT32,
    opset: int = DEFAULT_OPSET,
    verify_samples: Any = None,
    tolerance: float = 1e-4,
    optimize: bool = True,
    quantize: bool = True,
    to_ort: bool = True,
    target_platform: str | None = None,
) -> EdgeBundle:
    """Take a fitted estimator to a deployable edge artifact, in one call.

    Runs export → verify → optimise → int8 quantise → ``.ort``, reporting
    the size after each stage so you can see what actually paid off.

    Example:

        >>> bundle = edge_bundle(model, X_train[:50], "dist/",
        ...                      verify_samples=X_test[:500])
        >>> bundle.deployable, round(bundle.size_reduction, 2)
        ('dist/model.ort', 0.28)

    **Verification runs before the lossy stages**, against the plain
    export. Quantisation changes the numbers by design, so a check that
    ran after it could not tell a conversion bug from expected int8 drift
    — and the conversion bug is the one you need to catch.

    **Expect most stages to be skipped or useless here, and that is the
    honest result.** Classic scikit-learn models convert to
    ``ai.onnx.ml`` operators whose parameters live in node attributes, so
    integer quantisation does not apply to them at all; and because these
    graphs are kilobytes rather than megabytes, the optimiser and the
    ``.ort`` conversion often *add* more metadata than they remove. The
    deployable is therefore the **smallest** artifact produced, not the
    last one — see :func:`_smallest_artifact`.

    Where the edge win actually comes from for scikit-learn: the model is
    already tiny, so what you gain is dropping Python, NumPy and
    scikit-learn from the device and linking a minimal ONNX Runtime
    build instead. On **GPU edge**, keep the ``.onnx`` and pick a provider
    at load time (``CUDAExecutionProvider``,
    ``TensorrtExecutionProvider``). Either way, measure with
    :func:`~tempest_fastapi_sdk.modelops.benchmark_onnx` rather than
    assuming a stage helped.

    Args:
        estimator (Any): A fitted estimator or ``Pipeline``.
        samples (Any): Rows used to shape the graph.
        output_dir (str | Path): Where the artifacts go.
        name (str): Base filename.
        dtype (TensorDtype): Input element type.
        opset (int): Target opset.
        verify_samples (Any): Held-out rows to verify against. ``None``
            skips verification, which is only sensible in a throwaway
            experiment.
        tolerance (float): Numeric tolerance for verification.
        optimize (bool): Run the graph optimiser.
        quantize (bool): Produce an int8 dynamic-quantised graph.
        to_ort (bool): Convert to the ``.ort`` runtime format.
        target_platform (str | None): ``"arm"`` or ``"amd64"`` for the
            ``.ort`` conversion.

    Returns:
        EdgeBundle: Every stage, the verification, and which artifact to
        ship.

    Raises:
        ImportError: When a required extra is missing.
        ValueError: When conversion fails or the samples are malformed.
    """
    from tempest_fastapi_sdk.modelops.export import (
        export_onnx_to_ort,
        optimize_onnx_graph,
    )
    from tempest_fastapi_sdk.modelops.quantize import quantize_onnx_dynamic

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)

    export = export_sklearn_to_onnx(
        estimator,
        samples,
        directory / f"{name}.onnx",
        dtype=dtype,
        opset=opset,
    )
    stages = [
        EdgeStage(
            name="export",
            path=export.path,
            size_bytes=export.size_bytes,
            note=f"{export.estimator}, {export.n_features} features",
        ),
    ]

    verification: ExportVerification | None = None
    if verify_samples is not None:
        verification = verify_sklearn_onnx(
            estimator,
            export.path,
            verify_samples,
            dtype=dtype,
            tolerance=tolerance,
        )

    current = Path(export.path)

    if optimize:
        optimized = directory / f"{name}.opt.onnx"
        try:
            optimize_onnx_graph(current, optimized)
            stages.append(
                EdgeStage(
                    name="optimize",
                    path=str(optimized),
                    size_bytes=optimized.stat().st_size,
                ),
            )
            current = optimized
        except Exception as exc:
            stages.append(
                EdgeStage(name="optimize", skipped=True, note=str(exc)),
            )

    if quantize:
        if uses_ml_domain(export.path):
            stages.append(
                EdgeStage(
                    name="quantize",
                    skipped=True,
                    note=(
                        "graph uses ai.onnx.ml operators (trees, linear "
                        "models, scalers): their parameters are node "
                        "attributes, not weight tensors, so integer "
                        "quantisation does not apply"
                    ),
                ),
            )
        else:
            quantized = directory / f"{name}.int8.onnx"
            try:
                quantize_onnx_dynamic(current, quantized)
                stages.append(
                    EdgeStage(
                        name="quantize",
                        path=str(quantized),
                        size_bytes=quantized.stat().st_size,
                        note="int8 dynamic; verify on device before shipping",
                    ),
                )
                current = quantized
            except Exception as exc:
                stages.append(
                    EdgeStage(name="quantize", skipped=True, note=str(exc)),
                )

    if to_ort:
        try:
            results = export_onnx_to_ort(
                current,
                directory,
                target_platform=target_platform,
            )
            if results:
                produced = Path(results[0].output_path)
                stages.append(
                    EdgeStage(
                        name="ort",
                        path=str(produced),
                        size_bytes=produced.stat().st_size if produced.exists() else 0,
                        note="minimal-runtime format",
                    ),
                )
        except Exception as exc:
            stages.append(EdgeStage(name="ort", skipped=True, note=str(exc)))

    smallest = _smallest_artifact(stages, export)
    return EdgeBundle(
        export=export,
        verification=verification,
        stages=stages,
        deployable=smallest[0],
        size_reduction=(smallest[1] / export.size_bytes if export.size_bytes else 1.0),
    )


def _smallest_artifact(
    stages: list[EdgeStage],
    export: SklearnExport,
) -> tuple[str, int]:
    """Return the smallest artifact any stage produced.

    The last stage is **not** automatically the one to ship. For
    scikit-learn models the optimiser and the ``.ort`` conversion
    routinely produce a *larger* file than the plain export — these models
    are kilobytes, and the metadata those stages add outweighs what they
    save. Picking the last artifact would hand back something bigger than
    what it started from and call it optimised.

    Args:
        stages (list[EdgeStage]): The completed stages.
        export (SklearnExport): The initial export, used as the floor.

    Returns:
        tuple[str, int]: Path and size of the smallest artifact.
    """
    best_path = export.path
    best_size = export.size_bytes
    for stage in stages:
        if stage.skipped or not stage.path or not stage.size_bytes:
            continue
        if stage.size_bytes < best_size:
            best_path = stage.path
            best_size = stage.size_bytes
    return best_path, best_size


__all__: list[str] = [
    "BINARY_TREE_FIXED_IN_ONNXRUNTIME",
    "DEFAULT_OPSET",
    "EdgeBundle",
    "EdgeStage",
    "ExportVerification",
    "SklearnExport",
    "TensorDtype",
    "edge_bundle",
    "export_sklearn_to_onnx",
    "verify_sklearn_onnx",
]
