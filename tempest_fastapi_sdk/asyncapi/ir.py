"""Intermediate representation of one AsyncAPI document.

Mirrors :mod:`tempest_fastapi_sdk.openapi.ir` in spirit: a shape the emitter
can render without going back to the raw document. The schema half is
reused outright — payloads are JSON Schema in both specifications, and they
live under ``components.schemas`` in both — so only the message, channel and
operation halves are modelled here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tempest_fastapi_sdk.openapi.ir import SchemaIR


@dataclass(frozen=True, slots=True)
class MessageIR:
    """One frame the connection carries.

    Attributes:
        name (str): Component name in the document.
        class_name (str): Generated Python class for the payload.
        summary (str): One-line description, or empty.
        description (str): Longer prose, or empty.
        content_type (str): Declared media type of the payload.
        discriminant (tuple[str, str] | None): ``(field, value)`` when the
            payload carries a single-valued enum that identifies it, which
            is what a tagged union needs. ``None`` when the frame cannot be
            told apart from its siblings by a field.
    """

    name: str
    class_name: str
    summary: str = ""
    description: str = ""
    content_type: str = "application/json"
    discriminant: tuple[str, str] | None = None


@dataclass(frozen=True, slots=True)
class ChannelIR:
    """The connection itself.

    Attributes:
        name (str): Key the channel is registered under.
        address (str): Path the socket is served at.
        title (str): One-line description, or empty.
        description (str): Longer prose, or empty.
        handshake_headers (tuple[tuple[str, bool], ...]): ``(name,
            required)`` for each header the upgrade declares.

    WebSocket has no virtual channels — the specification is explicit that
    the channel *is* the connection — so a document describing a socket has
    exactly one, and the generated client takes one URL.
    """

    name: str
    address: str
    title: str = ""
    description: str = ""
    handshake_headers: tuple[tuple[str, bool], ...] = ()


@dataclass(frozen=True, slots=True)
class OperationIR:
    """A set of frames travelling one way.

    Attributes:
        name (str): Key the operation is registered under.
        direction (Literal["outbound", "inbound"]): Which way the frames
            travel **from the generated client's point of view**.
        summary (str): One-line description, or empty.
        description (str): Longer prose, or empty.
        messages (tuple[str, ...]): Names of the messages it carries.

    The direction is already inverted. The document's ``action`` is written
    from the server's point of view, so ``receive`` there — the server
    receiving — is ``outbound`` here.
    """

    name: str
    direction: Literal["outbound", "inbound"]
    summary: str = ""
    description: str = ""
    messages: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StreamIR:
    """The generated client class.

    Attributes:
        class_name (str): Name of the generated class.
        title (str): ``info.title`` from the document.
        version (str): ``info.version`` from the document.
        default_url (str): ``ws(s)://`` URL built from the first server
            entry, or empty when the document declares none.
        channel (ChannelIR): The connection.
        operations (tuple[OperationIR, ...]): Outbound first, then inbound.
        messages (tuple[MessageIR, ...]): Every frame, in document order.
    """

    class_name: str
    title: str
    version: str
    default_url: str
    channel: ChannelIR
    operations: tuple[OperationIR, ...] = ()
    messages: tuple[MessageIR, ...] = ()

    @property
    def outbound(self) -> tuple[MessageIR, ...]:
        """Frames the client sends.

        Returns:
            tuple[MessageIR, ...]: The matching messages, in document order.
        """
        names = {
            name
            for operation in self.operations
            if operation.direction == "outbound"
            for name in operation.messages
        }
        return tuple(m for m in self.messages if m.name in names)

    @property
    def inbound(self) -> tuple[MessageIR, ...]:
        """Frames the client receives.

        Returns:
            tuple[MessageIR, ...]: The matching messages, in document order.
        """
        names = {
            name
            for operation in self.operations
            if operation.direction == "inbound"
            for name in operation.messages
        }
        return tuple(m for m in self.messages if m.name in names)


@dataclass(frozen=True, slots=True)
class AsyncApiIR:
    """Everything parsed out of one AsyncAPI document.

    Attributes:
        schemas (tuple[SchemaIR, ...]): Payload classes, ordered so a class
            appears after the ones it depends on.
        stream (StreamIR): The generated client.
        cyclic (frozenset[str]): Classes taking part in a reference cycle.
        unsupported (tuple[str, ...]): Notes collected while parsing.
    """

    schemas: tuple[SchemaIR, ...]
    stream: StreamIR
    cyclic: frozenset[str] = frozenset()
    unsupported: tuple[str, ...] = ()


__all__: list[str] = [
    "AsyncApiIR",
    "ChannelIR",
    "MessageIR",
    "OperationIR",
    "StreamIR",
]
