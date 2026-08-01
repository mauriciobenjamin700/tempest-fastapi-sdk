"""From a fitted estimator to a directory two runtimes can consume.

:func:`~tempest_fastapi_sdk.modelops.edge_bundle` answers "what does each
optimisation stage cost on my model". This module answers the question
after it: **what do I actually ship, and how does the thing running it know
what it got.**

A shipped model is never one file. A device needs the graph, the feature
order that produced it, the classes it can answer, and a drift baseline to
know whether the world still looks like training. A browser needs the same
plus the byte size to decide whether to download now. Without a manifest,
every one of those lives in a wiki page that goes stale, and the failure
mode is silent: a model served with two columns swapped answers confidently
and wrongly.

    from tempest_fastapi_sdk.modelops import edge_pipeline, load_edge_package

    package = edge_pipeline(model, X_train, "dist/risk", labels=y_train)

    loaded = load_edge_package("dist/risk")
    loaded.predictor.predict([[5.1, 3.5, 1.4, 0.2]])

The same directory is served as static assets to
`tempest-react-sdk/tabular`, which reads the same `manifest.json`. That is
the point of pinning a `schema_version`: it is a cross-language contract,
not an internal convenience.

**The stages this pipeline runs are the ones that were measured to pay
off.** Graph optimisation and `.ort` conversion are not among them for
scikit-learn graphs — see :func:`edge_pipeline` for the numbers. Gzip is,
by a factor of ten.
"""

from __future__ import annotations

import gzip
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ConfigDict, Field

from tempest_fastapi_sdk.modelops.monitoring import (
    DEFAULT_BINS,
    FeatureBaseline,
    PredictionMonitor,
    baseline_from_samples,
)
from tempest_fastapi_sdk.modelops.serving import (
    DEFAULT_INTRA_OP_THREADS,
    OnnxPredictor,
)
from tempest_fastapi_sdk.modelops.sklearn import (
    DEFAULT_OPSET,
    ExportVerification,
    TensorDtype,
    export_sklearn_to_onnx,
    verify_sklearn_onnx,
)
from tempest_fastapi_sdk.schemas.base import BaseSchema

if TYPE_CHECKING:
    from collections.abc import Sequence

MANIFEST_SCHEMA_VERSION: int = 1
"""Version of the manifest contract.

Read by `tempest-react-sdk/tabular` as well as by this SDK, so a change
here breaks a consumer in another language. Bump it only for a breaking
shape change, and keep readers tolerant of unknown fields.
"""

MANIFEST_FILENAME: str = "manifest.json"
"""Fixed name, so a consumer needs only the directory URL."""

BASELINE_FILENAME: str = "baseline.json"
"""Fixed name, referenced from the manifest."""

_HASH_CHUNK_BYTES: int = 1 << 20
"""Read size when hashing, so a large forest does not land in memory at once."""

_VERSION_HASH_CHARS: int = 12
"""Characters of the content hash used as a default version.

Long enough to be unique in practice, short enough to read in a log. A
content-derived version means republishing identical bytes does not look
like a new model — which is what a device's "do I need to download this"
check needs.
"""


class ModelFile(BaseSchema):
    """The graph and how to verify you got the right bytes.

    Attributes:
        file (str): Filename inside the package directory.
        sha256 (str): Digest of the file, so a device can detect a
            truncated download instead of loading half a model.
        bytes (int): Size on disk.
        gzip_file (str | None): Pre-compressed copy, when one was written.
        gzip_bytes (int | None): Its size — what actually crosses the
            network when the server sends it with
            ``Content-Encoding: gzip``.
        opset (int): ONNX opset the graph targets.
        dtype (str): Input element type.
    """

    file: str = Field(
        title="File",
        description="Filename inside the package directory.",
        examples=["model.onnx"],
    )
    sha256: str = Field(
        title="SHA-256",
        description="Digest of the model file.",
    )
    bytes: int = Field(
        default=0,
        title="Bytes",
        description="Size on disk.",
        examples=[1_955_000],
    )
    gzip_file: str | None = Field(
        default=None,
        title="Gzip file",
        description="Pre-compressed copy of the model.",
        examples=["model.onnx.gz"],
    )
    gzip_bytes: int | None = Field(
        default=None,
        title="Gzip bytes",
        description="Compressed size.",
        examples=[225_000],
    )
    opset: int = Field(
        default=DEFAULT_OPSET,
        title="Opset",
        description="ONNX opset the graph targets.",
        examples=[15],
    )
    dtype: str = Field(
        default="float32",
        title="Dtype",
        description="Input element type.",
        examples=["float32"],
    )


