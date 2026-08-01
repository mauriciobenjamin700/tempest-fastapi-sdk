"""Tests for the edge package pipeline.

These run against real fitted estimators: the manifest's whole purpose is
to describe what the exporter actually produced, so a fake exporter would
be testing the description of a description.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk.modelops.edge import (
    BASELINE_FILENAME,
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    EdgeManifest,
    edge_pipeline,
    load_edge_package,
    read_manifest,
)

pytest.importorskip("sklearn")
pytest.importorskip("skl2onnx")
pytest.importorskip("onnxruntime")


@pytest.fixture
def training_data() -> tuple[Any, Any]:
    """Return a small 3-class dataset with named columns."""
    from sklearn.datasets import make_classification

    features, target = make_classification(
        n_samples=400,
        n_features=5,
        n_informative=4,
        n_redundant=0,
        n_repeated=0,
        n_classes=3,
        n_clusters_per_class=1,
        random_state=0,
    )
    return features, target


@pytest.fixture
def classifier(training_data: tuple[Any, Any]) -> Any:
    """Return a fitted classifier."""
    from sklearn.ensemble import RandomForestClassifier

    features, target = training_data
    return RandomForestClassifier(
        n_estimators=12,
        max_depth=6,
        random_state=0,
    ).fit(features, target)


@pytest.fixture
def package(tmp_path: Path, classifier: Any, training_data: tuple[Any, Any]) -> Any:
    """Build a package from the fitted classifier."""
    features, target = training_data
    return edge_pipeline(
        classifier,
        features,
        tmp_path / "risk",
        name="risk",
        labels=target,
        feature_names=["age", "income", "tenure", "score", "visits"],
    )


class TestPipeline:
    def test_it_writes_a_self_describing_directory(self, package: Any) -> None:
        directory = Path(package.directory)
        written = sorted(path.name for path in directory.iterdir())
        assert written == [
            "baseline.json",
            "manifest.json",
            "risk.onnx",
            "risk.onnx.gz",
        ]

    def test_the_manifest_pins_its_schema_version(self, package: Any) -> None:
        """A consumer in another language reads this file."""
        assert package.manifest.schema_version == MANIFEST_SCHEMA_VERSION

    def test_it_records_the_column_order(self, package: Any) -> None:
        """The field that catches two swapped columns; nothing else does."""
        assert package.manifest.input.feature_names == [
            "age",
            "income",
            "tenure",
            "score",
            "visits",
        ]
        assert package.manifest.input.features == 5

    def test_it_records_the_classes_in_score_order(self, package: Any) -> None:
        assert package.manifest.output.classes == ["0", "1", "2"]
        assert package.manifest.output.is_classifier is True

    def test_it_verifies_the_export_against_the_estimator(self, package: Any) -> None:
        assert package.manifest.verified is True
        assert package.manifest.verification is not None
        assert package.manifest.verification.label_agreement == 1.0

    def test_the_version_is_the_content_hash_by_default(self, package: Any) -> None:
        """Republishing identical bytes must not look like a new model."""
        assert package.manifest.version == package.manifest.model.sha256[:12]

    def test_an_explicit_version_wins(
        self,
        tmp_path: Path,
        classifier: Any,
        training_data: tuple[Any, Any],
    ) -> None:
        features, _ = training_data
        built = edge_pipeline(
            classifier,
            features,
            tmp_path / "v",
            version="2026.08.1",
        )
        assert built.manifest.version == "2026.08.1"

    def test_the_digest_matches_the_file(self, package: Any) -> None:
        from tempest_fastapi_sdk.modelops.edge import _digest

        assert package.manifest.model.sha256 == _digest(Path(package.model_path))

    def test_gzip_is_the_lever_that_pays(self, package: Any) -> None:
        """Measured at ~10-13% on real forests; assert the order of magnitude."""
        assert package.manifest.model.gzip_bytes is not None
        assert package.manifest.model.gzip_bytes < package.manifest.model.bytes / 2

    def test_compression_is_optional(
        self,
        tmp_path: Path,
        classifier: Any,
        training_data: tuple[Any, Any],
    ) -> None:
        features, _ = training_data
        built = edge_pipeline(classifier, features, tmp_path / "raw", compress=False)
        assert built.gzip_path is None
        assert built.manifest.model.gzip_file is None

    def test_the_baseline_travels_with_the_model(self, package: Any) -> None:
        assert package.manifest.baseline_file == BASELINE_FILENAME
        assert package.manifest.baseline_samples == 400

    def test_the_baseline_is_optional(
        self,
        tmp_path: Path,
        classifier: Any,
        training_data: tuple[Any, Any],
    ) -> None:
        features, _ = training_data
        built = edge_pipeline(classifier, features, tmp_path / "nb", baseline=False)
        assert built.baseline_path is None
        assert built.manifest.baseline_file is None

    def test_it_reads_column_names_from_a_dataframe(
        self,
        tmp_path: Path,
        training_data: tuple[Any, Any],
    ) -> None:
        pandas = pytest.importorskip("pandas")
        from sklearn.linear_model import LogisticRegression

        features, target = training_data
        frame = pandas.DataFrame(features, columns=["a", "b", "c", "d", "e"])
        model = LogisticRegression(max_iter=500).fit(frame, target)

        built = edge_pipeline(model, frame, tmp_path / "df", labels=target)
        assert built.manifest.input.feature_names == ["a", "b", "c", "d", "e"]

    def test_a_regressor_records_no_classes(
        self,
        tmp_path: Path,
    ) -> None:
        from sklearn.datasets import make_regression
        from sklearn.linear_model import LinearRegression

        features, target = make_regression(n_samples=100, n_features=3, random_state=0)
        model = LinearRegression().fit(features, target)

        built = edge_pipeline(model, features, tmp_path / "reg")
        assert built.manifest.output.classes == []
        assert built.manifest.output.is_classifier is False

    def test_verification_can_be_skipped_deliberately(
        self,
        tmp_path: Path,
        classifier: Any,
        training_data: tuple[Any, Any],
    ) -> None:
        """Skipping is allowed; not knowing is reported as None, not as True."""
        features, _ = training_data
        built = edge_pipeline(
            classifier,
            features,
            tmp_path / "skip",
            verify_samples=False,
        )
        assert built.manifest.verified is None
        assert built.manifest.verification is None


class TestManifestReading:
    def test_it_reads_without_loading_the_model(self, package: Any) -> None:
        manifest = read_manifest(package.directory)
        assert manifest.version == package.manifest.version

    def test_a_missing_manifest_says_so(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match=MANIFEST_FILENAME):
            read_manifest(tmp_path)

    def test_the_manifest_is_plain_json(self, package: Any) -> None:
        """A browser parses this; it must not need a Python reader."""
        raw = json.loads(Path(package.manifest_path).read_text(encoding="utf-8"))
        assert raw["model"]["file"] == "risk.onnx"
        assert raw["input"]["features"] == 5
        assert raw["schema_version"] == MANIFEST_SCHEMA_VERSION

    def test_a_reader_tolerates_unknown_fields(self, package: Any) -> None:
        """Forward compatibility: a newer writer must not break an older reader."""
        raw = json.loads(Path(package.manifest_path).read_text(encoding="utf-8"))
        raw["something_new"] = {"added": "later"}
        assert EdgeManifest.model_validate(raw).name == "risk"


class TestLoading:
    def test_it_returns_a_predictor_that_matches_the_estimator(
        self,
        package: Any,
        classifier: Any,
        training_data: tuple[Any, Any],
    ) -> None:
        features, _ = training_data
        loaded = load_edge_package(package.directory)
        result = loaded.predictor.predict(features[:20])
        assert result.labels == classifier.predict(features[:20]).tolist()

    def test_it_wires_the_monitor_to_the_packaged_baseline(
        self,
        package: Any,
        training_data: tuple[Any, Any],
    ) -> None:
        features, _ = training_data
        loaded = load_edge_package(package.directory)
        assert loaded.monitor is not None
        assert loaded.baseline is not None

        loaded.monitor.observe(features[:200], loaded.predictor.predict(features[:200]))
        report = loaded.monitor.report()
        assert report.model_version == package.manifest.version
        assert report.drift.sufficient_sample is True

    def test_the_monitor_is_optional(self, package: Any) -> None:
        loaded = load_edge_package(package.directory, monitor=False)
        assert loaded.monitor is None

    def test_a_truncated_download_is_caught_before_it_serves(
        self,
        package: Any,
    ) -> None:
        """Half a model must fail as a digest mismatch, not as a parse error."""
        model = Path(package.model_path)
        model.write_bytes(model.read_bytes()[: len(model.read_bytes()) // 2])
        with pytest.raises(ValueError, match="does not match the manifest digest"):
            load_edge_package(package.directory)

    def test_the_digest_check_can_be_skipped(self, package: Any) -> None:
        loaded = load_edge_package(package.directory, verify_digest=False)
        assert loaded.manifest.name == "risk"

    def test_a_missing_model_file_says_so(self, package: Any) -> None:
        Path(package.model_path).unlink()
        with pytest.raises(FileNotFoundError, match="model file missing"):
            load_edge_package(package.directory)

    def test_threads_are_configurable_at_load(self, package: Any) -> None:
        loaded = load_edge_package(package.directory, intra_op_threads=2)
        assert loaded.predictor.info.intra_op_threads == 2


class TestVerificationGate:
    def test_a_broken_export_is_refused_rather_than_shipped(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The one failure this pipeline must never let through quietly.

        The converter defect this guards against is real (binary tree
        ensembles on skl2onnx 1.20), but it is version-dependent, so the
        gate is exercised by forcing the verification to fail rather than
        by depending on a specific converter release.
        """
        from sklearn.datasets import make_classification
        from sklearn.linear_model import LogisticRegression

        from tempest_fastapi_sdk.modelops import edge as edge_module
        from tempest_fastapi_sdk.modelops.sklearn import ExportVerification

        features, target = make_classification(
            n_samples=100,
            n_features=4,
            n_informative=4,
            n_redundant=0,
            n_repeated=0,
            random_state=0,
        )
        model = LogisticRegression(max_iter=500).fit(features, target)

        def failing_verify(*args: Any, **kwargs: Any) -> ExportVerification:
            return ExportVerification(
                passed=False,
                n_samples=100,
                mismatched=37,
                label_agreement=0.63,
                detail="forced failure",
            )

        monkeypatch.setattr(edge_module, "verify_sklearn_onnx", failing_verify)

        with pytest.raises(ValueError, match="does not reproduce"):
            edge_pipeline(model, features, tmp_path / "bad")
