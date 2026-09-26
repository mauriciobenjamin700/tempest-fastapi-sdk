"""Tests for the prediction router's serving guarantees.

Each class pins a defect that shipped: inference and reload ran on the event
loop, the batch had no size limit, the operational endpoints had no guard and
leaked the device's absolute path, and concurrent syncs downloaded and
reloaded the same version twice without checking what arrived.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk.modelops.router import (
    DEFAULT_MAX_PREDICT_ROWS,
    RegistryModelSource,
    make_prediction_router,
)
from tempest_fastapi_sdk.modelops.serving import (
    OnnxPredictor,
    Prediction,
    PredictorInfo,
)

_BLOCKING_SECONDS: float = 0.4
"""How long the stub predictor holds the calling thread."""

_LOOP_LAG_LIMIT: float = 0.2
"""Largest scheduling delay tolerated while a blocking call is in flight."""


class _SlowPredictor:
    """A predictor whose calls hold the thread, like a large ONNX session.

    Attributes:
        info (PredictorInfo): What the router reports as loaded.
        reloads (list[Path]): Every path :meth:`reload` was called with.
    """

    def __init__(self) -> None:
        """Describe a fixed-width model at an absolute device path."""
        self.info = PredictorInfo(
            path="/srv/device/models/fraud-v1.onnx",
            input_name="input",
            n_features=2,
        )
        self.reloads: list[Path] = []

    def predict(self, features: Any) -> Prediction:
        """Block the thread, then answer.

        Args:
            features (Any): The rows.

        Returns:
            Prediction: One zero label per row.
        """
        time.sleep(_BLOCKING_SECONDS)
        return Prediction(labels=[0] * len(features), n_rows=len(features))

    def reload(self, model_path: str | Path) -> PredictorInfo:
        """Block the thread, then record the reload.

        Args:
            model_path (str | Path): The new model.

        Returns:
            PredictorInfo: The unchanged description.
        """
        time.sleep(_BLOCKING_SECONDS)
        self.reloads.append(Path(model_path))
        return self.info


class _Row:
    """A registry row for version ``v2``.

    Attributes:
        version (str): The version label.
        file_key (str): The object key.
        sha256 (str | None): The expected digest, when the row carries one.
    """

    version = "v2"
    file_key = "models/m-v2.onnx"

    def __init__(self, sha256: str | None = None) -> None:
        """Set the digest the row advertises.

        Args:
            sha256 (str | None): Expected hex SHA-256.
        """
        self.sha256 = sha256


class _Minio:
    """Object storage that writes ``payload`` after yielding to the loop.

    Attributes:
        payload (bytes): What a download produces.
        downloads (list[str]): Keys downloaded.
        fail_after_partial (bool): Write half the bytes, then raise.
    """

    def __init__(self, payload: bytes, *, fail_after_partial: bool = False) -> None:
        """Configure the fake.

        Args:
            payload (bytes): Bytes each download writes.
            fail_after_partial (bool): Simulate a connection drop mid-file.
        """
        self.payload = payload
        self.downloads: list[str] = []
        self.fail_after_partial = fail_after_partial

    async def fget_object(
        self,
        key: str,
        file_path: Any,
        *,
        bucket: str | None = None,
    ) -> Path:
        """Write the payload to ``file_path``.

        Args:
            key (str): Object key.
            file_path (Any): Destination.
            bucket (str | None): Bucket name.

        Returns:
            Path: The destination.

        Raises:
            ConnectionError: When ``fail_after_partial`` is set.
        """
        self.downloads.append(key)
        await asyncio.sleep(0.05)
        if self.fail_after_partial:
            Path(file_path).write_bytes(self.payload[: len(self.payload) // 2])
            raise ConnectionError("connection reset mid-download")
        Path(file_path).write_bytes(self.payload)
        return Path(file_path)


class _Registry:
    """A registry whose current row is fixed.

    Attributes:
        minio (_Minio): The storage client.
        bucket (str): The bucket name.
        row (_Row): What :meth:`current` returns.
    """

    bucket = "artifacts"

    def __init__(self, minio: _Minio, row: _Row) -> None:
        """Store the collaborators.

        Args:
            minio (_Minio): The storage client.
            row (_Row): The current row.
        """
        self.minio = minio
        self.row = row

    async def current(self, name: str) -> _Row:
        """Return the fixed row.

        Args:
            name (str): The artifact key.

        Returns:
            _Row: The current row.
        """
        return self.row


async def _max_loop_lag(until: asyncio.Task[Any]) -> float:
    """Measure the worst scheduling delay while ``until`` runs.

    Args:
        until (asyncio.Task[Any]): The task whose duration is observed.

    Returns:
        float: The largest gap, in seconds, beyond a 10 ms tick.
    """
    worst = 0.0
    while not until.done():
        started = time.perf_counter()
        await asyncio.sleep(0.01)
        worst = max(worst, time.perf_counter() - started - 0.01)
    return worst


@pytest.fixture
async def slow_client() -> AsyncIterator[tuple[AsyncClient, _SlowPredictor]]:
    """Serve a slow predictor through an in-process ASGI client.

    Yields:
        tuple[AsyncClient, _SlowPredictor]: The client and the predictor.
    """
    predictor = _SlowPredictor()
    app = FastAPI()
    app.include_router(make_prediction_router(predictor))
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client, predictor


class TestInferenceLeavesTheLoopFree:
    async def test_predict_does_not_stall_the_event_loop(
        self,
        slow_client: tuple[AsyncClient, _SlowPredictor],
    ) -> None:
        """A 0.4 s inference used to freeze every other request for 0.4 s."""
        client, _ = slow_client
        request = asyncio.create_task(
            client.post("/api/predict/", json={"rows": [[0.1, 0.2]]}),
        )
        lag = await _max_loop_lag(request)
        assert (await request).status_code == 200
        assert lag < _LOOP_LAG_LIMIT

    async def test_sync_reload_does_not_stall_the_event_loop(
        self,
        tmp_path: Path,
    ) -> None:
        predictor = _SlowPredictor()
        source = RegistryModelSource(
            _Registry(_Minio(b"model"), _Row()),
            "m",
            tmp_path / "cache",
        )
        task = asyncio.create_task(source.sync(predictor))
        lag = await _max_loop_lag(task)
        assert await task == "v2"
        assert lag < _LOOP_LAG_LIMIT


class TestBatchLimit:
    def _client(self, **options: Any) -> TestClient:
        app = FastAPI()
        predictor = _SlowPredictor()
        predictor.predict = lambda features: Prediction(
            labels=[0] * len(features),
            n_rows=len(features),
        )
        app.include_router(make_prediction_router(predictor, **options))
        return TestClient(app)

    def test_a_batch_above_the_limit_is_a_422(self) -> None:
        client = self._client(max_rows=3)
        response = client.post("/api/predict/", json={"rows": [[0.0, 0.0]] * 4})
        assert response.status_code == 422
        assert "at most 3 rows" in response.json()["detail"]

    def test_a_batch_at_the_limit_is_served(self) -> None:
        client = self._client(max_rows=3)
        response = client.post("/api/predict/", json={"rows": [[0.0, 0.0]] * 3})
        assert response.status_code == 200
        assert response.json()["n_rows"] == 3

    def test_the_default_limit_applies_without_configuration(self) -> None:
        client = self._client()
        too_many = [[0.0, 0.0]] * (DEFAULT_MAX_PREDICT_ROWS + 1)
        assert client.post("/api/predict/", json={"rows": too_many}).status_code == 422

    def test_none_disables_the_limit(self) -> None:
        client = self._client(max_rows=None)
        rows = [[0.0, 0.0]] * (DEFAULT_MAX_PREDICT_ROWS + 1)
        assert client.post("/api/predict/", json={"rows": rows}).status_code == 200

    def test_a_non_positive_limit_is_refused_at_build_time(self) -> None:
        with pytest.raises(ValueError, match="max_rows"):
            make_prediction_router(_SlowPredictor(), max_rows=0)


def _require_token(x_token: str = Header(default="")) -> None:
    """Reject requests without the operator token.

    Args:
        x_token (str): The ``X-Token`` header.

    Raises:
        HTTPException: ``401`` when the token is wrong.
    """
    if x_token != "operator":
        raise HTTPException(status_code=401, detail="operator token required")


class TestOperationalEndpoints:
    def _client(self, tmp_path: Path, **options: Any) -> TestClient:
        source = RegistryModelSource(
            _Registry(_Minio(b"model"), _Row()),
            "m",
            tmp_path / "cache",
        )
        app = FastAPI()
        app.include_router(
            make_prediction_router(
                _SlowPredictor(),
                source=source,
                **options,
            ),
        )
        return TestClient(app)

    def test_the_model_endpoint_hides_the_absolute_path_by_default(
        self,
        tmp_path: Path,
    ) -> None:
        body = self._client(tmp_path).get("/api/predict/model").json()
        assert body["path"] == "fraud-v1.onnx"

    def test_the_absolute_path_is_shown_when_asked_for(self, tmp_path: Path) -> None:
        client = self._client(tmp_path, expose_model_path=True)
        body = client.get("/api/predict/model").json()
        assert body["path"] == "/srv/device/models/fraud-v1.onnx"

    def test_sync_hides_the_absolute_path_by_default(self, tmp_path: Path) -> None:
        body = self._client(tmp_path).post("/api/predict/model/sync").json()
        assert body["path"] == "fraud-v1.onnx"

    def test_admin_dependencies_guard_sync_and_model(self, tmp_path: Path) -> None:
        client = self._client(
            tmp_path,
            admin_dependencies=[Depends(_require_token)],
        )
        assert client.post("/api/predict/model/sync").status_code == 401
        assert client.get("/api/predict/model").status_code == 401
        allowed = client.post(
            "/api/predict/model/sync",
            headers={"X-Token": "operator"},
        )
        assert allowed.status_code == 200

    def test_admin_dependencies_leave_predict_open(self, tmp_path: Path) -> None:
        client = self._client(
            tmp_path,
            admin_dependencies=[Depends(_require_token)],
        )
        response = client.post("/api/predict/", json={"rows": [[0.0, 0.0]]})
        assert response.status_code == 200

    def test_dependencies_guard_every_route(self, tmp_path: Path) -> None:
        client = self._client(tmp_path, dependencies=[Depends(_require_token)])
        response = client.post("/api/predict/", json={"rows": [[0.0, 0.0]]})
        assert response.status_code == 401


class TestRegistrySync:
    async def test_concurrent_syncs_download_and_reload_once(
        self,
        tmp_path: Path,
    ) -> None:
        minio = _Minio(b"model")
        source = RegistryModelSource(
            _Registry(minio, _Row()),
            "m",
            tmp_path / "cache",
        )
        predictor = _SlowPredictor()
        results = await asyncio.gather(
            source.sync(predictor),
            source.sync(predictor),
            source.sync(predictor),
        )
        assert results == ["v2", "v2", "v2"]
        assert len(minio.downloads) == 1
        assert len(predictor.reloads) == 1

    async def test_an_interrupted_download_is_not_mistaken_for_a_cached_one(
        self,
        tmp_path: Path,
    ) -> None:
        broken = _Minio(b"0123456789", fail_after_partial=True)
        registry = _Registry(broken, _Row())
        source = RegistryModelSource(registry, "m", tmp_path / "cache")
        with pytest.raises(ConnectionError):
            await source.fetch()

        registry.minio = _Minio(b"0123456789")
        found = await source.fetch()
        assert found is not None
        assert found[1].read_bytes() == b"0123456789"

    async def test_a_matching_digest_is_accepted(self, tmp_path: Path) -> None:
        payload = b"the real model"
        row = _Row(sha256=hashlib.sha256(payload).hexdigest())
        source = RegistryModelSource(
            _Registry(_Minio(payload), row),
            "m",
            tmp_path / "cache",
        )
        found = await source.fetch()
        assert found is not None
        assert found[1].read_bytes() == payload

    async def test_a_mismatched_digest_is_refused_and_not_cached(
        self,
        tmp_path: Path,
    ) -> None:
        row = _Row(sha256=hashlib.sha256(b"what was published").hexdigest())
        source = RegistryModelSource(
            _Registry(_Minio(b"what arrived"), row),
            "m",
            tmp_path / "cache",
        )
        predictor = _SlowPredictor()
        with pytest.raises(RuntimeError, match="SHA-256"):
            await source.sync(predictor)
        assert predictor.reloads == []
        assert list((tmp_path / "cache").iterdir()) == []

    def test_a_mismatched_digest_is_a_503_over_http(self, tmp_path: Path) -> None:
        row = _Row(sha256=hashlib.sha256(b"what was published").hexdigest())
        source = RegistryModelSource(
            _Registry(_Minio(b"what arrived"), row),
            "m",
            tmp_path / "cache",
        )
        app = FastAPI()
        app.include_router(make_prediction_router(_SlowPredictor(), source=source))
        response = TestClient(app).post("/api/predict/model/sync")
        assert response.status_code == 503
        assert "SHA-256" in response.json()["detail"]

    async def test_the_checksum_field_can_be_disabled(self, tmp_path: Path) -> None:
        row = _Row(sha256="0" * 64)
        source = RegistryModelSource(
            _Registry(_Minio(b"model"), row),
            "m",
            tmp_path / "cache",
            checksum_field=None,
        )
        assert await source.fetch() is not None


class TestRealPredictorOverHttp:
    @pytest.fixture
    def model_path(self, tmp_path: Path) -> Path:
        """Export a small classifier to serve.

        Returns:
            Path: The exported ``.onnx`` file.
        """
        pytest.importorskip("sklearn")
        pytest.importorskip("skl2onnx")
        pytest.importorskip("onnxruntime")
        import numpy
        from sklearn.linear_model import LogisticRegression

        from tempest_fastapi_sdk.modelops.sklearn import export_sklearn_to_onnx

        features = numpy.random.RandomState(0).normal(size=(100, 2))
        target = (features[:, 0] > 0).astype(int)
        model = LogisticRegression().fit(features, target)
        export = export_sklearn_to_onnx(model, features[:10], tmp_path / "m.onnx")
        return Path(export.path)

    def test_a_real_model_still_answers_from_the_worker_thread(
        self,
        model_path: Path,
    ) -> None:
        app = FastAPI()
        app.include_router(make_prediction_router(OnnxPredictor(model_path)))
        client = TestClient(app)
        response = client.post("/api/predict/", json={"rows": [[2.0, 0.0]]})
        assert response.status_code == 200
        assert response.json()["labels"] == [1]