class ModelInput(BaseSchema):
    """What the graph expects per row.

    Attributes:
        name (str): Graph input name.
        features (int): Values per row.
        feature_names (list[str]): Column order used at training time.
            The most valuable field in the manifest: a model fed the right
            columns in the wrong order answers confidently and wrongly,
            and nothing else in the package catches it.
    """

    name: str = Field(
        default="input",
        title="Name",
        description="Graph input name.",
        examples=["input"],
    )
    features: int = Field(
        default=0,
        title="Features",
        description="Values per row.",
        examples=[4],
    )
    feature_names: list[str] = Field(
        default_factory=list,
        title="Feature names",
        description="Column order used at training time.",
        examples=[["age", "income", "tenure", "score"]],
    )


class ModelOutput(BaseSchema):
    """What the graph answers.

    Attributes:
        is_classifier (bool): Whether class scores are produced.
        label_output (str): Output holding classes or values.
        probability_output (str | None): Output holding class scores.
        classes (list[str]): Class labels in score-column order, so a
            consumer can map ``probabilities[2]`` to a name.
    """

    is_classifier: bool = Field(
        default=False,
        title="Is classifier",
        description="Whether class scores are produced.",
        examples=[True],
    )
    label_output: str = Field(
        default="label",
        title="Label output",
        description="Output holding classes or values.",
    )
    probability_output: str | None = Field(
        default=None,
        title="Probability output",
        description="Output holding class scores.",
    )
    classes: list[str] = Field(
        default_factory=list,
        title="Classes",
        description="Class labels in score-column order.",
        examples=[["denied", "review", "approved"]],
    )


class ArtifactSource(BaseSchema):
    """Where the packaged model came from, when it came from somewhere.

    Present when the package was built from an existing artifact — today a
    `.pkl` handed over by a training pipeline. It is what lets someone
    holding a model that is running on a device answer "which file made
    this", months later.

    Attributes:
        file (str): Name of the source artifact.
        kind (str): What it was, e.g. ``"pickle"``.
        sha256 (str): Digest of that file.
        bytes (int): Its size.
        sklearn_version (str): The scikit-learn version that read it and
            performed the conversion. A pickle records no version of its
            own (measured on scikit-learn 1.9), so this is the only
            version fact the chain can offer.
        warnings (list[str]): Warnings raised while reading the source.
    """

    file: str = Field(
        title="File",
        description="Name of the source artifact.",
        examples=["risk.pkl"],
    )
    kind: str = Field(
        default="pickle",
        title="Kind",
        description="What the source artifact was.",
        examples=["pickle"],
    )
    sha256: str = Field(
        default="",
        title="SHA-256",
        description="Digest of the source artifact.",
    )
    bytes: int = Field(
        default=0,
        title="Bytes",
        description="Size of the source artifact.",
        examples=[48_120],
    )
    sklearn_version: str = Field(
        default="",
        title="scikit-learn version",
        description="Version that read the source and converted it.",
        examples=["1.9.0"],
    )
    warnings: list[str] = Field(
        default_factory=list,
        title="Warnings",
        description="Warnings raised while reading the source.",
    )


