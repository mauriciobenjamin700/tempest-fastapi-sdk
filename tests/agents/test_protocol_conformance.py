"""Static type conformance tests for backend Protocols and TaskIQ ParamSpec."""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

import pytest

PACKAGE_ROOT: Path = (
    Path(__file__).resolve().parent.parent.parent / "tempest_fastapi_sdk"
)
"""Root of the package under check."""

POSITIVE_SNIPPET: str = """
from __future__ import annotations

from tempest_fastapi_sdk.agents import (
    AgentBackend,
    ChatBackend,
    SupportsImageGeneration,
    SupportsVisionGenerate,
    SupportsWebRetrieve,
    ToolCallingBackend,
    describe_image_tool,
    generate_image_tool,
    name_search_tool,
    retrieve_tool,
    speak_tool,
    transcribe_audio_tool,
    web_search_tool,
)
from tempest_fastapi_sdk.agents.testing import FailingBackend, ScriptedBackend
from tempest_fastapi_sdk.api.middlewares.rate_limit import (
    MemoryRateLimitStore,
    RateLimitStore,
    RedisRateLimitStore,
)
from tempest_fastapi_sdk.genai import (
    AnthropicGenerator,
    GenAIMetrics,
    ImageGenerator,
    ModelRuntimeReport,
    OllamaGenerator,
    OllamaSpeech,
    OpenAICompatEmbedder,
    OpenAICompatGenerator,
    OpenAICompatSpeech,
    TextGenerator,
    VisionTextGenerator,
)
from tempest_fastapi_sdk.genai.audio import (
    BarkSpeech,
    SpeechSynthesizer,
    SpeechToText,
    SpeechTranscriber,
    TextToSpeech,
)
from tempest_fastapi_sdk.genai.rag import (
    HybridNameIndex,
    HybridRetriever,
    Retriever,
    SemanticNameIndex,
    SupportsRetrieve,
    WebSearch,
)
from tempest_fastapi_sdk.genai.registry import Unloadable
from tempest_fastapi_sdk.queue import (
    DedupStore,
    MemoryDedupStore,
    MessageBroker,
    QueueMetrics,
    RedisDedupStore,
    RetryTopology,
)
from tempest_fastapi_sdk.tasks import (
    AsyncTaskBrokerManager,
    AsyncTaskScheduler,
    DbDeadLetterSink,
    DeadLetter,
    DeadLetterSink,
)
from tempest_fastapi_sdk.testing.fakes.genai import FakeTextBackend
from tempest_fastapi_sdk.text import FuzzyIndex


def check_backends(
    ollama: OllamaGenerator,
    openai_compat: OpenAICompatGenerator,
    openai_embedder: OpenAICompatEmbedder,
    anthropic: AnthropicGenerator,
    local_text: TextGenerator,
    scripted: ScriptedBackend,
    failing: FailingBackend,
    fake_text: FakeTextBackend,
    image_gen: ImageGenerator,
    vision_gen: VisionTextGenerator,
    web_search: WebSearch,
    retriever: Retriever,
    hybrid_retriever: HybridRetriever,
    stt: SpeechToText,
    ollama_speech: OllamaSpeech,
    openai_speech: OpenAICompatSpeech,
    tts: TextToSpeech,
    bark: BarkSpeech,
    fuzzy: FuzzyIndex[str],
    sem: SemanticNameIndex[str],
    hyb: HybridNameIndex[str],
) -> None:
    tool_backends: list[ToolCallingBackend] = [
        ollama,
        openai_compat,
        anthropic,
        local_text,
        scripted,
        failing,
    ]
    chat_backends: list[ChatBackend] = [
        ollama,
        openai_compat,
        anthropic,
        local_text,
        scripted,
        failing,
        fake_text,
    ]
    agent_backends: list[AgentBackend] = [*tool_backends, *chat_backends]
    img_backends: list[SupportsImageGeneration] = [image_gen]
    vis_backends: list[SupportsVisionGenerate] = [vision_gen]
    web_backends: list[SupportsWebRetrieve] = [web_search]
    ret_backends: list[SupportsRetrieve] = [retriever, hybrid_retriever]
    stt_backends: list[SpeechTranscriber] = [stt, ollama_speech, openai_speech]
    tts_backends: list[SpeechSynthesizer] = [tts, ollama_speech, bark]
    unloadables: list[Unloadable] = [
        ollama,
        openai_embedder,
        anthropic,
        local_text,
        image_gen,
        vision_gen,
        stt,
        tts,
        bark,
    ]
    generate_image_tool(image_gen)
    describe_image_tool(vision_gen)
    transcribe_audio_tool(stt)
    transcribe_audio_tool(ollama_speech)
    transcribe_audio_tool(openai_speech)
    speak_tool(tts)
    speak_tool(ollama_speech)
    speak_tool(bark)
    retrieve_tool(retriever)
    retrieve_tool(hybrid_retriever)
    web_search_tool(web_search)
    name_search_tool(fuzzy)
    name_search_tool(sem)
    name_search_tool(hyb)
    _ = (
        agent_backends,
        img_backends,
        vis_backends,
        web_backends,
        ret_backends,
        stt_backends,
        tts_backends,
        unloadables,
    )


async def check_queue_and_tasks(
    broker: MessageBroker,
    topo: RetryTopology,
    db_sink: DbDeadLetterSink,
    mem_dedup: MemoryDedupStore,
    redis_dedup: RedisDedupStore,
    qmetrics: QueueMetrics,
    gmetrics: GenAIMetrics,
    report: ModelRuntimeReport,
    mem_rl: MemoryRateLimitStore,
    redis_rl: RedisRateLimitStore,
    manager: AsyncTaskBrokerManager,
    scheduler: AsyncTaskScheduler,
) -> None:
    async def fn_sink(letter: DeadLetter) -> None:
        _ = letter

    await broker.declare_retry_topology(topo)
    broker.dead_letter(db_sink)
    broker.dead_letter(fn_sink)
    broker.deduplicate(mem_dedup)
    broker.deduplicate(redis_dedup)
    broker.enable_metrics(qmetrics)
    gmetrics.observe_inventory(report)
    sinks: list[DeadLetterSink] = [db_sink, fn_sink]
    stores: list[DedupStore] = [mem_dedup, redis_dedup]
    rl_stores: list[RateLimitStore] = [mem_rl, redis_rl]

    async def sample_task(x: int, *, flag: bool = False) -> str:
        return f"{x}:{flag}"

    t1 = manager.register_task(sample_task, task_name="sample")
    t2 = scheduler.register(sample_task, schedule=[{"cron": "* * * * *"}])
    await t1.kiq(1, flag=True)
    await t2.kiq(2, flag=False)
    _ = (sinks, stores, rl_stores)
"""
"""Valid assignments of every SDK backend to its Protocol and typed helpers."""

