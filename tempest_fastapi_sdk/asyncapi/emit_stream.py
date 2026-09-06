"""Render a :class:`~tempest_fastapi_sdk.asyncapi.ir.StreamIR` into a client.

The generated class wraps ``websockets``, which the SDK already declares
under the ``[websocket]`` extra, so a document turns into a client with no
new dependency.

Two shapes carry the whole design:

* **A tagged union per direction.** Frames the client sends are one union,
  frames it receives are another, and each variant is told apart by the
  single-valued enum the document declares. A caller matching on the inbound
  union gets exhaustiveness from the type-checker.
* **A dispatch table, not Pydantic's discriminator.** The payload classes
  type their tag as a generated enum rather than a ``Literal``, which is not
  what ``Field(discriminator=...)`` needs. Mapping the tag to its class is
  explicit, reads at a glance, and fails with the tag it did not recognise
  instead of a validation error listing every variant.
"""

from __future__ import annotations

from tempest_fastapi_sdk.asyncapi.ir import MessageIR, StreamIR
from tempest_fastapi_sdk.openapi.naming import to_snake
from tempest_fastapi_sdk.openapi.source import (
    string_literal as _string_literal,
)
from tempest_fastapi_sdk.openapi.source import (
    unsupported_comment as _unsupported_comment,
)
from tempest_fastapi_sdk.openapi.source import (
    wrap as _wrap,
)


def _header_argument(header: str) -> str:
    """Turn a wire header name into a Python argument name.

    Args:
        header (str): The header as the document spells it.

    Returns:
        str: A snake_case argument name.
    """
    return to_snake(header.replace("-", "_"))


def _union(name: str, messages: tuple[MessageIR, ...]) -> list[str]:
    """Render a type alias unioning a direction's frames.

    Args:
        name (str): The alias name.
        messages (tuple[MessageIR, ...]): The frames in that direction.

    Returns:
        list[str]: Source lines, empty when the direction carries none.
    """
    if not messages:
        return []
    variants = [message.class_name for message in messages]
    single = f"{name} = {' | '.join(variants)}"
    if len(single) <= 88:
        return [single, ""]
    lines = [f"{name} = ("]
    for index, variant in enumerate(variants):
        suffix = "" if index == len(variants) - 1 else " |"
        lines.append(f"    {variant}{suffix}")
    lines.extend([")", ""])
    return lines


def _dispatch(name: str, messages: tuple[MessageIR, ...], union: str) -> list[str]:
    """Render the tag-to-class table for one direction.

    Args:
        name (str): The constant name.
        messages (tuple[MessageIR, ...]): The frames in that direction.
        union (str): The union alias the values belong to.

    Returns:
        list[str]: Source lines, empty when the direction carries none.
    """
    tagged = [m for m in messages if m.discriminant is not None]
    if not tagged:
        return []
    lines = [f"{name}: dict[str, type[{union}]] = {{"]
    for message in tagged:
        assert message.discriminant is not None
        _, value = message.discriminant
        lines.append(f"    {_string_literal(value)}: {message.class_name},")
    lines.extend(["}", ""])
    return lines


def _tag_field(messages: tuple[MessageIR, ...]) -> str | None:
    """Return the field every frame in a direction is told apart by.

    Args:
        messages (tuple[MessageIR, ...]): The frames in that direction.

    Returns:
        str | None: The shared discriminant field, or ``None`` when the
        frames disagree on which field tags them.
    """
    fields = {m.discriminant[0] for m in messages if m.discriminant is not None}
    return fields.pop() if len(fields) == 1 else None


