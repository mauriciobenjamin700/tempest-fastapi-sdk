"""Tests for the HuggingFace weight-lifecycle module.

Every test drives a fake ``huggingface_hub`` injected over
:func:`tempest_fastapi_sdk.genai.hub._require_hub`, so the suite never
reaches the network and runs identically on a host with no extra
installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk.genai import (
    ModelRef,
    cache_size_bytes,
    download_model,
    list_cached_models,
    model_disk_bytes,
    remove_cached_model,
    resolve_revision,
)
from tempest_fastapi_sdk.genai import hub as hub_module


class FakeModelInfo:
    def __init__(
        self,
        sha: str | None = "abc123",
        siblings: list[Any] | None = None,
    ) -> None:
        self.sha = sha
        self.siblings = siblings or []


class FakeSibling:
    def __init__(self, size: int | None) -> None:
        self.size = size


class FakeHfApi:
    def __init__(
        self,
        info: FakeModelInfo | None = None,
        error: Exception | None = None,
    ) -> None:
        self._info = info or FakeModelInfo()
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def model_info(self, repo_id: str, **kwargs: Any) -> FakeModelInfo:
        self.calls.append({"repo_id": repo_id, **kwargs})
        if self._error is not None:
            raise self._error
        return self._info


class FakeRevision:
    def __init__(
        self,
        commit_hash: str,
        size_on_disk: int,
        refs: set[str] | None = None,
        snapshot_path: str = "/cache/snap",
        last_modified: float | None = 1_700_000_000.0,
    ) -> None:
        self.commit_hash = commit_hash
        self.size_on_disk = size_on_disk
        self.refs = refs or set()
        self.snapshot_path = snapshot_path
        self.last_modified = last_modified


class FakeRepo:
    def __init__(
        self,
        repo_id: str,
        size_on_disk: int,
        revisions: list[FakeRevision],
        repo_type: str = "model",
        repo_path: str = "/cache/repo",
    ) -> None:
        self.repo_id = repo_id
        self.size_on_disk = size_on_disk
        self.revisions = revisions
        self.repo_type = repo_type
        self.repo_path = repo_path


class FakeStrategy:
    def __init__(self, expected_freed_size: int) -> None:
        self.expected_freed_size = expected_freed_size
        self.executed = False

    def execute(self) -> None:
        self.executed = True


class FakeCacheInfo:
    def __init__(self, repos: list[FakeRepo]) -> None:
        self.repos = repos
        self.deleted: list[str] = []
        self.strategy = FakeStrategy(0)

    def delete_revisions(self, *hashes: str) -> FakeStrategy:
        self.deleted = list(hashes)
        freed = sum(
            revision.size_on_disk
            for repo in self.repos
            for revision in repo.revisions
            if revision.commit_hash in hashes
        )
        self.strategy = FakeStrategy(freed)
        return self.strategy


class FakeConstants:
    HF_HUB_CACHE = "/tmp/does-not-exist-hub-cache"


class FakeHub:
    def __init__(
        self,
        api: FakeHfApi | None = None,
        cache: FakeCacheInfo | None = None,
        download_path: str = "/cache/snap",
        scan_error: Exception | None = None,
    ) -> None:
        self._api = api or FakeHfApi()
        self._cache = cache
        self._download_path = download_path
        self._scan_error = scan_error
        self.constants = FakeConstants()
        self.download_calls: list[dict[str, Any]] = []

    def HfApi(self) -> FakeHfApi:  # noqa: N802
        return self._api

    def snapshot_download(self, repo_id: str, **kwargs: Any) -> str:
        self.download_calls.append({"repo_id": repo_id, **kwargs})
        return self._download_path

    def scan_cache_dir(self, cache_dir: str | None = None) -> FakeCacheInfo:
        if self._scan_error is not None:
            raise self._scan_error
        if self._cache is None:
            raise RuntimeError("cache not configured")
        return self._cache


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakeHub) -> FakeHub:
    monkeypatch.setattr(hub_module, "_require_hub", lambda: fake)
    return fake


class TestModelRef:
    def test_defaults_emit_no_kwargs(self) -> None:
        assert ModelRef(model_id="org/name").loader_kwargs() == {}

    def test_only_non_defaults_are_emitted(self) -> None:
        ref = ModelRef(
            model_id="org/name",
            revision="sha1",
            cache_dir="/models",
            token="hf_x",
            local_files_only=True,
            trust_remote_code=True,
        )
        assert ref.loader_kwargs() == {
            "cache_dir": "/models",
            "token": "hf_x",
            "revision": "sha1",
            "local_files_only": True,
            "trust_remote_code": True,
        }

    def test_download_kwargs_drop_trust_remote_code(self) -> None:
        ref = ModelRef(
            model_id="org/name",
            revision="sha1",
            trust_remote_code=True,
        )
        assert ref.download_kwargs() == {"revision": "sha1"}

    def test_loader_kwargs_are_not_shared_between_calls(self) -> None:
        ref = ModelRef(model_id="org/name", revision="sha1")
        first = ref.loader_kwargs()
        first["revision"] = "mutated"
        assert ref.loader_kwargs()["revision"] == "sha1"


class TestResolveRevision:
    def test_returns_the_commit_sha(self, monkeypatch: pytest.MonkeyPatch) -> None:
        api = FakeHfApi(FakeModelInfo(sha="deadbeef"))
        _install(monkeypatch, FakeHub(api=api))
        assert resolve_revision("org/name", revision="main") == "deadbeef"
        assert api.calls[0]["revision"] == "main"

    def test_unreachable_hub_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, FakeHub(api=FakeHfApi(error=OSError("offline"))))
        assert resolve_revision("org/name") is None

    def test_missing_sha_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch, FakeHub(api=FakeHfApi(FakeModelInfo(sha=None))))
        assert resolve_revision("org/name") is None


class TestModelDiskBytes:
    def test_sums_sibling_sizes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        info = FakeModelInfo(siblings=[FakeSibling(100), FakeSibling(250)])
        api = FakeHfApi(info)
        _install(monkeypatch, FakeHub(api=api))
        assert model_disk_bytes("org/name") == 350
        assert api.calls[0]["files_metadata"] is True

    def test_ignores_siblings_without_a_size(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        info = FakeModelInfo(siblings=[FakeSibling(None), FakeSibling(40)])
        _install(monkeypatch, FakeHub(api=FakeHfApi(info)))
        assert model_disk_bytes("org/name") == 40

    def test_no_sizes_at_all_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        info = FakeModelInfo(siblings=[FakeSibling(None)])
        _install(monkeypatch, FakeHub(api=FakeHfApi(info)))
        assert model_disk_bytes("org/name") is None

    def test_unreachable_hub_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, FakeHub(api=FakeHfApi(error=OSError("offline"))))
        assert model_disk_bytes("org/name") is None


class TestDownloadModel:
    def test_forwards_the_ref_and_measures_the_snapshot(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "config.json").write_bytes(b"x" * 10)
        (tmp_path / "weights.safetensors").write_bytes(b"y" * 90)
        fake = _install(
            monkeypatch,
            FakeHub(
                api=FakeHfApi(FakeModelInfo(siblings=[FakeSibling(100)])),
                download_path=str(tmp_path),
            ),
        )
        snapshot = download_model(
            "org/name",
            revision="sha1",
            cache_dir=str(tmp_path),
            token="hf_x",
            allow_patterns=["*.safetensors"],
            check_disk=False,
        )
        assert snapshot.size_bytes == 100
        assert snapshot.file_count == 2
        assert snapshot.path == str(tmp_path)
        call = fake.download_calls[0]
        assert call["revision"] == "sha1"
        assert call["token"] == "hf_x"
        assert call["allow_patterns"] == ["*.safetensors"]
        assert "trust_remote_code" not in call

    def test_refuses_to_start_when_the_disk_is_too_small(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _install(
            monkeypatch,
            FakeHub(
                api=FakeHfApi(FakeModelInfo(siblings=[FakeSibling(10**12)])),
                download_path=str(tmp_path),
            ),
        )
        with pytest.raises(OSError, match="GB are free"):
            download_model("org/name", cache_dir=str(tmp_path))

    def test_offline_resolution_skips_the_disk_check(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        fake = _install(
            monkeypatch,
            FakeHub(
                api=FakeHfApi(FakeModelInfo(siblings=[FakeSibling(10**12)])),
                download_path=str(tmp_path),
            ),
        )
        snapshot = download_model(
            "org/name",
            cache_dir=str(tmp_path),
            local_files_only=True,
        )
        assert snapshot.file_count == 0
        assert fake.download_calls[0]["local_files_only"] is True

    def test_unknown_size_does_not_block_the_download(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _install(
            monkeypatch,
            FakeHub(
                api=FakeHfApi(error=OSError("offline")),
                download_path=str(tmp_path),
            ),
        )
        snapshot = download_model("org/name", cache_dir=str(tmp_path))
        assert snapshot.model_id == "org/name"


class TestCacheListing:
    def _cache(self) -> FakeCacheInfo:
        return FakeCacheInfo(
            [
                FakeRepo(
                    "org/small",
                    100,
                    [FakeRevision("aaa", 100, {"main"})],
                ),
                FakeRepo(
                    "org/big",
                    900,
                    [
                        FakeRevision("bbb", 600, {"main"}),
                        FakeRevision("ccc", 300, set()),
                    ],
                ),
                FakeRepo(
                    "org/dataset",
                    5000,
                    [FakeRevision("ddd", 5000)],
                    repo_type="dataset",
                ),
            ]
        )

    def test_lists_models_largest_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch, FakeHub(cache=self._cache()))
        models = list_cached_models()
        assert [model.model_id for model in models] == ["org/big", "org/small"]
        assert [revision.revision for revision in models[0].revisions] == [
            "bbb",
            "ccc",
        ]

    def test_datasets_are_not_models(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch, FakeHub(cache=self._cache()))
        assert all(model.model_id != "org/dataset" for model in list_cached_models())

    def test_missing_cache_is_an_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, FakeHub(scan_error=OSError("no cache")))
        assert list_cached_models() == []

    def test_total_size_sums_the_models_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, FakeHub(cache=self._cache()))
        assert cache_size_bytes() == 1000


class TestRemoveCachedModel:
    def _fake(self) -> FakeHub:
        cache = FakeCacheInfo(
            [
                FakeRepo(
                    "org/big",
                    900,
                    [
                        FakeRevision("bbb", 600, {"main"}),
                        FakeRevision("ccc", 300, set()),
                    ],
                ),
            ]
        )
        return FakeHub(cache=cache)

    def test_removes_every_revision_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _install(monkeypatch, self._fake())
        assert remove_cached_model("org/big") == 900
        assert fake._cache is not None
        assert sorted(fake._cache.deleted) == ["bbb", "ccc"]
        assert fake._cache.strategy.executed is True

    def test_removes_one_revision_by_sha(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _install(monkeypatch, self._fake())
        assert remove_cached_model("org/big", revision="ccc") == 300
        assert fake._cache is not None
        assert fake._cache.deleted == ["ccc"]

    def test_removes_one_revision_by_ref_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, self._fake())
        assert remove_cached_model("org/big", revision="main") == 600

    def test_dry_run_reports_without_deleting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _install(monkeypatch, self._fake())
        assert remove_cached_model("org/big", dry_run=True) == 900
        assert fake._cache is not None
        assert fake._cache.strategy.executed is False

    def test_unknown_model_frees_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch, self._fake())
        assert remove_cached_model("org/absent") == 0

    def test_unknown_revision_frees_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, self._fake())
        assert remove_cached_model("org/big", revision="zzz") == 0

    def test_missing_cache_frees_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch, FakeHub(scan_error=OSError("no cache")))
        assert remove_cached_model("org/big") == 0


class FakeAuto:
    def __init__(self, recorder: list[dict[str, Any]]) -> None:
        self._recorder = recorder

    def from_pretrained(self, model_id: str, **kwargs: Any) -> Any:
        self._recorder.append({"model_id": model_id, **kwargs})
        return FakeTorchModule()


class FakeTorchModule:
    def to(self, device: str) -> FakeTorchModule:
        return self

    def eval(self) -> FakeTorchModule:
        return self


class FakeTransformers:
    def __init__(self, recorder: list[dict[str, Any]]) -> None:
        self.AutoTokenizer = FakeAuto(recorder)
        self.AutoModel = FakeAuto(recorder)
        self.AutoModelForCausalLM = FakeAuto(recorder)
        self.AutoModelForSequenceClassification = FakeAuto(recorder)
        self.AutoProcessor = FakeAuto(recorder)
        self.AutoModelForImageTextToText = FakeAuto(recorder)


class FakeTorch:
    float32 = "float32"
    float16 = "float16"
    bfloat16 = "bfloat16"


def _patch_transformers(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
) -> list[dict[str, Any]]:
    """Replace a module's ``_require_transformers`` with a recording fake.

    Args:
        monkeypatch (pytest.MonkeyPatch): The active patcher.
        module (Any): The module whose loader helper to replace.

    Returns:
        list[dict[str, Any]]: Every ``from_pretrained`` call, in order.
    """
    recorder: list[dict[str, Any]] = []
    fake = FakeTransformers(recorder)
    monkeypatch.setattr(
        module,
        "_require_transformers",
        lambda: (FakeTorch(), fake),
    )
    return recorder


_PIN: dict[str, Any] = {
    "revision": "sha1",
    "local_files_only": True,
    "trust_remote_code": True,
}

_EXPECTED: dict[str, Any] = {
    "revision": "sha1",
    "local_files_only": True,
    "trust_remote_code": True,
    "cache_dir": "/models",
    "token": "hf_x",
}


class TestLoadersForwardTheRef:
    def test_text_generator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tempest_fastapi_sdk.genai import TextGenerator, text

        recorder = _patch_transformers(monkeypatch, text)
        generator = TextGenerator(
            "org/name",
            device="cpu",
            cache_dir="/models",
            hf_token="hf_x",
            **_PIN,
        )
        generator.load()
        assert len(recorder) == 2
        for call in recorder:
            assert {key: call[key] for key in _EXPECTED} == _EXPECTED

    def test_embedder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tempest_fastapi_sdk.genai import Embedder, text

        recorder = _patch_transformers(monkeypatch, text)
        embedder = Embedder(
            "org/name",
            device="cpu",
            cache_dir="/models",
            hf_token="hf_x",
            **_PIN,
        )
        embedder.load()
        assert len(recorder) == 2
        for call in recorder:
            assert {key: call[key] for key in _EXPECTED} == _EXPECTED

    def test_reranker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tempest_fastapi_sdk.genai.rag import Reranker
        from tempest_fastapi_sdk.genai.rag import rerank as rerank_module

        recorder = _patch_transformers(monkeypatch, rerank_module)
        reranker = Reranker(
            "org/name",
            device="cpu",
            cache_dir="/models",
            hf_token="hf_x",
            **_PIN,
        )
        reranker.load()
        assert len(recorder) == 2
        for call in recorder:
            assert {key: call[key] for key in _EXPECTED} == _EXPECTED

    def test_classifier_moderator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tempest_fastapi_sdk.genai import ClassifierModerator
        from tempest_fastapi_sdk.genai import moderation as moderation_module

        recorder = _patch_transformers(monkeypatch, moderation_module)
        moderator = ClassifierModerator(
            "org/name",
            device="cpu",
            cache_dir="/models",
            hf_token="hf_x",
            **_PIN,
        )
        moderator.load()
        assert len(recorder) == 2
        for call in recorder:
            assert {key: call[key] for key in _EXPECTED} == _EXPECTED

    def test_vision_text_generator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tempest_fastapi_sdk.genai import VisionTextGenerator
        from tempest_fastapi_sdk.genai import vision_text as vision_module

        recorder = _patch_transformers(monkeypatch, vision_module)
        generator = VisionTextGenerator(
            "org/name",
            device="cpu",
            cache_dir="/models",
            hf_token="hf_x",
            **_PIN,
        )
        generator.load()
        assert len(recorder) == 2
        for call in recorder:
            assert {key: call[key] for key in _EXPECTED} == _EXPECTED

    def test_unpinned_loaders_send_nothing_extra(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tempest_fastapi_sdk.genai import TextGenerator, text

        recorder = _patch_transformers(monkeypatch, text)
        TextGenerator("org/name", device="cpu").load()
        for call in recorder:
            assert set(call) <= {"model_id", "torch_dtype", "device_map"}


class TestSpeechToTextMapsWhisperNames:
    def test_forwards_under_faster_whisper_names(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tempest_fastapi_sdk.genai.audio import SpeechToText
        from tempest_fastapi_sdk.genai.audio import stt as stt_module

        calls: list[dict[str, Any]] = []

        class FakeWhisper:
            def WhisperModel(self, size: str, **kwargs: Any) -> Any:  # noqa: N802
                calls.append({"size": size, **kwargs})
                return object()

        monkeypatch.setattr(
            stt_module,
            "_require_faster_whisper",
            lambda: FakeWhisper(),
        )
        SpeechToText(
            "small",
            device="cpu",
            cache_dir="/models",
            revision="sha1",
            local_files_only=True,
            hf_token="hf_x",
        ).load()
        assert calls[0]["download_root"] == "/models"
        assert calls[0]["revision"] == "sha1"
        assert calls[0]["local_files_only"] is True
        assert calls[0]["use_auth_token"] == "hf_x"


class TestOnnxEmbedderPinsTheTokenizer:
    def test_hub_tokenizer_gets_revision_and_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tempest_fastapi_sdk.genai import OnnxEmbedder
        from tempest_fastapi_sdk.genai import onnx_embed as onnx_module

        calls: list[dict[str, Any]] = []

        class FakeTokenizer:
            def enable_truncation(self, max_length: int) -> None:
                return None

            def enable_padding(self) -> None:
                return None

        class FakeTokenizerCls:
            def from_pretrained(self, ref: str, **kwargs: Any) -> FakeTokenizer:
                calls.append({"ref": ref, **kwargs})
                return FakeTokenizer()

            def from_file(self, ref: str) -> FakeTokenizer:
                calls.append({"ref": ref, "from_file": True})
                return FakeTokenizer()

        class FakeOrt:
            def InferenceSession(self, path: str, **kwargs: Any) -> Any:  # noqa: N802
                return object()

        monkeypatch.setattr(
            onnx_module,
            "_require_onnx",
            lambda: (FakeOrt(), FakeTokenizerCls()),
        )
        OnnxEmbedder(
            "model.onnx",
            tokenizer="org/name",
            tokenizer_revision="sha1",
            hf_token="hf_x",
        ).load()
        assert calls[0] == {"ref": "org/name", "revision": "sha1", "token": "hf_x"}

    def test_local_tokenizer_file_takes_no_hub_kwargs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tempest_fastapi_sdk.genai import OnnxEmbedder
        from tempest_fastapi_sdk.genai import onnx_embed as onnx_module

        calls: list[dict[str, Any]] = []

        class FakeTokenizer:
            def enable_truncation(self, max_length: int) -> None:
                return None

            def enable_padding(self) -> None:
                return None

        class FakeTokenizerCls:
            def from_pretrained(self, ref: str, **kwargs: Any) -> FakeTokenizer:
                calls.append({"ref": ref, **kwargs})
                return FakeTokenizer()

            def from_file(self, ref: str) -> FakeTokenizer:
                calls.append({"ref": ref, "from_file": True})
                return FakeTokenizer()

        class FakeOrt:
            def InferenceSession(self, path: str, **kwargs: Any) -> Any:  # noqa: N802
                return object()

        monkeypatch.setattr(
            onnx_module,
            "_require_onnx",
            lambda: (FakeOrt(), FakeTokenizerCls()),
        )
        OnnxEmbedder(
            "model.onnx",
            tokenizer="/local/tokenizer.json",
            tokenizer_revision="sha1",
        ).load()
        assert calls[0] == {"ref": "/local/tokenizer.json", "from_file": True}


class TestRequireHub:
    def test_missing_dependency_names_the_extra(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def fail(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "huggingface_hub":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fail)
        with pytest.raises(ImportError, match=r"\[genai-hub\]"):
            hub_module._require_hub()
