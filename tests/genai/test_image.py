"""Tests for local image generation.

``diffusers`` is never imported: a fake pipeline is injected over
:func:`tempest_fastapi_sdk.genai.image._require_diffusers`, so the suite
exercises the config mapping, the seed contract, the encoding and the
router without a GPU or a multi-gigabyte download.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from tempest_fastapi_sdk.genai import (
    GeneratedImage,
    ImageGenerationConfig,
    ImageGenerator,
    make_genai_router,
)
from tempest_fastapi_sdk.genai import image as image_module


class FakePillowImage:
    def __init__(self, width: int = 64, height: int = 48) -> None:
        self.width = width
        self.height = height

    def save(self, buffer: Any, format: str) -> None:
        Image.new("RGB", (self.width, self.height), (10, 20, 30)).save(
            buffer,
            format=format,
        )


class FakeResult:
    def __init__(self, images: list[FakePillowImage]) -> None:
        self.images = images


class FakePipeline:
    def __init__(self, name: str = "text2img", n_images: int = 1) -> None:
        self.name = name
        self.n_images = n_images
        self.calls: list[dict[str, Any]] = []
        self.moved_to: str | None = None

    def to(self, device: str) -> FakePipeline:
        self.moved_to = device
        return self

    def __call__(self, **kwargs: Any) -> FakeResult:
        self.calls.append(kwargs)
        count = kwargs.get("num_images_per_prompt", self.n_images)
        return FakeResult([FakePillowImage() for _ in range(count)])


class FakeGenerator:
    def __init__(self, device: str) -> None:
        self.device = device
        self.seed: int | None = None

    def manual_seed(self, seed: int) -> FakeGenerator:
        self.seed = seed
        return self


class FakeTorch:
    float32 = "float32"
    float16 = "float16"
    bfloat16 = "bfloat16"

    @staticmethod
    def Generator(device: str) -> FakeGenerator:  # noqa: N802
        return FakeGenerator(device)


class FakeAutoText2Image:
    def __init__(self, pipeline: FakePipeline) -> None:
        self._pipeline = pipeline
        self.load_kwargs: dict[str, Any] = {}

    def from_pretrained(self, model_id: str, **kwargs: Any) -> FakePipeline:
        self.load_kwargs = {"model_id": model_id, **kwargs}
        return self._pipeline


class FakeAutoImage2Image:
    def __init__(self, pipeline: FakePipeline) -> None:
        self._pipeline = pipeline
        self.from_pipe_calls: list[Any] = []

    def from_pipe(self, source: Any) -> FakePipeline:
        self.from_pipe_calls.append(source)
        return self._pipeline


class FakeDiffusers:
    def __init__(self) -> None:
        self.text2img = FakePipeline("text2img")
        self.img2img = FakePipeline("img2img")
        self.AutoPipelineForText2Image = FakeAutoText2Image(self.text2img)
        self.AutoPipelineForImage2Image = FakeAutoImage2Image(self.img2img)


def _install(monkeypatch: pytest.MonkeyPatch) -> FakeDiffusers:
    """Inject the fake torch + diffusers pair into the image module."""
    fake = FakeDiffusers()
    monkeypatch.setattr(
        image_module,
        "_require_diffusers",
        lambda: (FakeTorch(), fake),
    )
    return fake


def _png_bytes(width: int = 32, height: int = 16) -> bytes:
    """Return a small real PNG, for the image-to-image input."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 100, 50)).save(buffer, format="PNG")
    return buffer.getvalue()


