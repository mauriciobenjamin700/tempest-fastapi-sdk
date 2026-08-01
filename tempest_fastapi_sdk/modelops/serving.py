"""Running an exported model on the device that has to run it.

Exporting produces a `.onnx` file; this is everything between that file
and an answer. It is code every consumer otherwise writes identically and
gets subtly wrong:

* **Which input is the input.** `session.get_inputs()[0].name` is not a
  constant — `skl2onnx` names it what you asked for, other exporters do
  not — and hardcoding it breaks the day you change exporter.
* **Which output is which.** A classifier graph returns labels *and*
  probabilities; a regressor returns one tensor. Indexing `[1]` for
  probabilities works until you deploy a regressor.
* **Thread count.** This is the one that costs real latency. ONNX Runtime
  defaults to one thread per core, which is right on a server and often
  **wrong on a constrained device**: on a 4-core SBC running one model
  per request, the threads spend more time coordinating than computing.
  :class:`OnnxPredictor` defaults to a single intra-op thread for exactly
  that reason — measure before raising it.
* **The first call is slow.** Allocation and kernel selection happen on
  the first `run`. Warming up at construction moves that cost off the
  first real request.

Nothing here trains or exports; it consumes what
:func:`~tempest_fastapi_sdk.modelops.export_sklearn_to_onnx` and the rest
of this package produce.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from tempest_fastapi_sdk.schemas.base import BaseSchema

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_INTRA_OP_THREADS: int = 1
"""Intra-op threads for a constrained device.

