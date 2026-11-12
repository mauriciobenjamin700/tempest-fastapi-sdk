"""`MessageBroker.kafka_from_settings` — the broker a `KafkaSettings` describes.

Read off the connection builder FastStream keeps on the broker: those are the
keywords ``aiokafka`` receives on ``connect()``, so no cluster is needed.
"""

from __future__ import annotations

from typing import Any

import pytest
from faststream.kafka import KafkaBroker
from faststream.security import SASLScram512
from pydantic_settings import SettingsConfigDict

from tempest_fastapi_sdk.queue import MessageBroker, TransportEnum
from tempest_fastapi_sdk.settings import KafkaSettings


class Isolated(KafkaSettings):
    """The mixin without the developer's ``.env``."""

    model_config = SettingsConfigDict(env_file=None)


def _connection_kwargs(mq: MessageBroker) -> dict[str, Any]:
    """Return the keywords FastStream will hand ``AIOKafkaProducer``.

    Args:
        mq (MessageBroker): A facade over a ``KafkaBroker``.

    Returns:
        dict[str, Any]: The builder's bound keywords.
    """
    keywords: dict[str, Any] = mq.broker.config.broker_config.builder.keywords
    return keywords


def test_default_settings_build_a_plaintext_broker() -> None:
    mq = MessageBroker.kafka_from_settings(Isolated())
    kwargs = _connection_kwargs(mq)

    assert isinstance(mq.broker, KafkaBroker)
    assert mq.transport is TransportEnum.KAFKA
    assert kwargs["bootstrap_servers"] == ["localhost:9092"]
    assert "security_protocol" not in kwargs


def test_seeds_client_id_and_security_reach_the_connection() -> None:
    settings = Isolated(
        KAFKA_BOOTSTRAP_SERVERS=["k1:9093", "k2:9093"],
        KAFKA_CLIENT_ID="billing-api",
        KAFKA_SECURITY_PROTOCOL="SASL_SSL",
        KAFKA_SASL_MECHANISM="SCRAM-SHA-512",
        KAFKA_SASL_USERNAME="billing",
        KAFKA_SASL_PASSWORD="s3cret",
    )

    mq = MessageBroker.kafka_from_settings(settings)
    kwargs = _connection_kwargs(mq)

    assert kwargs["bootstrap_servers"] == ["k1:9093", "k2:9093"]
    assert kwargs["client_id"] == "billing-api"
    assert kwargs["security_protocol"] == "SASL_SSL"
    assert kwargs["sasl_mechanism"] == "SCRAM-SHA-512"
    assert kwargs["sasl_plain_username"] == "billing"
    assert kwargs["sasl_plain_password"] == "s3cret"
    assert kwargs["ssl_context"] is not None
    assert isinstance(mq.broker.specification.security, SASLScram512)


def test_options_win_over_the_settings() -> None:
    mq = MessageBroker.kafka_from_settings(
        Isolated(KAFKA_CLIENT_ID="from-settings"),
        client_id="from-call",
    )

    assert _connection_kwargs(mq)["client_id"] == "from-call"


def test_declare_topology_is_forwarded() -> None:
    mq = MessageBroker.kafka_from_settings(Isolated(), declare_topology=False)

    assert mq._declare_topology is False


def test_splatting_kwargs_alone_is_refused() -> None:
    """The seeds are positional on ``kafka``; a splat alone would carry none.

    Before the refusal FastStream took the empty list and the broker failed
    only on ``connect()``.
    """
    settings = Isolated(KAFKA_BOOTSTRAP_SERVERS=["k1:9093"])

    with pytest.raises(ValueError, match="kafka_from_settings"):
        MessageBroker.kafka(**settings.kafka_kwargs())


@pytest.mark.parametrize("protocol", ["SSL", "SASL_SSL"])
def test_tls_protocols_carry_a_context(protocol: str) -> None:
    settings = Isolated(
        KAFKA_SECURITY_PROTOCOL=protocol,
        KAFKA_SASL_USERNAME="billing",
        KAFKA_SASL_PASSWORD="s3cret",
    )

    kwargs = _connection_kwargs(MessageBroker.kafka_from_settings(settings))

    assert kwargs["security_protocol"] == protocol
    assert kwargs["ssl_context"] is not None