def emit_stream(stream: StreamIR, *, schemas_module: str) -> str:
    """Render the generated WebSocket client module.

    Args:
        stream (StreamIR): The parsed stream.
        schemas_module (str): Module name to import the payloads from,
            relative to the generated package.

    Returns:
        str: The complete module source, ending in a newline.

    Raises:
        ValueError: When the inbound frames are not all tagged by the same
            field. Reading them would mean trying each variant in turn and
            reporting the last failure, which is worse than refusing.
    """
    inbound = stream.inbound
    outbound = stream.outbound
    inbound_tag = _tag_field(inbound)
    if inbound and inbound_tag is None:
        raise ValueError(
            "The inbound frames are not tagged by a single shared field, so "
            "an incoming frame cannot be routed to its type. Give every frame "
            "the server sends the same discriminant field."
        )

    client_union = f"{stream.class_name}ClientFrame"
    server_union = f"{stream.class_name}ServerFrame"
    payloads = sorted({m.class_name for m in (*inbound, *outbound)})

    lines: list[str] = [
        f'"""Typed WebSocket client generated from the {stream.title} '
        f"AsyncAPI document.",
        "",
        "Do not edit by hand — rerun the generator to refresh.",
        "",
    ]
    where = stream.channel.address or "the channel the document declares"
    lines.extend(
        _wrap(
            f"The connection is {where}. Directions are the client's: `send` "
            f"puts a frame on the wire, and iterating the stream yields what "
            f"the server sends.",
            "",
            hanging=False,
        )
    )
    lines.extend(['"""', "", "from __future__ import annotations", ""])
    lines.extend(
        [
            "import json",
            "from collections.abc import AsyncIterator, Mapping",
            "from types import TracebackType",
            "from typing import Any",
            "",
            "from websockets.asyncio.client import ClientConnection, connect",
            "",
            f"from .{schemas_module} import (",
        ]
    )
    for payload in payloads:
        lines.append(f"    {payload},")
    lines.extend([")", ""])

    if stream.default_url:
        lines.extend(
            [
                f"DEFAULT_URL: str = {_string_literal(stream.default_url)}",
                '"""Connection URL the document\'s first server entry declares."""',
                "",
            ]
        )

    lines.extend(_union(client_union, outbound))
    lines.extend(_union(server_union, inbound))
    lines.extend(_dispatch("_INBOUND_FRAMES", inbound, server_union))
    lines.append("")

    lines.extend(_error_class(stream))
    lines.append("")
    lines.extend(_stream_class(stream, client_union, server_union, inbound_tag))

    exported = [
        f'    "{name}",'
        for name in sorted(
            {
                stream.class_name,
                f"{stream.class_name}FrameError",
                client_union,
                server_union,
                *(["DEFAULT_URL"] if stream.default_url else []),
            }
        )
    ]
    lines.extend(["", "", "__all__: list[str] = ["])
    lines.extend(exported)
    lines.append("]")
    return "\n".join(lines).rstrip("\n") + "\n"


def _error_class(stream: StreamIR) -> list[str]:
    """Render the exception raised for a frame the document does not declare.

    Args:
        stream (StreamIR): The parsed stream.

    Returns:
        list[str]: Source lines.
    """
    name = f"{stream.class_name}FrameError"
    return [
        "",
        f"class {name}(ValueError):",
        '    """Raised for an inbound frame the document does not declare.',
        "",
        "    Carries the payload so a caller can log what actually arrived. A",
        "    server that grows a frame the checked-in document predates is the",
        "    common cause, and the fix is to regenerate.",
        '    """',
        "",
        "    def __init__(self, tag: object, payload: Any) -> None:",
        '        """Initialize.',
        "",
        "        Args:",
        "            tag (object): The discriminant value that matched nothing.",
        "            payload (Any): The decoded frame.",
        '        """',
        '        super().__init__(f"unknown inbound frame {tag!r}")',
        "        self.tag: object = tag",
        "        self.payload: Any = payload",
    ]


