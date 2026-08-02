"""Tests for the runtime-free compact format.

Every case runs a real fitted estimator through the writer and back through
the reference reader, comparing against scikit-learn's own predictions. That
comparison *is* the format's guarantee: nothing else validates a
reimplementation of somebody else's arithmetic.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk.modelops.compact import (
    COMPACT_MAGIC,
    COMPACT_SCHEMA_VERSION,
    UnsupportedEstimatorError,
    export_sklearn_to_compact,
    predict_compact,
    read_compact,
)

pytest.importorskip("sklearn")
numpy = pytest.importorskip("numpy")


@pytest.fixture
def multiclass() -> tuple[Any, Any]:
    """A 3-class dataset."""
    from sklearn.datasets import make_classification

    return make_classification(
        n_samples=800,
        n_features=5,
        n_informative=4,
        n_redundant=0,
        n_repeated=0,
        n_classes=3,
        n_clusters_per_class=1,
        random_state=0,
    )


@pytest.fixture
def binary() -> tuple[Any, Any]:
    """A 2-class dataset."""
    from sklearn.datasets import make_classification

    return make_classification(
        n_samples=500,
        n_features=4,
        n_informative=3,
        n_redundant=0,
        n_repeated=0,
        n_classes=2,
        random_state=1,
    )


@pytest.fixture
def regression() -> tuple[Any, Any]:
    """A regression dataset."""
    from sklearn.datasets import make_regression

    return make_regression(n_samples=400, n_features=4, random_state=2)


def _agrees(model: Any, path: Path, rows: Any) -> tuple[bool, float]:
    """Compare the compact file against the estimator on the given rows.

    Args:
        model (Any): The fitted estimator.
        path (Path): The written compact file.
        rows (Any): Rows to compare on.

    Returns:
        tuple[bool, float]: Whether labels matched, and the largest score
        difference.
    """
    labels, probabilities = predict_compact(path, rows)
    expected = [str(value) for value in model.predict(rows)]
    matched = [str(value) for value in labels] == expected
    if probabilities:
        reference = numpy.asarray(model.predict_proba(rows))
        return matched, float(numpy.abs(reference - numpy.asarray(probabilities)).max())
    return matched, 0.0


class TestLinearModels:
    def test_multiclass_logistic_regression(
        self,
        tmp_path: Path,
        multiclass: tuple[Any, Any],
    ) -> None:
        from sklearn.linear_model import LogisticRegression

        features, target = multiclass
        model = LogisticRegression(max_iter=600).fit(features, target)
        path = tmp_path / "m.tmc"
        export = export_sklearn_to_compact(model, features[:200], path)

        assert export.kind == "linear"
        assert export.verified is True
        matched, difference = _agrees(model, path, features[:100])
        assert matched
        assert difference < 1e-5

    def test_binary_logistic_regression(
        self,
        tmp_path: Path,
        binary: tuple[Any, Any],
    ) -> None:
        """Binary logistic has one score column, mirrored into two."""
        from sklearn.linear_model import LogisticRegression

        features, target = binary
        model = LogisticRegression(max_iter=600).fit(features, target)
        path = tmp_path / "b.tmc"
        export_sklearn_to_compact(model, features[:200], path)

        matched, difference = _agrees(model, path, features[:100])
        assert matched
        assert difference < 1e-5

    def test_linear_regression(
        self,
        tmp_path: Path,
        regression: tuple[Any, Any],
    ) -> None:
        from sklearn.linear_model import LinearRegression

        features, target = regression
        model = LinearRegression().fit(features, target)
        path = tmp_path / "r.tmc"
        export = export_sklearn_to_compact(model, features[:200], path)

        assert export.task == "regression"
        labels, probabilities = predict_compact(path, features[:50])
        assert probabilities == []
        assert numpy.allclose(labels, model.predict(features[:50]), atol=1e-3)


class TestTreeModels:
    def test_decision_tree(self, tmp_path: Path, multiclass: tuple[Any, Any]) -> None:
        from sklearn.tree import DecisionTreeClassifier

        features, target = multiclass
        model = DecisionTreeClassifier(max_depth=6, random_state=0).fit(
            features,
            target,
        )
        path = tmp_path / "t.tmc"
        export = export_sklearn_to_compact(model, features[:200], path)

        assert export.kind == "tree_ensemble"
        assert export.n_trees == 1
        matched, difference = _agrees(model, path, features[:100])
        assert matched
        assert difference < 1e-6

    def test_random_forest(self, tmp_path: Path, multiclass: tuple[Any, Any]) -> None:
        """A forest averages per-tree distributions, not raw leaf counts."""
        from sklearn.ensemble import RandomForestClassifier

        features, target = multiclass
        model = RandomForestClassifier(
            n_estimators=15,
            max_depth=5,
            random_state=0,
        ).fit(features, target)
        path = tmp_path / "f.tmc"
        export = export_sklearn_to_compact(model, features[:200], path)

        assert export.n_trees == 15
        matched, difference = _agrees(model, path, features[:100])
        assert matched
        assert difference < 1e-6

    def test_extra_trees(self, tmp_path: Path, multiclass: tuple[Any, Any]) -> None:
        from sklearn.ensemble import ExtraTreesClassifier

        features, target = multiclass
        model = ExtraTreesClassifier(
            n_estimators=10,
            max_depth=6,
            random_state=0,
        ).fit(features, target)
        path = tmp_path / "e.tmc"
        export_sklearn_to_compact(model, features[:200], path)

        matched, _ = _agrees(model, path, features[:100])
        assert matched

    def test_forest_regressor(
        self,
        tmp_path: Path,
        regression: tuple[Any, Any],
    ) -> None:
        from sklearn.ensemble import RandomForestRegressor

        features, target = regression
        model = RandomForestRegressor(
            n_estimators=8,
            max_depth=5,
            random_state=0,
        ).fit(features, target)
        path = tmp_path / "fr.tmc"
        export_sklearn_to_compact(model, features[:200], path)

        labels, _ = predict_compact(path, features[:50])
        assert numpy.allclose(labels, model.predict(features[:50]), atol=1e-4)


class TestRoutingRule:
    def test_a_value_sitting_on_a_threshold_goes_where_sklearn_sends_it(
        self,
        tmp_path: Path,
    ) -> None:
        """The rule the iris forest exposed.

        `sklearn.tree` casts its input to float32 before traversing, so a
        threshold of 5.099999904632568 — a float32 value widened for
        storage — and an input of 5.1 compare *equal* and go left.
        Comparing in float64 sends that row right, changing one tree's vote
        out of twenty: a probability off by exactly 0.05.
        """
        from sklearn.datasets import load_iris
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import train_test_split

        iris = load_iris()
        features_train, _, target_train, _ = train_test_split(
            iris.data,
            iris.target,
            test_size=0.3,
            random_state=0,
        )
        model = RandomForestClassifier(
            n_estimators=20,
            max_depth=4,
            random_state=0,
        ).fit(features_train, target_train)

        path = tmp_path / "iris.tmc"
        export_sklearn_to_compact(model, features_train, path)

        matched, difference = _agrees(model, path, features_train)
        assert matched
        assert difference < 1e-6

    def test_the_boundary_rows_exist_in_this_dataset(self) -> None:
        """Guard for the guard: a fixture that never hits a boundary proves
        nothing, so this asserts the case is really there."""
        from sklearn.datasets import load_iris
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import train_test_split

        iris = load_iris()
        features_train, _, target_train, _ = train_test_split(
            iris.data,
            iris.target,
            test_size=0.3,
            random_state=0,
        )
        model = RandomForestClassifier(
            n_estimators=20,
            max_depth=4,
            random_state=0,
        ).fit(features_train, target_train)

        def leaf(tree: Any, row: Any, *, cast: bool) -> int:
            structure = tree.tree_
            node = 0
            while structure.children_left[node] != -1:
                column = structure.feature[node]
                value = numpy.float32(row[column]) if cast else float(row[column])
                node = (
                    structure.children_left[node]
                    if value <= structure.threshold[node]
                    else structure.children_right[node]
                )
            return int(node)

        diverging = sum(
            1
            for row in features_train
            if any(
                leaf(tree, row, cast=True) != leaf(tree, row, cast=False)
                for tree in model.estimators_
            )
        )
        assert diverging > 0


class TestPipelines:
    def test_standard_scaler_is_folded_in(
        self,
        tmp_path: Path,
        multiclass: tuple[Any, Any],
    ) -> None:
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        features, target = multiclass
        model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=600)).fit(
            features,
            target,
        )
        path = tmp_path / "p.tmc"
        export_sklearn_to_compact(model, features[:200], path)

        matched, difference = _agrees(model, path, features[:100])
        assert matched
        assert difference < 1e-5

    def test_minmax_scaler_is_folded_in(
        self,
        tmp_path: Path,
        multiclass: tuple[Any, Any],
    ) -> None:
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import MinMaxScaler

        features, target = multiclass
        model = make_pipeline(MinMaxScaler(), LogisticRegression(max_iter=600)).fit(
            features,
            target,
        )
        path = tmp_path / "mm.tmc"
        export_sklearn_to_compact(model, features[:200], path)

        matched, _ = _agrees(model, path, features[:100])
        assert matched

    def test_a_step_that_cannot_be_folded_is_refused(
        self,
        tmp_path: Path,
        multiclass: tuple[Any, Any],
    ) -> None:
        """Ignoring a transform would predict on the wrong numbers, silently."""
        from sklearn.decomposition import PCA
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline

        features, target = multiclass
        model = make_pipeline(
            PCA(n_components=3),
            LogisticRegression(max_iter=600),
        ).fit(features, target)
        with pytest.raises(UnsupportedEstimatorError, match="PCA"):
            export_sklearn_to_compact(model, features[:50], tmp_path / "pca.tmc")


class TestRefusals:
    def test_an_unsupported_estimator_names_the_onnx_route(
        self,
        tmp_path: Path,
        multiclass: tuple[Any, Any],
    ) -> None:
        from sklearn.ensemble import HistGradientBoostingClassifier

        features, target = multiclass
        model = HistGradientBoostingClassifier(max_iter=20, random_state=0).fit(
            features,
            target,
        )
        with pytest.raises(UnsupportedEstimatorError, match="export_sklearn_to_onnx"):
            export_sklearn_to_compact(model, features[:50], tmp_path / "hgb.tmc")

    def test_a_disagreeing_export_is_refused(
        self,
        tmp_path: Path,
        multiclass: tuple[Any, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The gate that makes the format trustworthy at all."""
        from sklearn.linear_model import LogisticRegression

        from tempest_fastapi_sdk.modelops import compact as module

        features, target = multiclass
        model = LogisticRegression(max_iter=600).fit(features, target)

        monkeypatch.setattr(module, "_verify", lambda *args, **kwargs: (False, 0.5))
        with pytest.raises(ValueError, match="does not reproduce"):
            export_sklearn_to_compact(model, features[:50], tmp_path / "bad.tmc")

    def test_verification_can_be_skipped_deliberately(
        self,
        tmp_path: Path,
        multiclass: tuple[Any, Any],
    ) -> None:
        from sklearn.linear_model import LogisticRegression

        features, target = multiclass
        model = LogisticRegression(max_iter=600).fit(features, target)
        export = export_sklearn_to_compact(
            model,
            False,
            tmp_path / "skip.tmc",
        )
        assert export.verified is None