class EdgeManifest(BaseSchema):
    """Everything a runtime needs to serve the package correctly.

    Written as `manifest.json` and read by both this SDK's
    :func:`load_edge_package` and `tempest-react-sdk/tabular` in the
    browser.

    Attributes:
        schema_version (int): Contract version.
        name (str): Logical model name.
        version (str): This build. Defaults to the model's content hash,
            so identical bytes never look like a new version.
        created_at (str): ISO-8601 UTC timestamp.
        sdk_version (str): SDK that produced the package.
        estimator (str): Class name of the exported estimator.
        model (ModelFile): The graph and its digest.
        input (ModelInput): What it expects.
        output (ModelOutput): What it answers.
        verified (bool | None): Whether the export was checked against the
            estimator's own predictions. ``None`` means it was not checked
            — which is worth seeing rather than assuming.
        verification (ExportVerification | None): The numbers behind it.
        source (ArtifactSource | None): Where the packaged model came
            from, when it was built from an existing artifact.
        baseline_file (str | None): Drift baseline filename.
        baseline_samples (int): Rows the baseline summarises.
    """

    schema_version: int = Field(
        default=MANIFEST_SCHEMA_VERSION,
        title="Schema version",
        description="Manifest contract version.",
        examples=[1],
    )
    name: str = Field(
        default="model",
        title="Name",
        description="Logical model name.",
        examples=["risk"],
    )
    version: str = Field(
        default="",
        title="Version",
        description="This build; defaults to the content hash.",
        examples=["a1b2c3d4e5f6"],
    )
    created_at: str = Field(
        default="",
        title="Created at",
        description="ISO-8601 UTC timestamp.",
    )
    sdk_version: str = Field(
        default="",
        title="SDK version",
        description="SDK that produced the package.",
    )
    estimator: str = Field(
        default="",
        title="Estimator",
        description="Class name of the exported estimator.",
        examples=["RandomForestClassifier"],
    )
    model: ModelFile = Field(
        title="Model",
        description="The graph and its digest.",
    )
    input: ModelInput = Field(
        default_factory=ModelInput,
        title="Input",
        description="What the graph expects.",
    )
    output: ModelOutput = Field(
        default_factory=ModelOutput,
        title="Output",
        description="What the graph answers.",
    )
    verified: bool | None = Field(
        default=None,
        title="Verified",
        description="Whether the export was checked against the estimator.",
    )
    verification: ExportVerification | None = Field(
        default=None,
        title="Verification",
        description="The numbers behind the check.",
    )
    source: ArtifactSource | None = Field(
        default=None,
        title="Source",
        description="Where the packaged model came from, when known.",
    )
    baseline_file: str | None = Field(
        default=None,
        title="Baseline file",
        description="Drift baseline filename.",
        examples=["baseline.json"],
    )
    baseline_samples: int = Field(
        default=0,
        title="Baseline samples",
        description="Rows the baseline summarises.",
        examples=[4000],
    )


class EdgePackage(BaseSchema):
    """What :func:`edge_pipeline` wrote.

    Attributes:
        directory (str): The package directory — this is what you ship,
            whole.
        manifest (EdgeManifest): Its manifest, already parsed.
        model_path (str): The graph.
        gzip_path (str | None): The pre-compressed copy.
        baseline_path (str | None): The drift baseline.
        manifest_path (str): The manifest file.
    """

    directory: str = Field(
        title="Directory",
        description="The package directory.",
        examples=["dist/risk"],
    )
    manifest: EdgeManifest = Field(
        title="Manifest",
        description="The package manifest.",
    )
    model_path: str = Field(
        title="Model path",
        description="The exported graph.",
    )
    gzip_path: str | None = Field(
        default=None,
        title="Gzip path",
        description="Pre-compressed copy of the graph.",
    )
    baseline_path: str | None = Field(
        default=None,
        title="Baseline path",
        description="The drift baseline.",
    )
    manifest_path: str = Field(
        title="Manifest path",
        description="The manifest file.",
    )


