"""FastStream-backed message queue primitives.

Imports the optional ``faststream`` package lazily so the rest of the
SDK remains importable when the ``[queue]`` extra is not installed.

``MessageBroker`` is the recommended typed, transport-agnostic facade
(constructors, ``@on`` / class-based consumers, channel-first publish).
``AsyncQueueManager`` is a minimal lifecycle wrapper around an injected
broker (renamed from ``AsyncBrokerManager``, kept as a deprecated alias).
"""

from tempest_fastapi_sdk.queue.broker import MessageBroker as MessageBroker
from tempest_fastapi_sdk.queue.consumer import Consumer as Consumer
from tempest_fastapi_sdk.queue.consumer import subscribe as subscribe
from tempest_fastapi_sdk.queue.manager import AsyncBrokerManager as AsyncBrokerManager
from tempest_fastapi_sdk.queue.manager import AsyncQueueManager as AsyncQueueManager

__all__: list[str] = [
    "AsyncBrokerManager",
    "AsyncQueueManager",
    "Consumer",
    "MessageBroker",
    "subscribe",
]