class TestFileLayout:
    def test_it_starts_with_the_magic(
        self,
        tmp_path: Path,
        multiclass: tuple[Any, Any],
    ) -> None:
        from sklearn.linear_model import LogisticRegression

        features, target = multiclass
        model = LogisticRegression(max_iter=600).fit(features, target)
        path = tmp_path / "m.tmc"
        export_sklearn_to_compact(model, features[:50], path)

        assert path.read_bytes()[:4] == COMPACT_MAGIC

    def test_sections_start_aligned_for_a_browser(
        self,
        tmp_path: Path,
        multiclass: tuple[Any, Any],
    ) -> None:
        """A JavaScript Float32Array cannot view an unaligned offset."""
        from sklearn.ensemble import RandomForestClassifier

        features, target = multiclass
        model = RandomForestClassifier(
            n_estimators=5,
            max_depth=4,
            random_state=0,
        ).fit(features, target)
        path = tmp_path / "f.tmc"
        export_sklearn_to_compact(model, features[:50], path)

        raw = path.read_bytes()
        (header_length,) = struct.unpack("<I", raw[4:8])
        assert (8 + header_length) % 8 == 0

    def test_the_header_is_plain_json(
        self,
        tmp_path: Path,
        multiclass: tuple[Any, Any],
    ) -> None:
        """Another language parses this; it must not need a Python reader."""
        from sklearn.linear_model import LogisticRegression

        features, target = multiclass
        model = LogisticRegression(max_iter=600).fit(features, target)
        path = tmp_path / "m.tmc"
        export_sklearn_to_compact(model, features[:50], path)

        raw = path.read_bytes()
        (header_length,) = struct.unpack("<I", raw[4:8])
        header = json.loads(raw[8 : 8 + header_length].decode("utf-8"))
        assert header["schema_version"] == COMPACT_SCHEMA_VERSION
        assert header["kind"] == "linear"
        assert header["class_type"] == "int"

    def test_reading_back_gives_the_arrays(
        self,
        tmp_path: Path,
        multiclass: tuple[Any, Any],
    ) -> None:
        from sklearn.ensemble import RandomForestClassifier

        features, target = multiclass
        model = RandomForestClassifier(
            n_estimators=4,
            max_depth=4,
            random_state=0,
        ).fit(features, target)
        path = tmp_path / "f.tmc"
        export_sklearn_to_compact(model, features[:50], path)

        header, arrays = read_compact(path)
        assert header["n_trees"] == 4
        assert len(arrays["tree_offset"]) == 5
        assert arrays["node_feature"].dtype == numpy.int32
        assert arrays["node_threshold"].dtype == numpy.float32

    def test_a_foreign_file_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "not.tmc"
        path.write_bytes(b"ONNX and other bytes")
        with pytest.raises(ValueError, match="not a compact model"):
            read_compact(path)

    def test_it_records_the_column_order(
        self,
        tmp_path: Path,
        multiclass: tuple[Any, Any],
    ) -> None:
        from sklearn.linear_model import LogisticRegression

        features, target = multiclass
        model = LogisticRegression(max_iter=600).fit(features, target)
        path = tmp_path / "m.tmc"
        export_sklearn_to_compact(
            model,
            features[:50],
            path,
            feature_names=["a", "b", "c", "d", "e"],
        )

        header, _ = read_compact(path)
        assert header["feature_names"] == ["a", "b", "c", "d", "e"]


class TestSize:
    def test_it_is_smaller_than_the_onnx_of_the_same_forest(
        self,
        tmp_path: Path,
        multiclass: tuple[Any, Any],
    ) -> None:
        """Not the point of the format, but worth knowing it does not cost."""
        pytest.importorskip("skl2onnx")
        from sklearn.ensemble import RandomForestClassifier

        from tempest_fastapi_sdk.modelops.sklearn import export_sklearn_to_onnx

        features, target = multiclass
        model = RandomForestClassifier(
            n_estimators=20,
            max_depth=6,
            random_state=0,
        ).fit(features, target)

        compact = export_sklearn_to_compact(model, features[:50], tmp_path / "f.tmc")
        onnx = export_sklearn_to_onnx(model, features[:10], tmp_path / "f.onnx")
        assert compact.size_bytes < onnx.size_bytes
