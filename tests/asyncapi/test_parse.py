"""The parser speaks one point of view: the generated client's.

AsyncAPI's `action` is relative to whoever published the document. Every
document this generator reads was published by the server, so `receive`
there means the client sends. Inverting once, here, is what keeps the
emitter and every reader of the IR from having to remember whose `action`
they are holding — and the inversion is the single place a sign error would
produce a client that compiles and does the opposite.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from tempest_fastapi_sdk.asyncapi import parse_asyncapi
from tempest_fastapi_sdk.openapi.loader import SpecError


class TestDirection:
    """The document's point of view is inverted into the client's."""

    def test_a_server_receive_is_a_client_send(self, document: dict[str, Any]) -> None:
        """Regression: reading `action` straight through reverses the client.

        Args:
            document (dict[str, Any]): A valid document.
        """
        parsed = parse_asyncapi(document, client_name="chat")
        join = next(o for o in parsed.stream.operations if o.name == "join")
        assert join.direction == "outbound"

    def test_a_server_send_is_a_client_receive(self, document: dict[str, Any]) -> None:
        """The other half of the same inversion.

        Args:
            document (dict[str, Any]): A valid document.
        """
        parsed = parse_asyncapi(document, client_name="chat")
        received = next(o for o in parsed.stream.operations if o.name == "onMessage")
        assert received.direction == "inbound"

    def test_the_frames_land_on_the_right_side(self, document: dict[str, Any]) -> None:
        """The union a caller sends is not the one it receives.

        Args:
            document (dict[str, Any]): A valid document.
        """
        stream = parse_asyncapi(document, client_name="chat").stream
        assert [m.name for m in stream.outbound] == ["JoinFrame"]
        assert [m.name for m in stream.inbound] == ["MessageFrame"]

    def test_an_unknown_action_is_reported_not_guessed(
        self, document: dict[str, Any]
    ) -> None:
        """A third action would have to be assigned a direction blindly.

        Args:
            document (dict[str, Any]): A valid document to mangle.
        """
        document["operations"]["join"]["action"] = "publish"
        parsed = parse_asyncapi(document, client_name="chat")
        assert any("publish" in note for note in parsed.unsupported)
        assert [o.name for o in parsed.stream.operations] == ["onMessage"]


class TestDiscriminants:
    """A frame is told apart by the single-valued enum it declares."""

    def test_each_frame_carries_its_tag(self, document: dict[str, Any]) -> None:
        """A literal renders as a one-value enum, and that is the tag.

        Args:
            document (dict[str, Any]): A valid document.
        """
        stream = parse_asyncapi(document, client_name="chat").stream
        tags = {m.name: m.discriminant for m in stream.messages}
        assert tags["JoinFrame"] == ("action", "join")
        assert tags["MessageFrame"] == ("type", "message")

    def test_a_frame_without_a_literal_has_no_tag(
        self, document: dict[str, Any]
    ) -> None:
        """It cannot join a tagged union, and the IR says so rather than lying.

        Args:
            document (dict[str, Any]): A valid document to mangle.
        """
        document["components"]["schemas"]["MessageFrame"]["properties"]["type"] = {
            "type": "string"
        }
        stream = parse_asyncapi(document, client_name="chat").stream
        assert (
            next(m for m in stream.messages if m.name == "MessageFrame").discriminant
            is None
        )


class TestChannel:
    """WebSocket has one channel, and the handshake is part of it."""

    def test_the_address_and_handshake_are_read(self, document: dict[str, Any]) -> None:
        """The header the upgrade requires becomes a client argument.

        Args:
            document (dict[str, Any]): A valid document.
        """
        channel = parse_asyncapi(document, client_name="chat").stream.channel
        assert channel.address == "/ws"
        assert channel.handshake_headers == (("x-api-key", True),)

    def test_an_optional_header_is_marked_optional(
        self, document: dict[str, Any]
    ) -> None:
        """Required is read from the schema, not assumed.

        Args:
            document (dict[str, Any]): A valid document to mangle.
        """
        binding = document["channels"]["socket"]["bindings"]["ws"]
        binding["headers"]["required"] = []
        channel = parse_asyncapi(document, client_name="chat").stream.channel
        assert channel.handshake_headers == (("x-api-key", False),)

    def test_two_channels_are_refused(self, document: dict[str, Any]) -> None:
        """A document with several is not describing a WebSocket.

        Args:
            document (dict[str, Any]): A valid document to mangle.
        """
        document["channels"]["other"] = {"address": "/other"}
        with pytest.raises(SpecError, match="exactly one channel"):
            parse_asyncapi(document, client_name="chat")

    def test_a_document_with_no_frames_is_refused(
        self, document: dict[str, Any]
    ) -> None:
        """There would be nothing to generate.

        Args:
            document (dict[str, Any]): A valid document to mangle.
        """
        document["components"]["messages"] = {}
        with pytest.raises(SpecError, match=re.escape("no `components.messages`")):
            parse_asyncapi(document, client_name="chat")


class TestPayloads:
    """Payload classes come from the shared JSON Schema machinery."""

    def test_the_constraints_survive(self, document: dict[str, Any]) -> None:
        """A `maxLength` in the document reaches the generated model.

        This is what makes the generated client refuse a bad frame before
        it reaches the wire.

        Args:
            document (dict[str, Any]): A valid document.
        """
        parsed = parse_asyncapi(document, client_name="chat")
        join = next(s for s in parsed.schemas if s.name == "JoinFrame")
        room = next(f for f in join.fields if f.name == "room")
        assert room.constraints.get("max_length") == 64

    def test_an_inlined_payload_is_reported(self, document: dict[str, Any]) -> None:
        """Only a payload referencing `components.schemas` is modelled.

        Args:
            document (dict[str, Any]): A valid document to mangle.
        """
        document["components"]["messages"]["JoinFrame"]["payload"] = {"type": "object"}
        parsed = parse_asyncapi(document, client_name="chat")
        assert any("inlines its payload" in note for note in parsed.unsupported)
        assert [m.name for m in parsed.stream.messages] == ["MessageFrame"]


class TestServerUrl:
    """The client gets a default it can connect with."""

    def test_the_url_is_built_from_the_first_server(
        self, document: dict[str, Any]
    ) -> None:
        """Protocol, host and pathname, in that order.

        Args:
            document (dict[str, Any]): A valid document.
        """
        stream = parse_asyncapi(document, client_name="chat").stream
        assert stream.default_url == "ws://127.0.0.1:9/ws"

    def test_the_channel_address_fills_in_a_missing_pathname(
        self, document: dict[str, Any]
    ) -> None:
        """A server entry may leave the path to the channel.

        Args:
            document (dict[str, Any]): A valid document to mangle.
        """
        del document["servers"]["local"]["pathname"]
        stream = parse_asyncapi(document, client_name="chat").stream
        assert stream.default_url == "ws://127.0.0.1:9/ws"

    def test_no_servers_means_no_default(self, document: dict[str, Any]) -> None:
        """The generated client then requires a URL.

        Args:
            document (dict[str, Any]): A valid document to mangle.
        """
        del document["servers"]
        assert parse_asyncapi(document, client_name="chat").stream.default_url == ""