NEGATIVE_PARAMSPEC_SNIPPET: str = """
from __future__ import annotations

from tempest_fastapi_sdk.tasks import AsyncTaskBrokerManager, AsyncTaskScheduler


async def sample_task(x: int, *, flag: bool = False) -> str:
    return f"{x}:{flag}"


async def check_wrong_args(
    manager: AsyncTaskBrokerManager,
    scheduler: AsyncTaskScheduler,
) -> None:
    t1 = manager.register_task(sample_task, task_name="sample")
    t2 = scheduler.register(sample_task, schedule=[{"cron": "* * * * *"}])
    await t1.kiq("wrong")
    await t2.kiq(1, flag="wrong")
"""
"""Wrong argument types must fail mypy when ``ParamSpec`` is preserved."""


def _run_mypy(tmp_path: Path, filename: str, source: str) -> list[str]:
    """Run ``mypy --strict`` on a snippet and return its error lines.

    Args:
        tmp_path (Path): Per-test temporary directory.
        filename (str): Module name written under ``tmp_path``.
        source (str): Python source code to check.

    Returns:
        list[str]: Every line containing ``": error:"`` from mypy stdout.
    """
    mypy_api = pytest.importorskip("mypy.api", reason="mypy is a dev-group dependency")
    module = tmp_path / filename
    module.write_text(source, encoding="utf-8")
    root: str = str(PACKAGE_ROOT.parent)
    digest: str = hashlib.sha256(root.encode("utf-8")).hexdigest()[:12]
    cache: Path = Path(tempfile.gettempdir()) / f"tempest-mypy-{digest}-protocols"
    stdout, _stderr, _status = mypy_api.run(
        [
            str(module),
            "--strict",
            "--no-error-summary",
            "--hide-error-context",
            "--no-color-output",
            "--cache-dir",
            str(cache),
            "--python-executable",
            sys.executable,
        ]
    )
    return [line for line in stdout.splitlines() if ": error:" in line]


def test_sdk_implementations_satisfy_backend_protocols(tmp_path: Path) -> None:
    """Every concrete SDK backend satisfies its Protocol under mypy strict.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
    """
    errors = _run_mypy(tmp_path, "positive_conformance.py", POSITIVE_SNIPPET)
    assert not errors, "\n".join(errors)


def test_task_registration_preserves_paramspec(tmp_path: Path) -> None:
    """``register_task`` and ``scheduler.register`` reject wrong ``kiq`` args.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
    """
    errors = _run_mypy(tmp_path, "negative_paramspec.py", NEGATIVE_PARAMSPEC_SNIPPET)
    assert len(errors) == 2, "\n".join(errors)
    assert all("call-overload" in line for line in errors), "\n".join(errors)
