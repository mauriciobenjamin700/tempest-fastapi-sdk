"""Tests for exporting scikit-learn models to ONNX for edge deployment."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk.modelops.sklearn import (
    DEFAULT_OPSET,
    TensorDtype,
    edge_bundle,
    export_sklearn_to_onnx,
    uses_ml_domain,
    verify_sklearn_onnx,
)

sklearn = pytest.importorskip("sklearn")
pytest.importorskip("skl2onnx")
pytest.importorskip("onnxruntime")


@pytest.fixture
def data() -> tuple[Any, Any]:
    """Return a small deterministic classification dataset."""
    from sklearn.datasets import make_classification

    return make_classification(
        n_samples=200,
        n_features=6,
        n_informative=4,
        n_classes=3,
        n_clusters_per_class=1,
        random_state=0,
    )


@pytest.fixture
def forest(data: tuple[Any, Any]) -> Any:
    """Return a fitted random forest."""
    from sklearn.ensemble import RandomForestClassifier

    features, target = data
    return RandomForestClassifier(n_estimators=10, random_state=0).fit(
        features,
        target,
    )


class TestExport:
    def test_exports_a_classifier(
        self,
        forest: Any,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        features, _target = data
        export = export_sklearn_to_onnx(
            forest,
            features[:10],
            tmp_path / "m.onnx",
        )
        assert Path(export.path).exists()
        assert export.size_bytes > 0
        assert export.n_features == 6
        assert export.estimator == "RandomForestClassifier"
        assert export.opset == DEFAULT_OPSET

    def test_zipmap_is_off_by_default(
        self,
        forest: Any,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        """The probability output must be a tensor, not a list of dicts."""
        import onnxruntime

        features, _target = data
        export = export_sklearn_to_onnx(forest, features[:10], tmp_path / "m.onnx")
        assert export.zipmap is False

        session = onnxruntime.InferenceSession(export.path)
        outputs = session.run(None, {export.input_name: features[:5].astype("float32")})
        assert not isinstance(outputs[1], list) or not isinstance(
            outputs[1][0],
            dict,
        )

    def test_zipmap_can_be_kept(
        self,
        forest: Any,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        features, _target = data
        export = export_sklearn_to_onnx(
            forest,
            features[:10],
            tmp_path / "m.onnx",
            zipmap=True,
        )
        assert export.zipmap is True

    def test_exports_a_regressor(
        self,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        from sklearn.linear_model import LinearRegression

        features, target = data
        model = LinearRegression().fit(features, target)
        export = export_sklearn_to_onnx(model, features[:10], tmp_path / "r.onnx")
        assert export.estimator == "LinearRegression"

    def test_exports_a_pipeline(
        self,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        features, target = data
        model = Pipeline(
            [("scale", StandardScaler()), ("clf", LogisticRegression(max_iter=500))],
        ).fit(features, target)
        export = export_sklearn_to_onnx(model, features[:10], tmp_path / "p.onnx")
        assert export.estimator == "Pipeline"
        assert export.n_features == 6

    def test_float64_is_available(
        self,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        from sklearn.linear_model import LinearRegression

        features, target = data
        model = LinearRegression().fit(features, target)
        export = export_sklearn_to_onnx(
            model,
            features[:10],
            tmp_path / "d.onnx",
            dtype=TensorDtype.FLOAT64,
        )
        assert export.dtype == TensorDtype.FLOAT64

    def test_the_output_directory_is_created(
        self,
        forest: Any,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        features, _target = data
        export = export_sklearn_to_onnx(
            forest,
            features[:10],
            tmp_path / "nested" / "deep" / "m.onnx",
        )
        assert Path(export.path).exists()

    def test_an_unfitted_estimator_fails_clearly(
        self,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        from sklearn.ensemble import RandomForestClassifier

        features, _target = data
        with pytest.raises(ValueError, match="could not convert"):
            export_sklearn_to_onnx(
                RandomForestClassifier(),
                features[:10],
                tmp_path / "m.onnx",
            )


class TestShapeGuard:
    def test_a_flat_sample_is_refused(
        self,
        forest: Any,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        features, _target = data
        with pytest.raises(ValueError, match="2-D"):
            export_sklearn_to_onnx(forest, features[0], tmp_path / "m.onnx")


class TestVerification:
    def test_a_faithful_export_passes(
        self,
        forest: Any,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        features, _target = data
        export = export_sklearn_to_onnx(forest, features[:10], tmp_path / "m.onnx")
        check = verify_sklearn_onnx(forest, export.path, features)
        assert check.passed is True
        assert check.label_agreement == 1.0
        assert check.mismatched == 0
        assert check.n_samples == len(features)

    def test_it_reports_the_probability_drift(
        self,
        forest: Any,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        """float32 export drifts from float64 sklearn; it must be visible."""
        features, _target = data
        export = export_sklearn_to_onnx(forest, features[:10], tmp_path / "m.onnx")
        check = verify_sklearn_onnx(forest, export.path, features)
        assert check.max_abs_diff is not None
        assert check.max_abs_diff < 1e-3

    def test_a_regressor_is_compared_numerically(
        self,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        from sklearn.linear_model import LinearRegression

        features, target = data
        model = LinearRegression().fit(features, target)
        export = export_sklearn_to_onnx(model, features[:10], tmp_path / "r.onnx")
        check = verify_sklearn_onnx(model, export.path, features)
        assert check.passed is True
        assert check.label_agreement is None
        assert check.max_abs_diff is not None

    def test_an_impossible_tolerance_fails(
        self,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        from sklearn.linear_model import LinearRegression

        features, target = data
        model = LinearRegression().fit(features, target)
        export = export_sklearn_to_onnx(model, features[:10], tmp_path / "r.onnx")
        check = verify_sklearn_onnx(
            model,
            export.path,
            features,
            tolerance=1e-12,
        )
        assert check.passed is False
        assert "tolerance" in check.detail


class TestKnownConverterDefect:
    """The binary tree-ensemble defect must be visible, not silent.

    skl2onnx 1.20 + scikit-learn 1.9 convert a binary tree ensemble to a
    graph whose probability output is a decision score in [-1, 1]. No
    converter option changes it, so the export flags it and verification
    catches it.
    """

    @pytest.fixture
    def binary_forest(self) -> tuple[Any, Any]:
        from sklearn.datasets import make_classification
        from sklearn.ensemble import RandomForestClassifier

        features, target = make_classification(
            n_samples=150,
            n_features=6,
            n_informative=4,
            n_classes=2,
            n_clusters_per_class=1,
            random_state=0,
        )
        model = RandomForestClassifier(n_estimators=10, random_state=0).fit(
            features,
            target,
        )
        return model, features

    def test_the_export_warns(
        self,
        binary_forest: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        model, features = binary_forest
        export = export_sklearn_to_onnx(model, features[:10], tmp_path / "b.onnx")
        assert export.warnings
        assert export.needs_verification is True
        assert "binary tree-ensemble" in export.warnings[0]

    def test_verification_catches_the_disagreement(
        self,
        binary_forest: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        model, features = binary_forest
        export = export_sklearn_to_onnx(model, features[:10], tmp_path / "b.onnx")
        check = verify_sklearn_onnx(model, export.path, features)
        assert check.passed is False
        assert check.mismatched > 0

    def test_a_multiclass_tree_is_not_warned_about(
        self,
        forest: Any,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        features, _target = data
        export = export_sklearn_to_onnx(forest, features[:10], tmp_path / "m.onnx")
        assert export.warnings == []

    def test_a_linear_binary_model_is_not_warned_about(
        self,
        binary_forest: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        from sklearn.linear_model import LogisticRegression

        _model, _features = binary_forest
        from sklearn.datasets import make_classification

        feats, target = make_classification(
            n_samples=150,
            n_features=6,
            n_classes=2,
            n_informative=4,
            n_clusters_per_class=1,
            random_state=0,
        )
        linear = LogisticRegression(max_iter=400).fit(feats, target)
        export = export_sklearn_to_onnx(linear, feats[:10], tmp_path / "l.onnx")
        assert export.warnings == []
        check = verify_sklearn_onnx(linear, export.path, feats)
        assert check.passed is True


class TestMlDomain:
    def test_a_forest_uses_the_ml_domain(
        self,
        forest: Any,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        features, _target = data
        export = export_sklearn_to_onnx(forest, features[:10], tmp_path / "m.onnx")
        assert uses_ml_domain(export.path) is True

    def test_a_missing_file_is_not_a_crash(self, tmp_path: Path) -> None:
        assert uses_ml_domain(tmp_path / "nope.onnx") is False


class TestEdgeBundle:
    def test_it_exports_and_verifies(
        self,
        forest: Any,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        features, _target = data
        bundle = edge_bundle(
            forest,
            features[:20],
            tmp_path,
            name="m",
            verify_samples=features,
            to_ort=False,
        )
        assert bundle.verification is not None
        assert bundle.verification.passed is True
        assert bundle.export.n_features == 6

    def test_quantisation_is_skipped_with_a_reason_for_tree_models(
        self,
        forest: Any,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        features, _target = data
        bundle = edge_bundle(
            forest,
            features[:20],
            tmp_path,
            name="m",
            to_ort=False,
        )
        quantise = next(s for s in bundle.stages if s.name == "quantize")
        assert quantise.skipped is True
        assert "ai.onnx.ml" in quantise.note

    def test_the_deployable_is_the_smallest_artifact_not_the_last(
        self,
        forest: Any,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        """Optimising a tiny sklearn graph often makes it bigger."""
        features, _target = data
        bundle = edge_bundle(
            forest,
            features[:20],
            tmp_path,
            name="m",
            to_ort=False,
        )
        produced = [
            stage.size_bytes
            for stage in bundle.stages
            if not stage.skipped and stage.size_bytes
        ]
        deployable_size = Path(bundle.deployable).stat().st_size
        assert deployable_size == min(produced)

    def test_size_reduction_never_lies_about_growth(
        self,
        forest: Any,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        features, _target = data
        bundle = edge_bundle(
            forest,
            features[:20],
            tmp_path,
            name="m",
            to_ort=False,
        )
        assert bundle.size_reduction <= 1.0

    def test_verification_can_be_skipped(
        self,
        forest: Any,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        features, _target = data
        bundle = edge_bundle(forest, features[:20], tmp_path, name="m", to_ort=False)
        assert bundle.verification is None

    def test_stages_can_be_turned_off(
        self,
        forest: Any,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        features, _target = data
        bundle = edge_bundle(
            forest,
            features[:20],
            tmp_path,
            name="m",
            optimize=False,
            quantize=False,
            to_ort=False,
        )
        assert [stage.name for stage in bundle.stages] == ["export"]
        assert bundle.deployable == bundle.export.path

    def test_quantisation_is_skipped_for_sklearn_classifiers_generally(
        self,
        data: tuple[Any, Any],
        tmp_path: Path,
    ) -> None:
        """Not just trees: sklearn classifiers land in ai.onnx.ml broadly.

        Even an MLP, whose hidden layers are real weight tensors, declares
        the ml domain for its output layer. Measured separately, quantising
        one of these produced a *larger* file than the plain export — these
        graphs are kilobytes, and the scale/zero-point nodes cost more than
        the int8 weights save. Skipping is the right outcome, and saying so
        beats letting the quantiser fail with an opaque message.
        """
        from sklearn.neural_network import MLPClassifier

        features, target = data
        model = MLPClassifier(
            hidden_layer_sizes=(32,),
            max_iter=30,
            random_state=0,
        ).fit(features, target)
        bundle = edge_bundle(
            model,
            features[:20],
            tmp_path,
            name="mlp",
            to_ort=False,
        )
        quantise = next(s for s in bundle.stages if s.name == "quantize")
        assert quantise.skipped is True
        assert bundle.deployable == bundle.export.path


class TestMissingExtra:
    def test_the_import_error_names_the_extra(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import builtins

        from tempest_fastapi_sdk.modelops import sklearn as module

        real_import = builtins.__import__

        def fail(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "skl2onnx":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fail)
        with pytest.raises(ImportError, match=r"\[modelops-sklearn\]"):
            module._require_skl2onnx()
