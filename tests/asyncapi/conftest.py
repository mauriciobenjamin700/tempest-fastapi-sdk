"""Fixtures shared by the AsyncAPI tests."""

from __future__ import annotations

import json
from typing import Any

import pytest


def _document(**overrides: Any) -> dict[str, Any]:
    """Build a minimal AsyncAPI 3.0 document describing one socket.

    Args:
        **overrides (Any): Root keys to replace.

    Returns:
        dict[str, Any]: The document.
    """
    document: dict[str, Any] = {
        "asyncapi": "3.0.0",
        "x-tempest-perspective": "server",
        "info": {"title": "Chat", "version": "1.0.0"},
        "servers": {
            "local": {"host": "127.0.0.1:9", "protocol": "ws", "pathname": "/ws"}
        },
        "defaultContentType": "application/json",
        "channels": {
            "socket": {
                "address": "/ws",
                "title": "Realtime connection",
                "messages": {
                    "JoinFrame": {"$ref": "#/components/messages/JoinFrame"},
                    "MessageFrame": {"$ref": "#/components/messages/MessageFrame"},
                },
                "bindings": {
                    "ws": {
                        "bindingVersion": "0.1.0",
                        "method": "GET",
                        "headers": {
                            "type": "object",
                            "properties": {"x-api-key": {"type": "string"}},
                            "required": ["x-api-key"],
                        },
                    }
                },
            }
        },
        "operations": {
            "join": {
                "action": "receive",
                "channel": {"$ref": "#/channels/socket"},
                "messages": [{"$ref": "#/channels/socket/messages/JoinFrame"}],
            },
            "onMessage": {
                "action": "send",
                "channel": {"$ref": "#/channels/socket"},
                "messages": [{"$ref": "#/channels/socket/messages/MessageFrame"}],
            },
        },
        "components": {
            "messages": {
                "JoinFrame": {
                    "name": "JoinFrame",
                    "contentType": "application/json",
                    "payload": {"$ref": "#/components/schemas/JoinFrame"},
                },
                "MessageFrame": {
                    "name": "MessageFrame",
                    "contentType": "application/json",
                    "payload": {"$ref": "#/components/schemas/MessageFrame"},
                },
            },
            "schemas": {
                "JoinFrame": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["join"]},
                        "room": {"type": "string", "maxLength": 64},
                    },
                    "required": ["action", "room"],
                },
                "MessageFrame": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["message"]},
                        "text": {"type": "string"},
                    },
                    "required": ["type", "text"],
                },
            },
        },
    }
    document.update(overrides)
    return document


@pytest.fixture
def document() -> dict[str, Any]:
    """A minimal, valid AsyncAPI 3.0 document.

    Returns:
        dict[str, Any]: The document.
    """
    return _document()


@pytest.fixture
def document_factory() -> Any:
    """Build a document with root keys replaced.

    Returns:
        Any: The `_document` builder.
    """
    return _document


@pytest.fixture
def document_file(tmp_path: Any, document: dict[str, Any]) -> Any:
    """Write the document to disk and return its path.

    Args:
        tmp_path (Any): pytest's temporary directory.
        document (dict[str, Any]): The document.

    Returns:
        Any: Path to the written JSON.
    """
    path = tmp_path / "chat.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path
