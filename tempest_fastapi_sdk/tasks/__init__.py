"""TaskIQ-backed background task primitives.

Imports the optional ``taskiq`` package lazily so the rest of the SDK
remains importable when the ``[tasks]`` extra is not installed.
"""

from tempest_fastapi_sdk.tasks.manager import AsyncTaskBrokerManager

__all__: list[str] = [
    "AsyncTaskBrokerManager",
]
