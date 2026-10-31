"""English (``en-US``) OpenAPI text of the AI and inference routers.

Covers ``make_agent_router``, ``make_ai_chat_router``,
``make_genai_router``, ``make_prediction_router`` and
``make_vision_router``.

None of these routes ever declared a ``summary`` or a ``description``:
every entry is left ``None``, so FastAPI keeps deriving the summary from
the endpoint name and the description from its docstring, exactly as
before #643.
"""

from __future__ import annotations

from tempest_fastapi_sdk.api.route_docs.doc import RouteDoc

AI_ROUTES: dict[str, RouteDoc] = {
    "agent.run_agent": RouteDoc(),
    "agent.stream_agent": RouteDoc(),
    "agent.list_runs": RouteDoc(),
    "agent.get_artifact": RouteDoc(),
    "ai_chat.chat": RouteDoc(),
    "ai_chat.chat_stream": RouteDoc(),
    "genai.generate": RouteDoc(),
    "genai.generate_stream": RouteDoc(),
    "genai.chat": RouteDoc(),
    "genai.embed": RouteDoc(),
    "genai.rag_context": RouteDoc(),
    "genai.transcribe": RouteDoc(),
    "genai.synthesize": RouteDoc(),
    "genai.list_models": RouteDoc(),
    "genai.render_image": RouteDoc(),
    "genai.render_audio": RouteDoc(),
    "prediction.predict": RouteDoc(),
    "prediction.model_info": RouteDoc(),
    "prediction.sync_model": RouteDoc(),
    "prediction.monitor_report": RouteDoc(),
    "vision.classify": RouteDoc(),
    "vision.detect": RouteDoc(),
    "vision.segment": RouteDoc(),
}
"""Operation text of the AI routers, keyed ``<router>.<endpoint name>``."""

__all__: list[str] = ["AI_ROUTES"]
