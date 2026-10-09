"""Taking a `.pkl` from the training pipeline to a browser-ready package.

The training side hands over a pickle, because that is what
``joblib.dump`` produces and what every notebook already writes. The serving
side cannot use it: a pickle is a Python program, so it needs a Python
runtime — which rules out the browser entirely, and rules out treating it as
data anywhere else.

This module is the bridge, and it belongs to the **build**, not to the
request path: read the pickle once, in your own pipeline, and emit the ONNX
package that devices and browsers actually load.

    from tempest_fastapi_sdk.modelops import edge_pipeline_from_pickle

    package = edge_pipeline_from_pickle(
        "artifacts/risk.pkl", X_train, "dist/risk", labels=y_train,
    )

**Loading a pickle executes arbitrary code.** ``joblib.load`` and
``pickle.load`` are not parsers — they run instructions from the file, so a
pickle from an untrusted source is remote code execution, not a risk to
weigh. Everything here therefore takes a **local path you produced** and
never a URL, and the resulting package records where it came from so an
artifact on a device can be traced back to the pickle that made it. That
asymmetry is the whole point of converting: ONNX is data, a pickle is a
program.

**Pin the digest you released.** ``expected_sha256`` makes the load refuse
a file whose SHA-256 differs from the value your release recorded, and the
comparison runs **before** ``joblib.load``, so a substituted pickle never
gets to execute:

    load_sklearn_artifact(
        "artifacts/risk.pkl",
        expected_sha256="sha256:9f2c...",
    )

**Pickles also carry no version contract you can rely on.** Measured on
scikit-learn 1.9: a model pickled by one version and loaded by another
produces *no* warning and stores no version field — the mismatch, when it
matters, is silent. Reading it once at build time and shipping ONNX turns
that class of problem into a conversion that either verifies or refuses.
"""

from __future__ import annotations

import hashlib
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

from pydantic import ConfigDict, Field

from tempest_fastapi_sdk.modelops.edge import (
    ArtifactSource,
    EdgePackage,
    edge_pipeline,
)
from tempest_fastapi_sdk.schemas.base import BaseSchema

if TYPE_CHECKING:
    from collections.abc import Sequence

_URL_PREFIXES: tuple[str, ...] = ("http://", "https://", "ftp://", "s3://", "gs://")
"""Schemes refused outright.

Not a security control — a caller can always download first — but it stops
the shape of code that reads "load the model from this URL", which is the
one call that turns a model registry into a remote-code-execution surface.
"""

_HASH_CHUNK_BYTES: int = 1 << 20
"""Read size when hashing, so a large artifact is not held in memory twice."""

_SHA256_PREFIX: str = "sha256:"
"""Optional algorithm prefix accepted on ``expected_sha256``."""

_SHA256_HEX_LENGTH: int = 64
"""Length of a SHA-256 hex digest."""

_HEX_DIGITS: frozenset[str] = frozenset("0123456789abcdef")
"""Characters a lower-cased hex digest may contain."""


class ArtifactDigestMismatchError(ValueError):
    """The pickle on disk is not the one the caller pinned.

    Raised by :func:`load_sklearn_artifact` **before** the file is
    unpickled, so a substituted artifact never executes. Subclasses
    ``ValueError`` to match the digest check of ``load_edge_package``.

    Attributes:
        path (str): The file that was checked.
        expected (str): The normalised digest the caller pinned.
        actual (str): The digest of the file on disk.
    """

    def __init__(self, path: str, expected: str, actual: str) -> None:
        """Build the error with both digests in the message.

        Args:
            path (str): The file that was checked.
            expected (str): The normalised digest the caller pinned.
            actual (str): The digest of the file on disk.
        """
        self.path: str = path
        self.expected: str = expected
        self.actual: str = actual
        super().__init__(
            f"refusing to load {path}: its SHA-256 is {actual}, expected "
            f"{expected}. The file was replaced, truncated or is not the "
            "released artifact; it was not unpickled.",
        )


