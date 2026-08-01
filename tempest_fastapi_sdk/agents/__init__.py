"""AI agents over the models you already self-host.

An agent takes a **goal**, decides what to do, calls tools, and reports
what it did. That last part is the difference from a chat pipeline: the
run comes back with a step-by-step trace — arguments, outputs, timings,
failures — plus whatever files it produced.

    >>> from tempest_fastapi_sdk.agents import Agent, generate_image_tool
    >>> agent = Agent(
    ...     OllamaGenerator("llama3.2"),
    ...     tools=[generate_image_tool(image_generator)],
    ... )
    >>> run = await agent.run("Draw a red bicycle at sunset.")
    >>> run.output, run.tool_calls
    >>> run.artifacts[0].data       # the PNG bytes

The tools in :mod:`tempest_fastapi_sdk.agents.builtin` wrap the models the
SDK already runs locally — text, image, audio, RAG — so an agent composes
what is there rather than reaching for a paid API. They chain through
**named artifacts**: an agent can draw a picture and then ask the vision
model to check it, without either the bytes touching disk or base64
touching the prompt.

Submodule import, like ``genai`` and ``modelops``:
``from tempest_fastapi_sdk.agents import Agent``. Imports with no optional
extra — the heavy work lives in the objects you inject.
"""

from tempest_fastapi_sdk.agents.agent import (
    DEFAULT_SYSTEM_PROMPT as DEFAULT_SYSTEM_PROMPT,
)
from tempest_fastapi_sdk.agents.agent import Agent as Agent
from tempest_fastapi_sdk.agents.builtin import (
    describe_image_tool as describe_image_tool,
)
from tempest_fastapi_sdk.agents.builtin import (
    generate_image_tool as generate_image_tool,
)
from tempest_fastapi_sdk.agents.builtin import retrieve_tool as retrieve_tool
from tempest_fastapi_sdk.agents.builtin import (
    save_artifact_tool as save_artifact_tool,
)
from tempest_fastapi_sdk.agents.builtin import speak_tool as speak_tool
from tempest_fastapi_sdk.agents.builtin import (
    transcribe_audio_tool as transcribe_audio_tool,
)
from tempest_fastapi_sdk.agents.builtin import web_search_tool as web_search_tool
from tempest_fastapi_sdk.agents.router import (
    AgentArtifactSchema as AgentArtifactSchema,
)
from tempest_fastapi_sdk.agents.router import (
    AgentRunRequestSchema as AgentRunRequestSchema,
)
from tempest_fastapi_sdk.agents.router import (
    AgentRunResponseSchema as AgentRunResponseSchema,
)
from tempest_fastapi_sdk.agents.router import make_agent_router as make_agent_router
from tempest_fastapi_sdk.agents.schemas import AgentArtifact as AgentArtifact
from tempest_fastapi_sdk.agents.schemas import AgentBudget as AgentBudget
from tempest_fastapi_sdk.agents.schemas import AgentRun as AgentRun
from tempest_fastapi_sdk.agents.schemas import AgentStep as AgentStep
from tempest_fastapi_sdk.agents.schemas import StepKind as StepKind
from tempest_fastapi_sdk.agents.schemas import StopReason as StopReason
from tempest_fastapi_sdk.agents.schemas import ToolResult as ToolResult
from tempest_fastapi_sdk.agents.storage import AgentRunSink as AgentRunSink
from tempest_fastapi_sdk.agents.storage import BaseAgentRunModel as BaseAgentRunModel
from tempest_fastapi_sdk.agents.storage import DbAgentRunSink as DbAgentRunSink
from tempest_fastapi_sdk.agents.storage import (
    InMemoryAgentRunSink as InMemoryAgentRunSink,
)
from tempest_fastapi_sdk.agents.storage import (
    make_agent_run_model as make_agent_run_model,
)
from tempest_fastapi_sdk.agents.tools import AgentContext as AgentContext
from tempest_fastapi_sdk.agents.tools import AgentTool as AgentTool
from tempest_fastapi_sdk.agents.tools import AgentToolError as AgentToolError
from tempest_fastapi_sdk.agents.tools import ToolHandler as ToolHandler
from tempest_fastapi_sdk.agents.tools import ToolReturn as ToolReturn
from tempest_fastapi_sdk.agents.tools import text_tool as text_tool

__all__: list[str] = [
    "DEFAULT_SYSTEM_PROMPT",
    "Agent",
    "AgentArtifact",
    "AgentArtifactSchema",
    "AgentBudget",
    "AgentContext",
    "AgentRun",
    "AgentRunRequestSchema",
    "AgentRunResponseSchema",
    "AgentRunSink",
    "AgentStep",
    "AgentTool",
    "AgentToolError",
    "BaseAgentRunModel",
    "DbAgentRunSink",
    "InMemoryAgentRunSink",
    "StepKind",
    "StopReason",
    "ToolHandler",
    "ToolResult",
    "ToolReturn",
    "describe_image_tool",
    "generate_image_tool",
    "make_agent_router",
    "make_agent_run_model",
    "retrieve_tool",
    "save_artifact_tool",
    "speak_tool",
    "text_tool",
    "transcribe_audio_tool",
    "web_search_tool",
]
