"""FastStream-backed message queue primitives.

Imports the optional ``faststream`` package lazily so the rest of the
SDK remains importable when the ``[queue]`` extra is not installed.

``MessageBroker`` is the recommended typed, transport-agnostic facade;
``AsyncBrokerManager`` is the older lifecycle-only wrapper, kept for
backward compatibility.
"""

from tempest_fastapi_sdk.queue.broker import MessageBroker as MessageBroker
from tempest_fastapi_sdk.queue.manager import AsyncBrokerManager as AsyncBrokerManager

__all__: list[str] = [
    "AsyncBrokerManager",
    "MessageBroker",
]
