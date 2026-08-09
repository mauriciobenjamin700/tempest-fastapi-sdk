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
from tempest_fastapi_sdk.queue.dedup import (
    ConcurrentDeliveryError as ConcurrentDeliveryError,
)
from tempest_fastapi_sdk.queue.dedup import DedupState as DedupState
from tempest_fastapi_sdk.queue.dedup import DedupStore as DedupStore
from tempest_fastapi_sdk.queue.dedup import MemoryDedupStore as MemoryDedupStore
from tempest_fastapi_sdk.queue.dedup import RedisDedupStore as RedisDedupStore
from tempest_fastapi_sdk.queue.manager import AsyncBrokerManager as AsyncBrokerManager
from tempest_fastapi_sdk.queue.manager import AsyncQueueManager as AsyncQueueManager
from tempest_fastapi_sdk.queue.reliability import (
    ConsumerRetryPolicy as ConsumerRetryPolicy,
)
from tempest_fastapi_sdk.queue.reliability import QueueMetrics as QueueMetrics
from tempest_fastapi_sdk.queue.reliability import RetryTopology as RetryTopology
from tempest_fastapi_sdk.queue.reliability import (
    delivery_attempt as delivery_attempt,
)
from tempest_fastapi_sdk.queue.reliability import retry_queues as retry_queues
from tempest_fastapi_sdk.queue.topology import DeadLetterSpec as DeadLetterSpec
from tempest_fastapi_sdk.queue.topology import QueueSpec as QueueSpec
from tempest_fastapi_sdk.queue.topology import QueueType as QueueType
from tempest_fastapi_sdk.queue.topology import Transport as Transport
from tempest_fastapi_sdk.queue.topology import (
    UnsupportedTopologyError as UnsupportedTopologyError,
)

__all__: list[str] = [
    "AsyncBrokerManager",
    "AsyncQueueManager",
    "ConcurrentDeliveryError",
    "Consumer",
    "ConsumerRetryPolicy",
    "DeadLetterSpec",
    "DedupState",
    "DedupStore",
    "MemoryDedupStore",
    "MessageBroker",
    "QueueMetrics",
    "QueueSpec",
    "QueueType",
    "RedisDedupStore",
    "RetryTopology",
    "Transport",
    "UnsupportedTopologyError",
    "delivery_attempt",
    "retry_queues",
    "subscribe",
]