def _stream_class(
    stream: StreamIR,
    client_union: str,
    server_union: str,
    inbound_tag: str | None,
) -> list[str]:
    """Render the client class itself.

    Args:
        stream (StreamIR): The parsed stream.
        client_union (str): Alias for the frames the client sends.
        server_union (str): Alias for the frames the client receives.
        inbound_tag (str | None): Field the inbound frames are tagged by.

    Returns:
        list[str]: Source lines.
    """
    headers = stream.channel.handshake_headers
    arguments = ["self", "*"]
    if stream.default_url:
        arguments.append("url: str = DEFAULT_URL")
    else:
        arguments.append("url: str")
    for header, required in headers:
        argument = _header_argument(header)
        arguments.append(
            f"{argument}: str" if required else f"{argument}: str | None = None"
        )
    arguments.append("extra_headers: Mapping[str, str] | None = None")

    lines: list[str] = ["", f"class {stream.class_name}:"]
    lines.extend(
        _wrap(f"Typed client for {stream.title}.", "    ", '"""', hanging=False)
    )
    if stream.channel.description:
        lines.append("")
        lines.extend(_wrap(stream.channel.description, "    ", hanging=False))
    lines.extend(
        [
            "",
            "    Use it as an async context manager; iterating yields the frames",
            "    the server sends, already parsed into their generated types.",
            '    """',
            "",
            "    def __init__(",
        ]
    )
    for argument in arguments:
        lines.append(f"        {argument}," if argument != "*" else "        *,")
    lines.extend(
        [
            "    ) -> None:",
            '        """Initialize.',
            "",
            "        Args:",
        ]
    )
    lines.extend(
        _wrap(
            "Connection URL.",
            "            ",
            "url (str): ",
        )
    )
    for header, required in headers:
        argument = _header_argument(header)
        suffix = "" if required else " Optional."
        lines.extend(
            _wrap(
                f"Sent as the `{header}` header on the upgrade.{suffix}",
                "            ",
                f"{argument} (str): " if required else f"{argument} (str | None): ",
            )
        )
    lines.extend(
        _wrap(
            "Extra headers merged onto the upgrade request.",
            "            ",
            "extra_headers (Mapping[str, str] | None): ",
        )
    )
    lines.extend(
        [
            '        """',
            "        self.url: str = url",
            "        headers: dict[str, str] = {}",
        ]
    )
    for header, required in headers:
        argument = _header_argument(header)
        key = _string_literal(header)
        if required:
            lines.append(f"        headers[{key}] = {argument}")
        else:
            lines.extend(
                [
                    f"        if {argument} is not None:",
                    f"            headers[{key}] = {argument}",
                ]
            )
    lines.extend(
        [
            "        if extra_headers is not None:",
            "            headers.update(extra_headers)",
            "        self.headers: dict[str, str] = headers",
            "        self._socket: ClientConnection | None = None",
            "",
            "    async def __aenter__(self) -> " + stream.class_name + ":",
            '        """Open the connection.',
            "",
            "        Returns:",
            f"            {stream.class_name}: This client, connected.",
            '        """',
            "        self._socket = await connect(",
            "            self.url, additional_headers=self.headers",
            "        )",
            "        return self",
            "",
            "    async def __aexit__(",
            "        self,",
            "        exc_type: type[BaseException] | None,",
            "        exc: BaseException | None,",
            "        traceback: TracebackType | None,",
            "    ) -> None:",
            '        """Close the connection.',
            "",
            "        Args:",
            "            exc_type (type[BaseException] | None): Raised type, if any.",
            "            exc (BaseException | None): Raised exception, if any.",
            "            traceback (TracebackType | None): Its traceback, if any.",
            '        """',
            "        if self._socket is not None:",
            "            await self._socket.close()",
            "            self._socket = None",
            "",
            "    @property",
            "    def socket(self) -> ClientConnection:",
            '        """The live connection.',
            "",
            "        Returns:",
            "            ClientConnection: The open socket.",
            "",
            "        Raises:",
            "            RuntimeError: When used outside the context manager.",
            '        """',
            "        if self._socket is None:",
            "            raise RuntimeError(",
            f'                "{stream.class_name} is not connected — use it as an "',
            '                "async context manager."',
            "            )",
            "        return self._socket",
        ]
    )

    if stream.outbound:
        lines.extend(
            [
                "",
                f"    async def send(self, frame: {client_union}) -> None:",
                '        """Put one frame on the wire.',
                "",
                "        Args:",
                f"            frame ({client_union}): The frame to send.",
                '        """',
                '        payload = frame.model_dump(mode="json", by_alias=True)',
                "        await self.socket.send(json.dumps(payload))",
            ]
        )

    if stream.inbound and inbound_tag is not None:
        tag = _string_literal(inbound_tag)
        lines.extend(
            [
                "",
                f"    async def receive(self) -> {server_union}:",
                '        """Read the next frame the server sends.',
                "",
                "        Returns:",
                f"            {server_union}: The parsed frame.",
                "",
                "        Raises:",
                f"            {stream.class_name}FrameError: For a frame whose",
                f"                `{inbound_tag}` the document does not declare.",
                '        """',
                "        raw: Any = json.loads(await self.socket.recv())",
                f"        tag = raw.get({tag}) if isinstance(raw, dict) else None",
                "        model = (",
                "            _INBOUND_FRAMES.get(tag)",
                "            if isinstance(tag, str)",
                "            else None",
                "        )",
                "        if model is None:",
                f"            raise {stream.class_name}FrameError(tag, raw)",
                "        return model.model_validate(raw)",
                "",
                f"    async def __aiter__(self) -> AsyncIterator[{server_union}]:",
                '        """Iterate the frames the server sends.',
                "",
                "        Yields:",
                f"            {server_union}: Each parsed frame, until the socket",
                "                closes.",
                '        """',
                "        async for _ in self.socket:",
                "            raw: Any = json.loads(_)",
                f"            tag = raw.get({tag}) if isinstance(raw, dict) else None",
                "            model = (",
                "                _INBOUND_FRAMES.get(tag)",
                "                if isinstance(tag, str)",
                "                else None",
                "            )",
                "            if model is None:",
                f"                raise {stream.class_name}FrameError(tag, raw)",
                "            yield model.model_validate(raw)",
            ]
        )

    notes = _unsupported_comment((), "    ")
    lines.extend(notes)
    return lines


__all__: list[str] = [
    "emit_stream",
]