class LoadedArtifact(BaseSchema):
    """A fitted estimator read from a pickle, plus where it came from.

    Attributes:
        estimator (Any): The unpickled object, ready to convert.
        source_path (str): The file it was read from.
        sha256 (str): Digest of that file, so the produced package can be
            traced back to the exact artifact.
        bytes (int): Its size on disk.
        sklearn_version (str): The scikit-learn version **that read it** —
            which is the one that will convert it. The version that wrote
            it is not recorded in the pickle (see the module docstring).
        estimator_type (str): Class name of the loaded estimator.
        feature_names (list[str]): Column order recovered from
            ``feature_names_in_``, when the model was fitted on a
            DataFrame. Empty otherwise — and worth supplying by hand then,
            since nothing else records it.
        load_warnings (list[str]): Warnings raised while unpickling. Empty
            is the normal case; anything here is worth reading before
            shipping the conversion.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    estimator: Any = Field(
        title="Estimator",
        description="The unpickled object.",
    )
    source_path: str = Field(
        title="Source path",
        description="The file it was read from.",
        examples=["artifacts/risk.pkl"],
    )
    sha256: str = Field(
        title="SHA-256",
        description="Digest of the source file.",
    )
    bytes: int = Field(
        default=0,
        title="Bytes",
        description="Size of the source file.",
        examples=[48_120],
    )
    sklearn_version: str = Field(
        default="",
        title="scikit-learn version",
        description="The version that read the pickle.",
        examples=["1.9.0"],
    )
    estimator_type: str = Field(
        default="",
        title="Estimator type",
        description="Class name of the loaded estimator.",
        examples=["RandomForestClassifier"],
    )
    feature_names: list[str] = Field(
        default_factory=list,
        title="Feature names",
        description="Column order recovered from the fitted estimator.",
    )
    load_warnings: list[str] = Field(
        default_factory=list,
        title="Load warnings",
        description="Warnings raised while unpickling.",
    )


def _digest(handle: BinaryIO) -> str:
    """Return the SHA-256 of an open binary file, read in chunks.

    Reads from the current position to the end; the caller rewinds before
    reusing the handle.

    Args:
        handle (BinaryIO): The file to hash.

    Returns:
        str: Hex digest.
    """
    hasher = hashlib.sha256()
    while chunk := handle.read(_HASH_CHUNK_BYTES):
        hasher.update(chunk)
    return hasher.hexdigest()


def _normalise_sha256(value: str) -> str:
    """Bring a caller-supplied SHA-256 to lower-case bare hex.

    Accepts upper or lower case and an optional ``sha256:`` prefix (in any
    case), with surrounding whitespace ignored.

    Args:
        value (str): The digest as the caller wrote it.

    Returns:
        str: The 64-character lower-case hex digest.

    Raises:
        ValueError: When the value is not a SHA-256 hex digest.
    """
    normalised = value.strip().lower()
    if normalised.startswith(_SHA256_PREFIX):
        normalised = normalised[len(_SHA256_PREFIX) :]
    if len(normalised) != _SHA256_HEX_LENGTH or not set(normalised) <= _HEX_DIGITS:
        raise ValueError(
            f"expected_sha256 must be a {_SHA256_HEX_LENGTH}-character hex "
            f"digest, optionally prefixed with {_SHA256_PREFIX!r}; got {value!r}",
        )
    return normalised


def _unwrap(loaded: Any, key: str | None, path: Path) -> Any:
    """Pull the estimator out of whatever the pickle held.

    A training pipeline often dumps a dict — the model next to its metrics,
    its encoder, the training date. Guessing which entry is the model is
    fine when exactly one of them can predict; guessing between two is not,
    so the error lists what is in the file instead.

    Args:
        loaded (Any): The unpickled object.
        key (str | None): Explicit key into a mapping.
        path (Path): Source path, for error messages.

    Returns:
        Any: The estimator.

    Raises:
        KeyError: When ``key`` is not in the mapping.
        TypeError: When no estimator can be identified, or more than one
            could be.
    """
    if not isinstance(loaded, dict):
        return loaded

    if key is not None:
        if key not in loaded:
            raise KeyError(
                f"{path.name} has no key {key!r}; it holds: {sorted(loaded)}",
            )
        return loaded[key]

    candidates = [name for name, value in loaded.items() if hasattr(value, "predict")]
    if len(candidates) == 1:
        return loaded[candidates[0]]
    if not candidates:
        raise TypeError(
            f"{path.name} holds a dict with no estimator in it: {sorted(loaded)}. "
            "Pass key= if the model is nested deeper.",
        )
    raise TypeError(
        f"{path.name} holds {len(candidates)} estimators ({sorted(candidates)}); "
        "pass key= to say which one to convert.",
    )


def load_sklearn_artifact(
    path: str | Path,
    *,
    key: str | None = None,
    expected_sha256: str | None = None,
) -> LoadedArtifact:
    """Read a fitted estimator out of a pickle, recording its provenance.

    Example:

        >>> artifact = load_sklearn_artifact("artifacts/risk.pkl")
        >>> artifact.estimator_type, artifact.sha256[:12]
        ('RandomForestClassifier', 'a1b2c3d4e5f6')

    Pinning the digest the release recorded:

        >>> artifact = load_sklearn_artifact(
        ...     "artifacts/risk.pkl",
        ...     expected_sha256="sha256:A1B2C3D4E5F6...",
        ... )

    **This executes the code in the file.** Point it at artifacts your own
    pipeline produced, in your own build environment — never at an upload,
    and never at something a device downloaded.

    The file is opened once: it is hashed through that handle, compared
    with ``expected_sha256`` when given, and only then unpickled from the
    same handle — so the bytes that were checked are the bytes that run,
    even if the path is swapped for another file in between.

    Args:
        path (str | Path): Local path to a ``joblib`` or ``pickle`` file.
        key (str | None): Entry to take when the pickle holds a dict. Not
            needed when exactly one entry can predict.
        expected_sha256 (str | None): The SHA-256 the file must have, as
            hex in any case, optionally prefixed with ``sha256:``. Checked
            before unpickling; ``None`` skips the check and only records
            the digest.

    Returns:
        LoadedArtifact: The estimator plus its digest, size, the
        scikit-learn version that read it, any warnings raised, and the
        column order when the estimator recorded one.

    Raises:
        ValueError: When the path looks like a URL. Downloading and loading
            in one call is the shape that turns a registry into remote code
            execution; fetch deliberately first if that is what you mean.
            Also raised when ``expected_sha256`` is not a SHA-256 hex
            digest.
        FileNotFoundError: When the file does not exist.
        ArtifactDigestMismatchError: When the file's SHA-256 differs from
            ``expected_sha256``. The file is not unpickled.
        TypeError: When the file holds something that cannot predict.
        ImportError: When ``joblib`` (which ships with scikit-learn) is
            unavailable.
    """
    text = str(path)
    if text.startswith(_URL_PREFIXES):
        raise ValueError(
            f"refusing to load a pickle from {text!r}: unpickling executes code, "
            "so this call only takes a local path you produced. Download it "
            "deliberately first if that is really what you want.",
        )

    pinned = _normalise_sha256(expected_sha256) if expected_sha256 is not None else None

    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"pickle not found: {source}")

    try:
        import joblib
    except ImportError as exc:  # pragma: no cover - joblib ships with sklearn
        raise ImportError(
            "reading a .pkl needs joblib, which ships with scikit-learn. "
            "Install with: pip install tempest-fastapi-sdk[modelops-sklearn]",
        ) from exc

    with source.open("rb") as handle:
        digest = _digest(handle)
        if pinned is not None and digest != pinned:
            raise ArtifactDigestMismatchError(str(source), pinned, digest)
        handle.seek(0)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            loaded = joblib.load(handle)
            raised = [
                f"{type(item.message).__name__}: {item.message}" for item in caught
            ]

    estimator = _unwrap(loaded, key, source)
    if not hasattr(estimator, "predict"):
        raise TypeError(
            f"{source.name} holds a {type(estimator).__name__}, which has no "
            "predict(); it is not a fitted estimator.",
        )

    import sklearn

    recorded = getattr(estimator, "feature_names_in_", None)
    return LoadedArtifact(
        estimator=estimator,
        source_path=str(source),
        sha256=digest,
        bytes=source.stat().st_size,
        sklearn_version=str(sklearn.__version__),
        estimator_type=type(estimator).__name__,
        feature_names=[str(name) for name in recorded] if recorded is not None else [],
        load_warnings=raised,
    )


def edge_pipeline_from_pickle(
    path: str | Path,
    samples: Any,
    output_dir: str | Path,
    *,
    key: str | None = None,
    expected_sha256: str | None = None,
    feature_names: Sequence[str] | None = None,
    **kwargs: Any,
) -> EdgePackage:
    """Convert a pickled estimator into a shippable edge package.

    The build-time half of the story: the pickle stays in your pipeline,
    the ONNX package goes to devices and browsers.

    Example:

        >>> package = edge_pipeline_from_pickle(
        ...     "artifacts/risk.pkl", X_train, "dist/risk", labels=y_train,
        ... )
        >>> package.manifest.source.file, package.manifest.verified
        ('risk.pkl', True)

    Column order comes from the estimator's own ``feature_names_in_`` when
    it has one, so a model fitted on a DataFrame carries its columns into
    the manifest without anyone retyping them — which is the field that
    catches a browser sending features in the wrong order.

    The manifest records the pickle's name, digest and the scikit-learn
    version that read it, so an artifact running on a device can be traced
    back to the file that produced it.

    Args:
        path (str | Path): Local path to the pickle.
        samples (Any): Training rows, used to trace the export and build
            the drift baseline.
        output_dir (str | Path): Package directory to create.
        key (str | None): Entry to take when the pickle holds a dict.
        expected_sha256 (str | None): The SHA-256 the pickle must have,
            checked before it is unpickled. See
            :func:`load_sklearn_artifact` for the accepted format.
        feature_names (Sequence[str] | None): Column order, overriding
            whatever the estimator recorded.
        **kwargs (Any): Forwarded to
            :func:`~tempest_fastapi_sdk.modelops.edge_pipeline` — ``name``,
            ``labels``, ``version``, ``verify_samples``, ``compress`` and
            the rest.

    Returns:
        EdgePackage: The written package, with provenance in its manifest.

    Raises:
        ValueError: When the path is a URL, or when the exported graph does
            not reproduce the estimator.
        FileNotFoundError: When the pickle does not exist.
        ArtifactDigestMismatchError: When the pickle's SHA-256 differs from
            ``expected_sha256``. Nothing is unpickled or written.
        TypeError: When the pickle holds something that cannot predict.
    """
    artifact = load_sklearn_artifact(
        path,
        key=key,
        expected_sha256=expected_sha256,
    )
    resolved = feature_names if feature_names is not None else artifact.feature_names
    package = edge_pipeline(
        artifact.estimator,
        samples,
        output_dir,
        feature_names=resolved or None,
        **kwargs,
    )

    stamped = package.manifest.model_copy(
        update={
            "source": ArtifactSource(
                file=Path(artifact.source_path).name,
                kind="pickle",
                sha256=artifact.sha256,
                bytes=artifact.bytes,
                sklearn_version=artifact.sklearn_version,
                warnings=artifact.load_warnings,
            ),
        },
    )
    Path(package.manifest_path).write_text(
        stamped.model_dump_json(indent=1),
        encoding="utf-8",
    )
    return package.model_copy(update={"manifest": stamped})


__all__: list[str] = [
    "ArtifactDigestMismatchError",
    "LoadedArtifact",
    "edge_pipeline_from_pickle",
    "load_sklearn_artifact",
]