class LoadedEdgePackage(BaseSchema):
    """A package loaded and wired up, ready to answer.

    Attributes:
        manifest (EdgeManifest): What was loaded.
        predictor (OnnxPredictor): The running model.
        monitor (PredictionMonitor | None): Wired to the package's
            baseline, when it has one.
        baseline (FeatureBaseline | None): The baseline itself.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    manifest: EdgeManifest = Field(
        title="Manifest",
        description="What was loaded.",
    )
    predictor: OnnxPredictor = Field(
        title="Predictor",
        description="The running model.",
    )
    monitor: PredictionMonitor | None = Field(
        default=None,
        title="Monitor",
        description="Wired to the package's baseline, when it has one.",
    )
    baseline: FeatureBaseline | None = Field(
        default=None,
        title="Baseline",
        description="The drift baseline.",
    )


def _digest(path: Path) -> str:
    """Return the SHA-256 of a file, read in chunks.

    Args:
        path (Path): The file to hash.

    Returns:
        str: Hex digest.
    """
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            hasher.update(chunk)
    return hasher.hexdigest()


def _class_labels(estimator: Any) -> list[str]:
    """Read an estimator's classes in score-column order.

    Args:
        estimator (Any): The fitted estimator, possibly a Pipeline.

    Returns:
        list[str]: Class labels, or an empty list for a regressor.
    """
    classes = getattr(estimator, "classes_", None)
    if classes is None:
        steps = getattr(estimator, "steps", None)
        if steps:
            classes = getattr(steps[-1][1], "classes_", None)
    if classes is None:
        return []
    return [str(value) for value in classes]


def _resolve_feature_names(
    samples: Any,
    feature_names: Sequence[str] | None,
    count: int,
) -> list[str]:
    """Decide the column order to record.

    Args:
        samples (Any): The training rows, possibly a DataFrame.
        feature_names (Sequence[str] | None): Explicit names.
        count (int): Features the graph expects.

    Returns:
        list[str]: Names, falling back to positional indices.
    """
    if feature_names is not None:
        return [str(name) for name in feature_names]
    columns = getattr(samples, "columns", None)
    if columns is not None:
        return [str(column) for column in columns]
    return [str(index) for index in range(count)]


def edge_pipeline(
    estimator: Any,
    samples: Any,
    output_dir: str | Path,
    *,
    name: str = "model",
    version: str | None = None,
    labels: Any = None,
    feature_names: Sequence[str] | None = None,
    verify_samples: Any = None,
    tolerance: float = 1e-4,
    baseline: bool = True,
    bins: int = DEFAULT_BINS,
    compress: bool = True,
    dtype: TensorDtype = TensorDtype.FLOAT32,
    opset: int = DEFAULT_OPSET,
) -> EdgePackage:
    """Turn a fitted estimator into a directory you ship as-is.

    Runs export → verify → drift baseline → manifest, writing a
    self-describing package that this SDK's :func:`load_edge_package` and
    `tempest-react-sdk/tabular` both consume.

    Example:

        >>> package = edge_pipeline(model, X_train, "dist/risk", labels=y_train)
        >>> package.manifest.version, package.manifest.verified
        ('a1b2c3d4e5f6', True)

    **What this deliberately does not run, and why.** Measured on random
    forests of 10 to 300 trees exported from scikit-learn:

    * *Graph optimisation* changed the file by 0.1 KB on every size. The
      `ai.onnx.ml` operators are single nodes; there is nothing to fuse.
    * *`.ort` conversion* more than doubled the file every time
      (381 KB to 878 KB; 12.1 MB to 27.0 MB). It is a runtime-loading
      format, not a compression one.
    * *int8 quantisation* does not apply at all — tree and linear
      parameters are node attributes, not tensors.
    * *gzip* took the same files to 10-13% of their size. That is the
      lever, and it costs nothing but a Content-Encoding header.

    Use :func:`~tempest_fastapi_sdk.modelops.edge_bundle` when you want to
    see those stages measured on your own model rather than trusting the
    summary.

    **What actually controls size** is the estimator, before any of this:
    at 50 trees on 20 features, `max_depth=6` produced 257 KB at 0.881
    test accuracy while unlimited depth produced 1444 KB at 0.922 —
    5.6x the bytes for 4 points. Single-row latency was identical
    (0.0075 ms vs 0.0079 ms), because on these graphs latency is not what
    you are trading.

    Args:
        estimator (Any): The fitted estimator or Pipeline.
        samples (Any): Training rows — used both to trace the export and
            to build the drift baseline. Pass the **training** set: a
            baseline built from production traffic describes the drifted
            population as normal.
        output_dir (str | Path): Directory to create and fill.
        name (str): Logical model name; also the model filename stem.
        version (str | None): Build version. Defaults to the model's
            content hash, so republishing identical bytes does not look
            like a new version to a device.
        labels (Any): Training labels, recorded as the baseline output
            distribution. Without them, output drift cannot be measured.
        feature_names (Sequence[str] | None): Column order. Taken from a
            DataFrame when not given, positional indices otherwise.
        verify_samples (Any): Rows to check the export against the
            estimator's own predictions. Defaults to ``samples``.
        tolerance (float): Numeric tolerance for that check.
        baseline (bool): Write the drift baseline.
        bins (int): Quantile bins per feature in the baseline.
        compress (bool): Also write a gzipped copy of the graph.
        dtype (TensorDtype): Input element type.
        opset (int): Target ONNX opset.

    Returns:
        EdgePackage: Paths and the manifest.

    Raises:
        ValueError: When the export fails verification. Shipping a graph
            that does not reproduce the estimator is the one outcome this
            pipeline refuses to let pass quietly — pass
            ``verify_samples=False`` to skip the check deliberately.
    """
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    model_path = directory / f"{name}.onnx"

    export = export_sklearn_to_onnx(
        estimator,
        samples,
        model_path,
        dtype=dtype,
        opset=opset,
    )

    verification: ExportVerification | None = None
    if verify_samples is not False:
        checked = samples if verify_samples is None else verify_samples
        verification = verify_sklearn_onnx(
            estimator,
            model_path,
            checked,
            tolerance=tolerance,
        )
        if not verification.passed:
            raise ValueError(
                f"the exported graph does not reproduce {type(estimator).__name__}: "
                f"{verification.mismatched} of {verification.n_samples} samples "
                f"disagree. Shipping it would serve wrong answers silently. "
                f"See SklearnExport.warnings for known converter defects.",
            )

    baseline_path: Path | None = None
    baseline_samples = 0
    if baseline:
        built = baseline_from_samples(
            samples,
            labels=labels,
            names=feature_names,
            bins=bins,
        )
        baseline_path = directory / BASELINE_FILENAME
        baseline_path.write_text(built.model_dump_json(indent=1), encoding="utf-8")
        baseline_samples = built.n_samples

    gzip_path: Path | None = None
    if compress:
        gzip_path = model_path.with_suffix(".onnx.gz")
        gzip_path.write_bytes(gzip.compress(model_path.read_bytes(), 6))

    digest = _digest(model_path)
    probe = OnnxPredictor(model_path, warmup=False)
    info = probe.info

    manifest = EdgeManifest(
        name=name,
        version=version or digest[:_VERSION_HASH_CHARS],
        created_at=datetime.now(UTC).isoformat(),
        sdk_version=_sdk_version(),
        estimator=export.estimator,
        model=ModelFile(
            file=model_path.name,
            sha256=digest,
            bytes=export.size_bytes,
            gzip_file=gzip_path.name if gzip_path else None,
            gzip_bytes=gzip_path.stat().st_size if gzip_path else None,
            opset=export.opset,
            dtype=str(export.dtype),
        ),
        input=ModelInput(
            name=export.input_name,
            features=export.n_features,
            feature_names=_resolve_feature_names(
                samples,
                feature_names,
                export.n_features,
            ),
        ),
        output=ModelOutput(
            is_classifier=info.is_classifier,
            label_output=info.label_output or "label",
            probability_output=info.proba_output,
            classes=_class_labels(estimator),
        ),
        verified=None if verification is None else verification.passed,
        verification=verification,
        baseline_file=BASELINE_FILENAME if baseline_path else None,
        baseline_samples=baseline_samples,
    )

    manifest_path = directory / MANIFEST_FILENAME
    manifest_path.write_text(manifest.model_dump_json(indent=1), encoding="utf-8")

    return EdgePackage(
        directory=str(directory),
        manifest=manifest,
        model_path=str(model_path),
        gzip_path=str(gzip_path) if gzip_path else None,
        baseline_path=str(baseline_path) if baseline_path else None,
        manifest_path=str(manifest_path),
    )


def _sdk_version() -> str:
    """Return this SDK's version, without importing the world.

    Returns:
        str: The version string, or an empty string when unavailable.
    """
    try:
        from tempest_fastapi_sdk import __version__

        return str(__version__)
    except Exception:
        return ""


def read_manifest(directory: str | Path) -> EdgeManifest:
    """Read a package's manifest without loading the model.

    Cheap enough for a device to run on a schedule: it answers "is the
    published version the one I have" without touching the graph.

    Args:
        directory (str | Path): The package directory.

    Returns:
        EdgeManifest: The parsed manifest.

    Raises:
        FileNotFoundError: When the directory has no manifest.
    """
    path = Path(directory) / MANIFEST_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"no {MANIFEST_FILENAME} in {directory}")
    return EdgeManifest.model_validate_json(path.read_text(encoding="utf-8"))


def load_edge_package(
    directory: str | Path,
    *,
    providers: Sequence[str] | None = None,
    intra_op_threads: int = DEFAULT_INTRA_OP_THREADS,
    monitor: bool = True,
    window_rows: int | None = None,
    verify_digest: bool = True,
) -> LoadedEdgePackage:
    """Load a package into a running predictor, monitor included.

    Example:

        >>> loaded = load_edge_package("dist/risk")
        >>> result = loaded.predictor.predict(rows)
        >>> loaded.monitor.observe(rows, result)

    Args:
        directory (str | Path): The package directory.
        providers (Sequence[str] | None): Execution providers. Include a
            CPU fallback on a GPU device.
        intra_op_threads (int): Threads inside an operator. Worth raising
            for a large ensemble or a batched workload — measured on a
            300-tree forest, 1000 rows went from 16.6 ms at one thread to
            2.3 ms at eight. A small graph shows no difference at all.
        monitor (bool): Wire a :class:`PredictionMonitor` to the
            package's baseline.
        window_rows (int | None): Drift window size; the monitor's
            default when omitted.
        verify_digest (bool): Check the model file against the manifest's
            SHA-256 before loading it. A truncated download otherwise
            fails as a confusing parse error, or worse, does not fail.

    Returns:
        LoadedEdgePackage: Manifest, predictor, monitor and baseline.

    Raises:
        FileNotFoundError: When the manifest or model file is missing.
        ValueError: When the model file does not match its digest.
    """
    root = Path(directory)
    manifest = read_manifest(root)
    model_path = root / manifest.model.file
    if not model_path.exists():
        raise FileNotFoundError(f"model file missing from the package: {model_path}")

    if verify_digest and manifest.model.sha256:
        actual = _digest(model_path)
        if actual != manifest.model.sha256:
            raise ValueError(
                f"{model_path.name} does not match the manifest digest "
                f"(expected {manifest.model.sha256[:12]}..., got {actual[:12]}...). "
                "The download is truncated or the file was replaced.",
            )

    predictor = OnnxPredictor(
        model_path,
        providers=providers,
        intra_op_threads=intra_op_threads,
    )

    loaded_baseline: FeatureBaseline | None = None
    if manifest.baseline_file:
        baseline_path = root / manifest.baseline_file
        if baseline_path.exists():
            loaded_baseline = FeatureBaseline.model_validate_json(
                baseline_path.read_text(encoding="utf-8"),
            )

    prediction_monitor: PredictionMonitor | None = None
    if monitor:
        kwargs: dict[str, Any] = {
            "baseline": loaded_baseline,
            "model_version": manifest.version,
        }
        if window_rows is not None:
            kwargs["window_rows"] = window_rows
        prediction_monitor = PredictionMonitor(**kwargs)

    return LoadedEdgePackage(
        manifest=manifest,
        predictor=predictor,
        monitor=prediction_monitor,
        baseline=loaded_baseline,
    )


__all__: list[str] = [
    "BASELINE_FILENAME",
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "ArtifactSource",
    "EdgeManifest",
    "EdgePackage",
    "LoadedEdgePackage",
    "ModelFile",
    "ModelInput",
    "ModelOutput",
    "edge_pipeline",
    "load_edge_package",
    "read_manifest",
]
