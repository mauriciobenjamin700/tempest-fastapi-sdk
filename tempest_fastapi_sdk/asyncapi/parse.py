"""Parse an AsyncAPI 3.0 document into the intermediate representation.

The payload half is delegated: AsyncAPI keeps its schemas under
``components.schemas`` exactly where OpenAPI does, and both are JSON Schema,
so :class:`tempest_fastapi_sdk.openapi.parse._Parser` renders them without
knowing which specification they came from. What is written here is the part
that has no OpenAPI counterpart — channels, messages, and the direction of
an operation.

**The direction is inverted here, once.** The document's ``action`` is the
publisher's point of view, the publisher is the server, and the thing being
generated is the client. Doing the inversion in the parser means the emitter
and every reader of the IR see one consistent point of view — the client's —
instead of each having to remember whose ``action`` they are holding.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tempest_fastapi_sdk.asyncapi.ir import (
    AsyncApiIR,
    ChannelIR,
    MessageIR,
    OperationIR,
    StreamIR,
)
from tempest_fastapi_sdk.openapi.ir import ClientIR
from tempest_fastapi_sdk.openapi.loader import SpecError, deref
from tempest_fastapi_sdk.openapi.naming import class_name, to_pascal
from tempest_fastapi_sdk.openapi.parse import (
    _order_schemas,
    _Parser,
    _resolve_dependencies,
)


def _clean(value: Any) -> str:
    """Collapse a prose field to a single trimmed string.

    Args:
        value (Any): The raw value from the document.

    Returns:
        str: The text, or empty when it was absent or not a string.
    """
    return " ".join(str(value).split()) if isinstance(value, str) else ""


def _payload_component(payload: Any) -> str | None:
    """Return the component name a payload points at.

    Args:
        payload (Any): A message's ``payload`` value.

    Returns:
        str | None: The trailing segment of a ``#/components/schemas/...``
        reference, or ``None`` when the payload is inlined.
    """
    if not isinstance(payload, dict):
        return None
    reference = payload.get("$ref")
    if not isinstance(reference, str):
        return None
    prefix = "#/components/schemas/"
    return reference[len(prefix) :] if reference.startswith(prefix) else None


def _discriminant(schema: Mapping[str, Any]) -> tuple[str, str] | None:
    """Find the field that tells one frame apart from its siblings.

    Args:
        schema (Mapping[str, Any]): The payload's JSON Schema.

    Returns:
        tuple[str, str] | None: ``(field, value)`` for the first property
        whose enum holds exactly one string, or ``None``.

    A literal renders as a single-valued ``enum`` rather than ``const`` —
    measured against the documents this generator is pointed at — so that
    is the shape looked for. Without one, a tagged union cannot be built and
    the emitter falls back to trying each variant.
    """
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return None
    for name, raw in properties.items():
        if not isinstance(raw, dict):
            continue
        values = raw.get("enum")
        if isinstance(values, list) and len(values) == 1 and isinstance(values[0], str):
            return str(name), values[0]
    return None


def _server_url(document: Mapping[str, Any], address: str) -> str:
    """Build a connectable URL from the document's first server entry.

    Args:
        document (Mapping[str, Any]): The parsed document.
        address (str): The channel's address, used when the server entry
            declares no ``pathname``.

    Returns:
        str: A ``ws(s)://host/path`` URL, or empty when the document
        declares no servers.
    """
    servers = document.get("servers")
    if not isinstance(servers, dict) or not servers:
        return ""
    entry = next(iter(servers.values()))
    if not isinstance(entry, dict):
        return ""
    host = str(entry.get("host") or "")
    if not host:
        return ""
    protocol = str(entry.get("protocol") or "ws")
    path = str(entry.get("pathname") or address or "")
    if path and not path.startswith("/"):
        path = f"/{path}"
    return f"{protocol}://{host}{path}"


def _handshake_headers(channel: Mapping[str, Any]) -> tuple[tuple[str, bool], ...]:
    """Read the headers the upgrade declares.

    Args:
        channel (Mapping[str, Any]): The channel object.

    Returns:
        tuple[tuple[str, bool], ...]: ``(name, required)`` per header.

    The schema is read from the binding inline. A ``$ref`` there makes the
    document fail AsyncAPI's own meta-schema — ``headers`` is
    ``oneOf: [Schema, Reference]`` and a bare reference satisfies both — so
    a document that validates has it inlined.
    """
    bindings = channel.get("bindings")
    if not isinstance(bindings, dict):
        return ()
    websocket = bindings.get("ws")
    if not isinstance(websocket, dict):
        return ()
    headers = websocket.get("headers")
    if not isinstance(headers, dict):
        return ()
    properties = headers.get("properties")
    if not isinstance(properties, dict):
        return ()
    required = {
        str(name) for name in (headers.get("required") or []) if isinstance(name, str)
    }
    return tuple((str(name), str(name) in required) for name in properties)


def parse_asyncapi(document: Mapping[str, Any], *, client_name: str) -> AsyncApiIR:
    """Parse a loaded AsyncAPI document into the intermediate representation.

    Args:
        document (Mapping[str, Any]): The document, as returned by
            :func:`tempest_fastapi_sdk.asyncapi.load_asyncapi_spec`.
        client_name (str): Base name for the generated client class.

    Returns:
        AsyncApiIR: Payload schemas, the stream client, cyclic class names
        and every note collected.

    Raises:
        SpecError: When the document declares no channel, more than one
            channel, or no messages. One channel is not a simplification:
            WebSocket has no virtual channels, and a document with several
            is describing a transport this emitter does not generate for.
    """
    parser = _Parser(document, client_name=client_name)

    components = document.get("components")
    components = components if isinstance(components, dict) else {}
    raw_schemas = components.get("schemas")
    raw_schemas = raw_schemas if isinstance(raw_schemas, dict) else {}
    raw_messages = components.get("messages")
    raw_messages = raw_messages if isinstance(raw_messages, dict) else {}

    if not raw_messages:
        raise SpecError(
            "The document declares no `components.messages`, so there are no "
            "frames to generate."
        )

    for wire_name in raw_schemas:
        raw = raw_schemas[wire_name]
        if isinstance(raw, dict):
            parser.ensure_component(str(wire_name), deref(document, raw))

    channels = document.get("channels")
    channels = channels if isinstance(channels, dict) else {}
    if len(channels) != 1:
        raise SpecError(
            f"The document declares {len(channels)} channels; this generator "
            f"emits a WebSocket client, and WebSocket has exactly one channel "
            f"per connection."
        )
    channel_name, raw_channel = next(iter(channels.items()))
    raw_channel = raw_channel if isinstance(raw_channel, dict) else {}
    address = str(raw_channel.get("address") or "")

    channel = ChannelIR(
        name=str(channel_name),
        address=address,
        title=_clean(raw_channel.get("title")),
        description=_clean(raw_channel.get("description")),
        handshake_headers=_handshake_headers(raw_channel),
    )

    messages: list[MessageIR] = []
    for wire_name, raw in raw_messages.items():
        raw = raw if isinstance(raw, dict) else {}
        component = _payload_component(raw.get("payload"))
        if component is None:
            parser.note(
                f"message {wire_name} inlines its payload — only a payload "
                f"referencing `#/components/schemas/...` is modelled"
            )
            continue
        schema = raw_schemas.get(component)
        schema = schema if isinstance(schema, dict) else {}
        messages.append(
            MessageIR(
                name=str(wire_name),
                class_name=parser.wire_to_class.get(component, class_name(component)),
                summary=_clean(raw.get("summary")),
                description=_clean(raw.get("description")),
                content_type=str(raw.get("contentType") or "application/json"),
                discriminant=_discriminant(schema),
            )
        )

    operations: list[OperationIR] = []
    raw_operations = document.get("operations")
    raw_operations = raw_operations if isinstance(raw_operations, dict) else {}
    for wire_name, raw in raw_operations.items():
        raw = raw if isinstance(raw, dict) else {}
        action = raw.get("action")
        if action not in {"send", "receive"}:
            parser.note(
                f"operation {wire_name} declares action {action!r}, which is "
                f"neither `send` nor `receive` — skipped"
            )
            continue
        operations.append(
            OperationIR(
                name=str(wire_name),
                direction="outbound" if action == "receive" else "inbound",
                summary=_clean(raw.get("summary")),
                description=_clean(raw.get("description")),
                messages=tuple(_operation_messages(raw)),
            )
        )

    info = document.get("info")
    info = info if isinstance(info, dict) else {}
    stream = StreamIR(
        class_name=f"{to_pascal(client_name)}Stream",
        title=str(info.get("title") or client_name),
        version=str(info.get("version") or ""),
        default_url=_server_url(document, address),
        channel=channel,
        operations=tuple(
            sorted(operations, key=lambda o: 0 if o.direction == "outbound" else 1)
        ),
        messages=tuple(messages),
    )

    resolved = _resolve_dependencies(parser.schemas)
    ordered, cyclic = _order_schemas(resolved)
    return AsyncApiIR(
        schemas=ordered,
        stream=stream,
        cyclic=cyclic,
        unsupported=tuple(parser.notes),
    )


def _operation_messages(operation: Mapping[str, Any]) -> list[str]:
    """Read the message names an operation refers to.

    Args:
        operation (Mapping[str, Any]): The operation object.

    Returns:
        list[str]: The trailing segment of each ``$ref``, in order.

    The specification points these at the channel
    (``#/channels/<name>/messages/<message>``) rather than at
    ``components``, so only the last segment is meaningful here.
    """
    raw = operation.get("messages")
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for entry in raw:
        if isinstance(entry, dict) and isinstance(entry.get("$ref"), str):
            names.append(str(entry["$ref"]).rsplit("/", 1)[-1])
    return names


def as_spec_ir(parsed: AsyncApiIR) -> Any:
    """Wrap the payload schemas so the OpenAPI schema emitter can render them.

    Args:
        parsed (AsyncApiIR): The parsed document.

    Returns:
        Any: A ``SpecIR`` carrying the schemas and cyclic set.

    ``emit_schemas`` reads only those two fields, so the client it also
    holds is a placeholder rather than something this module has to model.
    """
    from tempest_fastapi_sdk.openapi.ir import SpecIR

    return SpecIR(
        schemas=parsed.schemas,
        client=ClientIR(
            class_name=parsed.stream.class_name,
            title=parsed.stream.title,
            version=parsed.stream.version,
            base_url=parsed.stream.default_url,
        ),
        cyclic=parsed.cyclic,
        unsupported=parsed.unsupported,
    )


__all__: list[str] = [
    "as_spec_ir",
    "parse_asyncapi",
]