class TestImageGenerationConfig:
    def test_renames_to_the_diffusers_spellings(self) -> None:
        config = ImageGenerationConfig(steps=4, num_images=3)
        kwargs = config.to_pipeline_kwargs()
        assert kwargs["num_inference_steps"] == 4
        assert kwargs["num_images_per_prompt"] == 3
        assert "steps" not in kwargs
        assert "num_images" not in kwargs

    def test_seed_is_not_a_pipeline_keyword(self) -> None:
        assert "seed" not in ImageGenerationConfig(seed=7).to_pipeline_kwargs()

    def test_unset_fields_do_not_leak(self) -> None:
        assert ImageGenerationConfig().to_pipeline_kwargs() == {}

    def test_zero_guidance_survives(self) -> None:
        kwargs = ImageGenerationConfig(guidance_scale=0.0).to_pipeline_kwargs()
        assert kwargs["guidance_scale"] == 0.0

    def test_rejects_non_positive_sizes(self) -> None:
        with pytest.raises(ValueError):
            ImageGenerationConfig(width=0)


class TestLoad:
    def test_forwards_dtype_and_the_model_ref(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _install(monkeypatch)
        generator = ImageGenerator(
            "org/diffusion",
            device="cpu",
            revision="sha1",
            cache_dir="/models",
            local_files_only=True,
        )
        generator.load()
        loaded = fake.AutoPipelineForText2Image.load_kwargs
        assert loaded["model_id"] == "org/diffusion"
        assert loaded["torch_dtype"] == "float32"
        assert loaded["revision"] == "sha1"
        assert loaded["cache_dir"] == "/models"
        assert loaded["local_files_only"] is True
        assert fake.text2img.moved_to == "cpu"

    def test_load_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _install(monkeypatch)
        generator = ImageGenerator("org/diffusion", device="cpu")
        generator.load()
        first = fake.text2img.moved_to
        fake.text2img.moved_to = None
        generator.load()
        assert first == "cpu"
        assert fake.text2img.moved_to is None

    def test_unload_frees_both_pipelines(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch)
        generator = ImageGenerator("org/diffusion", device="cpu")
        generator.load()
        assert generator.is_loaded is True
        generator.unload()
        assert generator.is_loaded is False

    def test_rejects_non_positive_concurrency(self) -> None:
        with pytest.raises(ValueError, match="max_concurrent"):
            ImageGenerator("org/diffusion", max_concurrent=0)


class TestGenerate:
    @pytest.mark.asyncio
    async def test_returns_encoded_images_with_dimensions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch)
        generator = ImageGenerator("org/diffusion", device="cpu")
        images = await generator.generate("a red bicycle")
        assert len(images) == 1
        assert isinstance(images[0], GeneratedImage)
        assert images[0].data.startswith(b"\x89PNG")
        assert images[0].width == 64
        assert images[0].height == 48
        assert images[0].image_format == "png"

    @pytest.mark.asyncio
    async def test_explicit_seed_is_used_and_returned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch)
        generator = ImageGenerator("org/diffusion", device="cpu")
        images = await generator.generate(
            "a red bicycle",
            config=ImageGenerationConfig(seed=99),
        )
        assert images[0].seed == 99

    @pytest.mark.asyncio
    async def test_a_seed_is_drawn_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch)
        generator = ImageGenerator("org/diffusion", device="cpu")
        images = await generator.generate("a red bicycle")
        assert isinstance(images[0].seed, int)
        assert images[0].seed >= 0

    @pytest.mark.asyncio
    async def test_config_reaches_the_pipeline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _install(monkeypatch)
        generator = ImageGenerator("org/diffusion", device="cpu")
        await generator.generate(
            "a red bicycle",
            config=ImageGenerationConfig(
                steps=4,
                guidance_scale=0.0,
                negative_prompt="blurry",
                num_images=2,
            ),
        )
        call = fake.text2img.calls[0]
        assert call["prompt"] == "a red bicycle"
        assert call["num_inference_steps"] == 4
        assert call["guidance_scale"] == 0.0
        assert call["negative_prompt"] == "blurry"
        assert call["generator"].seed is not None

    @pytest.mark.asyncio
    async def test_num_images_returns_that_many(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch)
        generator = ImageGenerator("org/diffusion", device="cpu")
        images = await generator.generate(
            "a red bicycle",
            config=ImageGenerationConfig(num_images=3),
        )
        assert len(images) == 3
        assert len({image.seed for image in images}) == 1

    @pytest.mark.asyncio
    async def test_jpeg_format_changes_the_encoding(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch)
        generator = ImageGenerator(
            "org/diffusion",
            device="cpu",
            image_format="jpeg",
        )
        images = await generator.generate("a red bicycle")
        assert images[0].image_format == "jpeg"
        assert images[0].data.startswith(b"\xff\xd8")


