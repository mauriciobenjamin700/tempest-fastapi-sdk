"""Tests for the edge predictor and its HTTP surface."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk.modelops.router import (
    RegistryModelSource,
    make_prediction_router,
)
from tempest_fastapi_sdk.modelops.serving import (
    DEFAULT_INTRA_OP_THREADS,
    OnnxPredictor,
)
from tempest_fastapi_sdk.modelops.sklearn import export_sklearn_to_onnx

pytest.importorskip("sklearn")
pytest.importorskip("skl2onnx")
pytest.importorskip("onnxruntime")


@pytest.fixture
def classifier_path(tmp_path: Path) -> Path:
    """Export a small multi-class classifier and return its path."""
    from sklearn.datasets import make_classification
    from sklearn.ensemble import RandomForestClassifier

    features, target = make_classification(
        n_samples=120,
        n_features=4,
        n_informative=4,
        n_redundant=0,
        n_repeated=0,
        n_classes=3,
        n_clusters_per_class=1,
        random_state=0,
    )
    model = RandomForestClassifier(n_estimators=8, random_state=0).fit(
        features,
        target,
    )
    export = export_sklearn_to_onnx(model, features[:10], tmp_path / "c.onnx")
    return Path(export.path)


@pytest.fixture
def regressor_path(tmp_path: Path) -> Path:
    """Export a regressor and return its path."""
    from sklearn.datasets import make_regression
    from sklearn.linear_model import LinearRegression

    features, target = make_regression(n_samples=80, n_features=4, random_state=0)
    model = LinearRegression().fit(features, target)
    export = export_sklearn_to_onnx(model, features[:10], tmp_path / "r.onnx")
    return Path(export.path)


class TestPredictorLoading:
    def test_it_describes_the_graph(self, classifier_path: Path) -> None:
        predictor = OnnxPredictor(classifier_path)
        info = predictor.info
        assert info.input_name == "input"
        assert info.n_features == 4
        assert info.is_classifier is True
        assert info.label_output is not None
        assert info.proba_output is not None

    def test_it_reports_the_providers_actually_in_use(
        self,
        classifier_path: Path,
    ) -> None:
        """Requested providers and active providers are not the same thing."""
        predictor = OnnxPredictor(classifier_path)
        assert predictor.info.providers
        assert "CPUExecutionProvider" in predictor.info.providers

    def test_it_defaults_to_a_single_intra_op_thread(
        self,
        classifier_path: Path,
    ) -> None:
        predictor = OnnxPredictor(classifier_path)
        assert predictor.info.intra_op_threads == DEFAULT_INTRA_OP_THREADS
        assert DEFAULT_INTRA_OP_THREADS == 1

    def test_threads_are_configurable(self, classifier_path: Path) -> None:
        predictor = OnnxPredictor(classifier_path, intra_op_threads=2)
        assert predictor.info.intra_op_threads == 2

    def test_a_missing_file_fails_clearly(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="model not found"):
            OnnxPredictor(tmp_path / "nope.onnx")

    def test_a_regressor_has_no_probability_output(
        self,
        regressor_path: Path,
    ) -> None:
        predictor = OnnxPredictor(regressor_path)
        assert predictor.info.is_classifier is False
        assert predictor.info.proba_output is None


class TestPrediction:
    def test_it_predicts_a_batch(self, classifier_path: Path) -> None:
        predictor = OnnxPredictor(classifier_path)
        result = predictor.predict([[0.1, 0.2, 0.3, 0.4], [1.0, 1.1, 1.2, 1.3]])
        assert result.n_rows == 2
        assert len(result.labels) == 2
        assert len(result.probabilities) == 2
        assert result.seconds > 0

    def test_probabilities_sum_to_about_one(self, classifier_path: Path) -> None:
        predictor = OnnxPredictor(classifier_path)
        result = predictor.predict([[0.1, 0.2, 0.3, 0.4]])
        assert abs(sum(result.probabilities[0]) - 1.0) < 1e-3

    def test_a_regressor_returns_values_without_probabilities(
        self,
        regressor_path: Path,
    ) -> None:
        predictor = OnnxPredictor(regressor_path)
        result = predictor.predict([[0.1, 0.2, 0.3, 0.4]])
        assert len(result.labels) == 1
        assert result.probabilities == []

    def test_a_flat_row_is_refused_with_a_hint(
        self,
        classifier_path: Path,
    ) -> None:
        predictor = OnnxPredictor(classifier_path)
        with pytest.raises(ValueError, match=r"\[\[\.\.\.\]\]"):
            predictor.predict([0.1, 0.2, 0.3, 0.4])

    def test_the_wrong_width_is_refused_before_the_runtime(
        self,
        classifier_path: Path,
    ) -> None:
        predictor = OnnxPredictor(classifier_path)
        with pytest.raises(ValueError, match="expects 4 features"):
            predictor.predict([[0.1, 0.2]])

    def test_it_accepts_a_numpy_array(self, classifier_path: Path) -> None:
        import numpy

        predictor = OnnxPredictor(classifier_path)
        result = predictor.predict(numpy.zeros((3, 4)))
        assert result.n_rows == 3


class TestReload:
    def test_it_swaps_the_model(
        self,
        classifier_path: Path,
        regressor_path: Path,
    ) -> None:
        predictor = OnnxPredictor(classifier_path)
        assert predictor.info.is_classifier is True
        predictor.reload(regressor_path)
        assert predictor.info.is_classifier is False
        assert predictor.path == regressor_path

    def test_a_missing_file_leaves_the_old_model_serving(
        self,
        classifier_path: Path,
        tmp_path: Path,
    ) -> None:
        predictor = OnnxPredictor(classifier_path)
        with pytest.raises(FileNotFoundError):
            predictor.reload(tmp_path / "ghost.onnx")
        assert predictor.predict([[0.1, 0.2, 0.3, 0.4]]).n_rows == 1

    def test_a_corrupt_file_leaves_the_old_model_serving(
        self,
        classifier_path: Path,
        tmp_path: Path,
    ) -> None:
        """A bad rollout must degrade to the previous version, not to nothing."""
        broken = tmp_path / "broken.onnx"
        broken.write_bytes(b"this is not a model")
        predictor = OnnxPredictor(classifier_path)
        with pytest.raises(Exception, match="Protobuf parsing failed"):
            predictor.reload(broken)
        assert predictor.predict([[0.1, 0.2, 0.3, 0.4]]).n_rows == 1
        assert predictor.path == classifier_path


class TestRouter:
    def _client(self, path: Path, source: Any = None) -> TestClient:
        app = FastAPI()
        app.include_router(
            make_prediction_router(OnnxPredictor(path), source=source),
        )
        return TestClient(app)

    def test_predict_returns_labels_and_scores(
        self,
        classifier_path: Path,
    ) -> None:
        client = self._client(classifier_path)
        response = client.post(
            "/api/predict/",
            json={"rows": [[0.1, 0.2, 0.3, 0.4]]},
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["labels"]) == 1
        assert len(body["probabilities"][0]) == 3
        assert body["n_rows"] == 1

    def test_the_wrong_width_is_a_422_not_a_500(
        self,
        classifier_path: Path,
    ) -> None:
        client = self._client(classifier_path)
        response = client.post("/api/predict/", json={"rows": [[0.1, 0.2]]})
        assert response.status_code == 422
        assert "expects 4 features" in response.json()["detail"]

    def test_the_model_endpoint_describes_what_is_loaded(
        self,
        classifier_path: Path,
    ) -> None:
        client = self._client(classifier_path)
        body = client.get("/api/predict/model").json()
        assert body["n_features"] == 4
        assert body["is_classifier"] is True
        assert body["providers"]

    def test_sync_is_absent_without_a_source(
        self,
        classifier_path: Path,
    ) -> None:
        client = self._client(classifier_path)
        assert client.post("/api/predict/model/sync").status_code == 404


class TestRegistrySource:
    @pytest.mark.asyncio
    async def test_no_current_version_is_not_an_error(
        self,
        classifier_path: Path,
        tmp_path: Path,
    ) -> None:
        class EmptyRegistry:
            minio = None
            bucket = None

            async def current(self, name: str) -> None:
                return None

        source = RegistryModelSource(EmptyRegistry(), "m", tmp_path / "cache")
        predictor = OnnxPredictor(classifier_path)
        assert await source.sync(predictor) is None

    @pytest.mark.asyncio
    async def test_a_cached_version_is_not_downloaded_again(
        self,
        classifier_path: Path,
        tmp_path: Path,
    ) -> None:
        downloads: list[str] = []

        class Row:
            version = "v2"
            file_key = "models/m-v2.onnx"

        class Minio:
            async def fget_object(
                self,
                key: str,
                file_path: Any,
                *,
                bucket: str | None = None,
            ) -> Path:
                downloads.append(key)
                Path(file_path).write_bytes(classifier_path.read_bytes())
                return Path(file_path)

        class Registry:
            minio = Minio()
            bucket = "artifacts"

            async def current(self, name: str) -> Row:
                return Row()

        source = RegistryModelSource(Registry(), "m", tmp_path / "cache")
        predictor = OnnxPredictor(classifier_path)

        assert await source.sync(predictor) == "v2"
        assert await source.sync(predictor) == "v2"
        assert len(downloads) == 1

    @pytest.mark.asyncio
    async def test_a_registered_version_without_storage_says_so(
        self,
        classifier_path: Path,
        tmp_path: Path,
    ) -> None:
        class Row:
            version = "v1"
            file_key = "models/m.onnx"

        class Registry:
            minio = None
            bucket = None

            async def current(self, name: str) -> Row:
                return Row()

        source = RegistryModelSource(Registry(), "m", tmp_path / "cache")
        with pytest.raises(RuntimeError, match="object-storage"):
            await source.fetch()

    @pytest.mark.asyncio
    async def test_sync_reloads_the_predictor(
        self,
        classifier_path: Path,
        regressor_path: Path,
        tmp_path: Path,
    ) -> None:
        class Row:
            version = "v3"
            file_key = "models/m-v3.onnx"

        class Minio:
            async def fget_object(
                self,
                key: str,
                file_path: Any,
                *,
                bucket: str | None = None,
            ) -> Path:
                Path(file_path).write_bytes(regressor_path.read_bytes())
                return Path(file_path)

        class Registry:
            minio = Minio()
            bucket = "artifacts"

            async def current(self, name: str) -> Row:
                return Row()

        source = RegistryModelSource(Registry(), "m", tmp_path / "cache")
        predictor = OnnxPredictor(classifier_path)
        assert predictor.info.is_classifier is True

        await source.sync(predictor)
        assert predictor.info.is_classifier is False
        assert source.current_version == "v3"
