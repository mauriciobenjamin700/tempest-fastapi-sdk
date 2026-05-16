"""FastStream-backed message queue primitives.

Imports the optional ``faststream`` package lazily so the rest of the
SDK remains importable when the ``[queue]`` extra is not installed.
"""

from tempest_fastapi_sdk.queue.manager import AsyncBrokerManager

__all__: list[str] = [
    "AsyncBrokerManager",
]