class TestEdit:
    @pytest.mark.asyncio
    async def test_reuses_the_loaded_components(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _install(monkeypatch)
        generator = ImageGenerator("org/diffusion", device="cpu")
        await generator.edit("at night", _png_bytes())
        assert fake.AutoPipelineForImage2Image.from_pipe_calls == [fake.text2img]

    @pytest.mark.asyncio
    async def test_builds_the_edit_pipeline_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _install(monkeypatch)
        generator = ImageGenerator("org/diffusion", device="cpu")
        await generator.edit("at night", _png_bytes())
        await generator.edit("at dawn", _png_bytes())
        assert len(fake.AutoPipelineForImage2Image.from_pipe_calls) == 1

    @pytest.mark.asyncio
    async def test_forwards_strength_and_the_input_image(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _install(monkeypatch)
        generator = ImageGenerator("org/diffusion", device="cpu")
        await generator.edit("at night", _png_bytes(), strength=0.4)
        call = fake.img2img.calls[0]
        assert call["strength"] == 0.4
        assert call["image"].size == (32, 16)

    @pytest.mark.asyncio
    async def test_rejects_out_of_range_strength(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch)
        generator = ImageGenerator("org/diffusion", device="cpu")
        with pytest.raises(ValueError, match="strength"):
            await generator.edit("at night", _png_bytes(), strength=1.5)

    @pytest.mark.asyncio
    async def test_rejects_an_unsupported_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch)
        generator = ImageGenerator("org/diffusion", device="cpu")
        with pytest.raises(TypeError, match="unsupported image source"):
            await generator.edit("at night", 42)


class TestIdleUnload:
    def test_no_threshold_never_unloads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch)
        generator = ImageGenerator("org/diffusion", device="cpu")
        generator.load()
        assert generator.unload_if_idle() is False
        assert generator.is_loaded is True

    def test_unloads_past_the_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(monkeypatch)
        generator = ImageGenerator(
            "org/diffusion",
            device="cpu",
            idle_unload_seconds=0.0,
        )
        generator.load()
        assert generator.unload_if_idle() is True
        assert generator.is_loaded is False

    def test_keeps_a_recently_used_pipeline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch)
        generator = ImageGenerator(
            "org/diffusion",
            device="cpu",
            idle_unload_seconds=3600.0,
        )
        generator.load()
        assert generator.unload_if_idle() is False


class TestRouter:
    def _client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        _install(monkeypatch)
        generator = ImageGenerator("org/diffusion", device="cpu")
        app = FastAPI()
        app.include_router(make_genai_router(image_generator=generator))
        return TestClient(app)

    def test_returns_the_image_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._client(monkeypatch)
        response = client.post("/api/genai/image", json={"prompt": "a cat"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content.startswith(b"\x89PNG")

    def test_reports_the_seed_in_a_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = self._client(monkeypatch)
        response = client.post(
            "/api/genai/image",
            json={"prompt": "a cat", "config": {"seed": 123}},
        )
        assert response.headers["x-image-seed"] == "123"

    def test_router_without_any_object_refuses(self) -> None:
        with pytest.raises(ValueError, match="at least one GenAI object"):
            make_genai_router()


class TestMissingExtra:
    def test_import_error_names_the_extra(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def fail(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "diffusers":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fail)
        with pytest.raises(ImportError, match=r"\[genai-image\]"):
            image_module._require_diffusers()
