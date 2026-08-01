"""Tests for the pickle-to-edge-package bridge.

Real pickles, written by joblib the way a training pipeline writes them.
The point of the bridge is what happens between two file formats, so a
mocked loader would test nothing that matters.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk.modelops.pickled import (
    edge_pipeline_from_pickle,
    load_sklearn_artifact,
)

pytest.importorskip("sklearn")
pytest.importorskip("skl2onnx")
pytest.importorskip("onnxruntime")
joblib = pytest.importorskip("joblib")


@pytest.fixture
def training_data() -> tuple[Any, Any]:
    """Return a small 3-class dataset."""
    from sklearn.datasets import make_classification

    return make_classification(
        n_samples=300,
        n_features=4,
        n_informative=4,
        n_redundant=0,
        n_repeated=0,
        n_classes=3,
        n_clusters_per_class=1,
        random_state=0,
    )


@pytest.fixture
def estimator(training_data: tuple[Any, Any]) -> Any:
    """Return a fitted classifier."""
    from sklearn.ensemble import RandomForestClassifier

    features, target = training_data
    return RandomForestClassifier(
        n_estimators=10,
        max_depth=6,
        random_state=0,
    ).fit(features, target)


@pytest.fixture
def pickle_path(tmp_path: Path, estimator: Any) -> Path:
    """Dump the estimator the way a training pipeline would."""
    path = tmp_path / "risk.pkl"
    joblib.dump(estimator, path)
    return path


class TestLoading:
    def test_it_reads_the_estimator_back(self, pickle_path: Path) -> None:
        artifact = load_sklearn_artifact(pickle_path)
        assert artifact.estimator_type == "RandomForestClassifier"
        assert hasattr(artifact.estimator, "predict")

    def test_it_records_provenance(self, pickle_path: Path) -> None:
        """A device artifact has to be traceable to the file that made it."""
        artifact = load_sklearn_artifact(pickle_path)
        assert len(artifact.sha256) == 64
        assert artifact.bytes == pickle_path.stat().st_size
        assert artifact.source_path == str(pickle_path)

    def test_it_records_the_version_that_read_it(self, pickle_path: Path) -> None:
        """The pickle stores no version of its own; this is the only one there is."""
        import sklearn

        artifact = load_sklearn_artifact(pickle_path)
        assert artifact.sklearn_version == sklearn.__version__

    def test_a_clean_load_raises_no_warnings(self, pickle_path: Path) -> None:
        assert load_sklearn_artifact(pickle_path).load_warnings == []

    def test_it_refuses_a_url(self, tmp_path: Path) -> None:
        """Download-and-unpickle in one call is remote code execution."""
        with pytest.raises(ValueError, match="executes code"):
            load_sklearn_artifact("https://example.com/model.pkl")

    def test_a_missing_file_says_so(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="pickle not found"):
            load_sklearn_artifact(tmp_path / "absent.pkl")

    def test_something_that_cannot_predict_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "not-a-model.pkl"
        joblib.dump({"totally": "unrelated"}, path)
        with pytest.raises(TypeError, match="no estimator"):
            load_sklearn_artifact(path)

    def test_a_bare_object_that_cannot_predict_is_refused(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "list.pkl"
        joblib.dump([1, 2, 3], path)
        with pytest.raises(TypeError, match=r"not a fitted estimator"):
            load_sklearn_artifact(path)


class TestDictArtifacts:
    def test_it_finds_the_only_estimator_in_a_dict(
        self,
        tmp_path: Path,
        estimator: Any,
    ) -> None:
        """Training pipelines dump the model next to its metrics all the time."""
        path = tmp_path / "bundle.pkl"
        joblib.dump({"model": estimator, "auc": 0.91, "trained": "2026-08-01"}, path)

        artifact = load_sklearn_artifact(path)
        assert artifact.estimator_type == "RandomForestClassifier"

    def test_it_refuses_to_guess_between_two(
        self,
        tmp_path: Path,
        estimator: Any,
        training_data: tuple[Any, Any],
    ) -> None:
        from sklearn.linear_model import LogisticRegression

        features, target = training_data
        other = LogisticRegression(max_iter=300).fit(features, target)
        path = tmp_path / "two.pkl"
        joblib.dump({"champion": estimator, "challenger": other}, path)

        with pytest.raises(TypeError, match="2 estimators"):
            load_sklearn_artifact(path)

    def test_an_explicit_key_resolves_it(
        self,
        tmp_path: Path,
        estimator: Any,
        training_data: tuple[Any, Any],
    ) -> None:
        from sklearn.linear_model import LogisticRegression

        features, target = training_data
        other = LogisticRegression(max_iter=300).fit(features, target)
        path = tmp_path / "two.pkl"
        joblib.dump({"champion": estimator, "challenger": other}, path)

        artifact = load_sklearn_artifact(path, key="challenger")
        assert artifact.estimator_type == "LogisticRegression"

    def test_an_unknown_key_lists_what_is_there(
        self,
        tmp_path: Path,
        estimator: Any,
    ) -> None:
        path = tmp_path / "bundle.pkl"
        joblib.dump({"model": estimator, "auc": 0.91}, path)

        with pytest.raises(KeyError, match="auc"):
            load_sklearn_artifact(path, key="estimator")


class TestConversion:
    def test_it_produces_a_package_that_matches_the_estimator(
        self,
        tmp_path: Path,
        pickle_path: Path,
        estimator: Any,
        training_data: tuple[Any, Any],
    ) -> None:
        from tempest_fastapi_sdk.modelops.edge import load_edge_package

        features, target = training_data
        package = edge_pipeline_from_pickle(
            pickle_path,
            features,
            tmp_path / "dist",
            name="risk",
            labels=target,
        )
        assert package.manifest.verified is True

        loaded = load_edge_package(package.directory)
        assert loaded.predictor.predict(features[:20]).labels == (
            estimator.predict(features[:20]).tolist()
        )

    def test_the_manifest_records_where_it_came_from(
        self,
        tmp_path: Path,
        pickle_path: Path,
        training_data: tuple[Any, Any],
    ) -> None:
        import sklearn

        features, target = training_data
        package = edge_pipeline_from_pickle(
            pickle_path,
            features,
            tmp_path / "dist",
            name="risk",
            labels=target,
        )
        source = package.manifest.source
        assert source is not None
        assert source.file == "risk.pkl"
        assert source.kind == "pickle"
        assert source.sha256 == load_sklearn_artifact(pickle_path).sha256
        assert source.sklearn_version == sklearn.__version__

    def test_the_provenance_survives_to_disk(
        self,
        tmp_path: Path,
        pickle_path: Path,
        training_data: tuple[Any, Any],
    ) -> None:
        """A browser reads this file; the stamp has to be in it, not only in memory."""
        features, _ = training_data
        package = edge_pipeline_from_pickle(
            pickle_path,
            features,
            tmp_path / "dist",
            name="risk",
        )
        raw = json.loads(Path(package.manifest_path).read_text(encoding="utf-8"))
        assert raw["source"]["file"] == "risk.pkl"
        assert raw["source"]["kind"] == "pickle"

    def test_it_recovers_the_column_order_from_the_estimator(
        self,
        tmp_path: Path,
        training_data: tuple[Any, Any],
    ) -> None:
        """A model fitted on a DataFrame knows its columns; nothing else does."""
        pandas = pytest.importorskip("pandas")
        from sklearn.linear_model import LogisticRegression

        features, target = training_data
        frame = pandas.DataFrame(features, columns=["age", "income", "tenure", "score"])
        model = LogisticRegression(max_iter=300).fit(frame, target)

        path = tmp_path / "frame.pkl"
        joblib.dump(model, path)

        package = edge_pipeline_from_pickle(path, frame, tmp_path / "dist")
        assert package.manifest.input.feature_names == [
            "age",
            "income",
            "tenure",
            "score",
        ]

    def test_it_reads_feature_names_in_without_pandas(
        self,
        tmp_path: Path,
        estimator: Any,
        training_data: tuple[Any, Any],
    ) -> None:
        """`feature_names_in_` is the attribute the loader reads.

        Setting it directly covers the recovery path everywhere, including
        environments without pandas installed — a DataFrame fit is only one
        way that attribute gets there.
        """
        import numpy

        features, _ = training_data
        estimator.feature_names_in_ = numpy.array(["age", "income", "tenure", "score"])
        path = tmp_path / "named.pkl"
        joblib.dump(estimator, path)

        artifact = load_sklearn_artifact(path)
        assert artifact.feature_names == ["age", "income", "tenure", "score"]

        package = edge_pipeline_from_pickle(path, features, tmp_path / "dist")
        assert package.manifest.input.feature_names == [
            "age",
            "income",
            "tenure",
            "score",
        ]

    def test_explicit_names_win_over_the_recorded_ones(
        self,
        tmp_path: Path,
        pickle_path: Path,
        training_data: tuple[Any, Any],
    ) -> None:
        features, _ = training_data
        package = edge_pipeline_from_pickle(
            pickle_path,
            features,
            tmp_path / "dist",
            feature_names=["a", "b", "c", "d"],
        )
        assert package.manifest.input.feature_names == ["a", "b", "c", "d"]

    def test_it_forwards_pipeline_options(
        self,
        tmp_path: Path,
        pickle_path: Path,
        training_data: tuple[Any, Any],
    ) -> None:
        features, _ = training_data
        package = edge_pipeline_from_pickle(
            pickle_path,
            features,
            tmp_path / "dist",
            name="custom",
            version="2026.08.2",
            compress=False,
            baseline=False,
        )
        assert package.manifest.version == "2026.08.2"
        assert package.manifest.name == "custom"
        assert package.gzip_path is None
        assert package.baseline_path is None
