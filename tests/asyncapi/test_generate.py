"""End to end: a document becomes a client that talks to a real socket.

The unit tests above pin the parser's decisions. This one pins the outcome a
consumer sees — the generated package is imported, connected to a WebSocket
server running in-process, and driven. Everything between the document and
the wire is exercised, including the direction inversion, which is the one
mistake that would still compile and still type-check.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from websockets.asyncio.server import ServerConnection, serve

from tempest_fastapi_sdk.asyncapi.generate import generate_stream


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate the package from the shared document.

    Args:
        tmp_path_factory (pytest.TempPathFactory): pytest's factory.

    Returns:
        Path: The directory holding the generated package.
    """
    from tests.asyncapi.conftest import _document

    root = tmp_path_factory.mktemp("stream")
    spec = root / "chat.json"
    spec.write_text(json.dumps(_document()), encoding="utf-8")
    generate_stream(spec.as_posix(), out=root / "pkg" / "chat_ws", name="chat")
    return root / "pkg"


@pytest.fixture(scope="module")
def package(generated: Path) -> ModuleType:
    """Import the generated package.

    Args:
        generated (Path): Directory holding it.

    Returns:
        ModuleType: The imported package.
    """
    root = str(generated)
    if root not in sys.path:
        sys.path.insert(0, root)
    for stale in [name for name in sys.modules if name.startswith("chat_ws")]:
        del sys.modules[stale]
    return importlib.import_module("chat_ws")


class TestTheGeneratedPackage:
    """Three files, importable, exporting what the document declared."""

    def test_it_writes_the_three_modules(self, generated: Path) -> None:
        """Payloads, client, barrel.

        Args:
            generated (Path): Directory holding the package.
        """
        assert sorted(p.name for p in (generated / "chat_ws").glob("*.py")) == [
            "__init__.py",
            "schemas.py",
            "stream.py",
        ]

    def test_the_client_and_both_unions_are_exported(self, package: ModuleType) -> None:
        """A consumer imports from the package, not from inside it.

        Args:
            package (ModuleType): The generated package.
        """
        for name in (
            "ChatStream",
            "ChatStreamClientFrame",
            "ChatStreamServerFrame",
            "ChatStreamFrameError",
            "DEFAULT_URL",
        ):
            assert name in package.__all__, name
            assert hasattr(package, name), name

    def test_the_output_passes_ruff(self, generated: Path) -> None:
        """Generated source a formatter would rewrite is unstable source.

        Run under this repository's configuration rather than `--isolated`:
        isolated ruff groups `tempest_fastapi_sdk` with third-party imports
        and would ask for the blank line to go, which says nothing about the
        emitter and everything about which config is in force.

        Args:
            generated (Path): Directory holding the package.
        """
        root = Path(__file__).resolve().parents[2]
        for arguments in (
            ["ruff", "check", "--select", "E,F,I,W", str(generated)],
            ["ruff", "format", "--check", str(generated)],
        ):
            completed = subprocess.run(
                arguments, cwd=root, capture_output=True, text=True, check=False
            )
            assert completed.returncode == 0, completed.stdout + completed.stderr


class TestItTalksToARealSocket:
    """The client is only worth what reaches the wire."""

    def _run(self, package: ModuleType, handler: Any) -> list[Any]:
        """Serve `handler` and drive the generated client against it.

        Args:
            package (ModuleType): The generated package.
            handler (Any): The server coroutine, taking a connection.

        Returns:
            list[Any]: Whatever the client collected.
        """
        collected: list[Any] = []

        async def run() -> None:
            async with serve(handler, "127.0.0.1", 0) as server:
                port = next(iter(server.sockets)).getsockname()[1]
                url = f"ws://127.0.0.1:{port}/ws"
                async with package.ChatStream(url=url, x_api_key="k") as stream:
                    await stream.send(package.JoinFrame(action="join", room="lobby"))
                    collected.append(await stream.receive())

        asyncio.run(run())
        return collected

    def test_a_sent_frame_reaches_the_server_as_json(self, package: ModuleType) -> None:
        """`send` serializes the model, wire names and all.

        Args:
            package (ModuleType): The generated package.
        """
        seen: list[Any] = []

        async def handler(connection: ServerConnection) -> None:
            """Echo one frame back after recording it.

            Args:
                connection (ServerConnection): The accepted socket.
            """
            seen.append(json.loads(await connection.recv()))
            await connection.send(json.dumps({"type": "message", "text": "hi"}))

        self._run(package, handler)
        assert seen == [{"action": "join", "room": "lobby"}]

    def test_the_handshake_header_is_sent(self, package: ModuleType) -> None:
        """The document's header becomes a required argument, and travels.

        Args:
            package (ModuleType): The generated package.
        """
        seen: list[str | None] = []

        async def handler(connection: ServerConnection) -> None:
            """Record the upgrade header, then answer.

            Args:
                connection (ServerConnection): The accepted socket.
            """
            seen.append(connection.request.headers.get("x-api-key"))
            await connection.recv()
            await connection.send(json.dumps({"type": "message", "text": "hi"}))

        self._run(package, handler)
        assert seen == ["k"]

    def test_an_inbound_frame_is_parsed_into_its_type(
        self, package: ModuleType
    ) -> None:
        """The tag routes it to the generated class, not to a dict.

        Args:
            package (ModuleType): The generated package.
        """

        async def handler(connection: ServerConnection) -> None:
            """Answer with a `message` frame.

            Args:
                connection (ServerConnection): The accepted socket.
            """
            await connection.recv()
            await connection.send(json.dumps({"type": "message", "text": "hi"}))

        received = self._run(package, handler)[0]
        assert isinstance(received, package.MessageFrame)
        assert received.text == "hi"

    def test_an_undeclared_frame_raises_with_its_tag(self, package: ModuleType) -> None:
        """A server ahead of the checked-in document fails loudly.

        Args:
            package (ModuleType): The generated package.
        """

        async def handler(connection: ServerConnection) -> None:
            """Answer with a frame the document does not declare.

            Args:
                connection (ServerConnection): The accepted socket.
            """
            await connection.recv()
            await connection.send(json.dumps({"type": "typing", "who": "ana"}))

        with pytest.raises(package.ChatStreamFrameError) as caught:
            self._run(package, handler)
        assert caught.value.tag == "typing"
        assert caught.value.payload == {"type": "typing", "who": "ana"}


class TestTheDirectionSurvivesToTheWire:
    """The inversion is what an end-to-end run can actually catch."""

    def test_the_client_sends_what_the_document_marks_receive(
        self, package: ModuleType
    ) -> None:
        """`JoinFrame` is `action: receive` in the document — the server
        receives it — so the client must be able to send it, and must not
        expect it back.

        Reading `action` straight through would put `JoinFrame` in the
        inbound union and `MessageFrame` in the outbound one, and both
        `send` calls would still type-check.

        Args:
            package (ModuleType): The generated package.
        """
        import typing

        def variants(alias: Any) -> tuple[Any, ...]:
            """Members of a union alias, or the single class it collapsed to.

            Args:
                alias (Any): The generated alias.

            Returns:
                tuple[Any, ...]: Its variants.
            """
            return typing.get_args(alias) or (alias,)

        outbound = variants(package.ChatStreamClientFrame)
        inbound = variants(package.ChatStreamServerFrame)
        assert package.JoinFrame in outbound
        assert package.JoinFrame not in inbound
        assert package.MessageFrame in inbound
        assert package.MessageFrame not in outbound