ONNX Runtime's own default is one per core. That is right for a server
saturating a large model and usually wrong for edge inference on a small
graph, where the coordination costs more than the parallelism buys.
Raise it only after measuring with
:func:`~tempest_fastapi_sdk.modelops.benchmark_onnx` on the target device
— not on your laptop, whose core count and memory bandwidth are not the
device's.
"""

_LABEL_HINTS: tuple[str, ...] = ("label", "output_label", "class")
"""Output names that indicate predicted classes rather than scores."""

_PROBA_HINTS: tuple[str, ...] = ("probab", "score", "output_probability")
"""Output names that indicate class scores."""


class PredictorInfo(BaseSchema):
    """What a loaded predictor is and how it is configured.

    Attributes:
        path (str): The model file in use.
        input_name (str): Graph input name.
        n_features (int | None): Features per row, when the graph
            declares a fixed second dimension.
        output_names (list[str]): Every graph output, in order.
        label_output (str | None): The output holding predicted classes.
        proba_output (str | None): The output holding class scores.
        providers (list[str]): Execution providers actually in use — not
            the ones requested. ONNX Runtime silently falls back, so a
            device you believe is on CUDA may be on CPU.
        intra_op_threads (int): Threads used inside an operator.
        is_classifier (bool): Whether a class output was found.
    """

    path: str = Field(
        title="Path",
        description="The model file in use.",
        examples=["dist/classifier.onnx"],
    )
    input_name: str = Field(
        title="Input name",
        description="Graph input name.",
        examples=["input"],
    )
    n_features: int | None = Field(
        default=None,
        title="Features",
        description="Features per row, when the graph declares it.",
        examples=[4],
    )
    output_names: list[str] = Field(
        default_factory=list,
        title="Outputs",
        description="Every graph output, in order.",
    )
    label_output: str | None = Field(
        default=None,
        title="Label output",
        description="Output holding predicted classes.",
        examples=["label"],
    )
    proba_output: str | None = Field(
        default=None,
        title="Probability output",
        description="Output holding class scores.",
        examples=["probabilities"],
    )
    providers: list[str] = Field(
        default_factory=list,
        title="Providers",
        description="Execution providers actually in use.",
        examples=[["CPUExecutionProvider"]],
    )
    intra_op_threads: int = Field(
        default=DEFAULT_INTRA_OP_THREADS,
        title="Intra-op threads",
        description="Threads used inside an operator.",
        examples=[1],
    )
    is_classifier: bool = Field(
        default=False,
        title="Is classifier",
        description="Whether a class output was found.",
        examples=[True],
    )


class Prediction(BaseSchema):
    """One batch of predictions.

    Attributes:
        labels (list[Any]): Predicted class or regressed value per row.
        probabilities (list[list[float]]): Class scores per row, empty
            for a regressor or a graph without a score output.
        n_rows (int): Rows predicted.
        seconds (float): Wall-clock duration of the call.
    """

    labels: list[Any] = Field(
        default_factory=list,
        title="Labels",
        description="Predicted class or value per row.",
        examples=[[0, 1, 0]],
    )
    probabilities: list[list[float]] = Field(
        default_factory=list,
        title="Probabilities",
        description="Class scores per row, when the graph produces them.",
    )
    n_rows: int = Field(
        default=0,
        title="Rows",
        description="How many rows were predicted.",
        examples=[3],
    )
    seconds: float = Field(
        default=0.0,
        title="Seconds",
        description="Wall-clock duration of the call.",
        examples=[0.0004],
    )


class OnnxPredictor:
    """A loaded ONNX model, ready to answer.

    Example:

        >>> predictor = OnnxPredictor("dist/classifier.onnx")
        >>> result = predictor.predict([[5.1, 3.5, 1.4, 0.2]])
        >>> result.labels, result.probabilities[0]
        ([0], [0.98, 0.02, 0.0])

    Thread-safe for concurrent :meth:`predict`: ONNX Runtime sessions are,
    and :meth:`reload` swaps the session under a lock so an in-flight call
    finishes against the session it started with.

    Attributes:
        path (Path): The model file currently loaded.
    """

    def __init__(
        self,
        model_path: str | Path,
        *,
        providers: Sequence[str] | None = None,
        intra_op_threads: int = DEFAULT_INTRA_OP_THREADS,
        inter_op_threads: int = 1,
        warmup: bool = True,
        dtype: str = "float32",
    ) -> None:
        """Load a model and prepare it to serve.

        Args:
            model_path (str | Path): The ``.onnx`` or ``.ort`` file.
            providers (Sequence[str] | None): Execution providers in
                priority order, e.g.
                ``["CUDAExecutionProvider", "CPUExecutionProvider"]``.
                ``None`` lets the runtime choose. **Always include a CPU
                fallback** on a GPU device: without one, a driver problem
                turns into a failed load rather than a slower answer.
            intra_op_threads (int): Threads inside an operator. See
                :data:`DEFAULT_INTRA_OP_THREADS` for why this defaults
                to one.
            inter_op_threads (int): Threads across operators.
            warmup (bool): Run one throwaway inference at construction so
                the first real request does not pay for allocation and
                kernel selection.
            dtype (str): Element type to coerce inputs to. Must match what
                the graph was exported with.

        Raises:
            ImportError: When onnxruntime is unavailable.
            FileNotFoundError: When the model file does not exist.
        """
        self.path = Path(model_path)
        if not self.path.exists():
            raise FileNotFoundError(f"model not found: {self.path}")
        self._providers = list(providers) if providers else None
        self._intra = intra_op_threads
        self._inter = inter_op_threads
        self._dtype = dtype
        self._lock = threading.Lock()
        self._session: Any = None
        self._info: PredictorInfo | None = None
        self._load(self.path)
        if warmup:
            self.warm_up()

    def _load(self, path: Path) -> None:
        """Open a session for ``path`` and describe its graph.

        Args:
            path (Path): The model file.
        """
        from tempest_fastapi_sdk.modelops.static import _require_onnxruntime

        onnxruntime = _require_onnxruntime()
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = self._intra
        options.inter_op_num_threads = self._inter

        session = onnxruntime.InferenceSession(
            str(path),
            sess_options=options,
            providers=self._providers,
        )
        graph_input = session.get_inputs()[0]
        shape = list(graph_input.shape or [])
        n_features = (
            int(shape[1]) if len(shape) > 1 and isinstance(shape[1], int) else None
        )
        outputs = [output.name for output in session.get_outputs()]
        label_output = _match_output(outputs, _LABEL_HINTS)
        proba_output = _match_output(outputs, _PROBA_HINTS)
        if label_output is None and proba_output is None and outputs:
            label_output = outputs[0]

        with self._lock:
            self._session = session
            self.path = path
            self._info = PredictorInfo(
                path=str(path),
                input_name=graph_input.name,
                n_features=n_features,
                output_names=outputs,
                label_output=label_output,
                proba_output=proba_output,
                providers=list(session.get_providers()),
                intra_op_threads=self._intra,
                is_classifier=proba_output is not None,
            )

    @property
    def info(self) -> PredictorInfo:
        """Return what is loaded and how it is configured.

        Returns:
            PredictorInfo: The current model's description.
        """
        assert self._info is not None
        return self._info

    def warm_up(self, rows: int = 1) -> None:
        """Run one throwaway inference to pay the first-call cost now.

        Skipped silently when the graph does not declare a fixed feature
        count, since there is no shape to synthesise.

        Args:
            rows (int): Rows in the warm-up batch.
        """
        features = self.info.n_features
        if not features:
            return
        try:
            import numpy

            self.predict(numpy.zeros((rows, features), dtype=self._dtype))
        except Exception:
            pass

    def predict(self, features: Any) -> Prediction:
        """Predict for a batch of rows.

        Example:

            >>> predictor.predict([[5.1, 3.5, 1.4, 0.2], [6.2, 2.9, 4.3, 1.3]])

        Args:
            features (Any): A 2-D array, nested sequence, or DataFrame.
                A single row must still be wrapped: ``[[...]]``.

        Returns:
            Prediction: Labels, probabilities when the graph has them,
            and the call's duration.

        Raises:
            ValueError: When the input is not 2-D, or its width does not
                match what the graph expects. Checking the width here
                turns a confusing runtime error into a clear one.
        """
        import numpy

        values = getattr(features, "values", features)
        array = numpy.asarray(values, dtype=self._dtype)
        if array.ndim != 2:
            raise ValueError(
                f"features must be 2-D (n_rows, n_features); got shape "
                f"{array.shape}. Wrap a single row as [[...]].",
            )
        expected = self.info.n_features
        if expected is not None and array.shape[1] != expected:
            raise ValueError(
                f"model expects {expected} features per row, got {array.shape[1]}",
            )

        with self._lock:
            session = self._session
            info = self.info

        started = time.perf_counter()
        outputs = session.run(None, {info.input_name: array})
        elapsed = time.perf_counter() - started

        named = dict(zip(info.output_names, outputs, strict=False))
        labels_raw = named.get(info.label_output) if info.label_output else outputs[0]
        labels = numpy.asarray(labels_raw).reshape(-1).tolist()

        probabilities: list[list[float]] = []
        if info.proba_output:
            proba_raw = named.get(info.proba_output)
            proba = _as_matrix(proba_raw, numpy)
            if proba is not None:
                probabilities = proba.tolist()

        return Prediction(
            labels=labels,
            probabilities=probabilities,
            n_rows=int(array.shape[0]),
            seconds=elapsed,
        )

    def reload(self, model_path: str | Path, *, warmup: bool = True) -> PredictorInfo:
        """Swap in a different model file without recreating the object.

        The new session is built **before** the old one is dropped, so a
        broken file leaves the predictor serving the previous model
        instead of taking it out of service. That is the behaviour a
        fleet update needs: a bad rollout should degrade to "still on the
        old version", never to "answering nothing".

        Args:
            model_path (str | Path): The new model file.
            warmup (bool): Warm the new session before it serves.

        Returns:
            PredictorInfo: The newly loaded model's description.

        Raises:
            FileNotFoundError: When the new file does not exist.
            Exception: Whatever the runtime raises for an unloadable
                model — the previous model stays in service.
        """
        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(f"model not found: {path}")
        self._load(path)
        if warmup:
            self.warm_up()
        return self.info


def _match_output(names: Sequence[str], hints: Sequence[str]) -> str | None:
    """Return the first output whose name contains one of ``hints``.

    Args:
        names (Sequence[str]): The graph's output names.
        hints (Sequence[str]): Lowercase substrings to look for.

    Returns:
        str | None: The matching name, or ``None``.
    """
    for name in names:
        lowered = name.lower()
        if any(hint in lowered for hint in hints):
            return name
    return None


def _as_matrix(raw: Any, numpy: Any) -> Any:
    """Return ``raw`` as a 2-D array, or ``None`` when it is not one.

    A graph exported with ZipMap returns a list of dictionaries here
    rather than a tensor. Rather than guessing the key order to rebuild a
    matrix, this returns ``None`` and the caller reports labels only —
    export with ZipMap off (the SDK's default) to get scores.

    Args:
        raw (Any): A runtime output.
        numpy (Any): The numpy module.

    Returns:
        Any: A 2-D array, or ``None``.
    """
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return None
    array = numpy.asarray(raw)
    return array if array.ndim == 2 else None


__all__: list[str] = [
    "DEFAULT_INTRA_OP_THREADS",
    "OnnxPredictor",
    "Prediction",
    "PredictorInfo",
]
