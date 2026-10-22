"""A plaintext SMTP server small enough to drive real sockets in tests."""

from __future__ import annotations

import asyncio


class TinySMTP:
    """A real SMTP server, small enough to live in a test file.

    The bulk path is about the *protocol*: how many connections it opens,
    and what it does with a per-recipient refusal. A monkeypatched
    `aiosmtplib.send` can answer neither, so this speaks the handful of
    verbs aiosmtplib needs over a real socket and counts what arrives.
    """

    def __init__(self, refuse: dict[str, tuple[int, str]] | None = None) -> None:
        """Initialize.

        Args:
            refuse (dict[str, tuple[int, str]] | None): Addresses to answer
                with ``(code, message)`` at ``RCPT TO`` instead of ``250``.
        """
        self.refuse: dict[str, tuple[int, str]] = refuse or {}
        self.delivered: list[str] = []
        self.connections: int = 0
        self.server: asyncio.AbstractServer | None = None
        self.port: int = 0

    async def start(self) -> int:
        """Listen on an ephemeral port.

        Returns:
            int: The port the server bound to.
        """
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = int(self.server.sockets[0].getsockname()[1])
        return self.port

    async def stop(self) -> None:
        """Close the listening socket."""
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Serve one connection until QUIT.

        Args:
            reader (asyncio.StreamReader): Client stream.
            writer (asyncio.StreamWriter): Server stream.
        """
        self.connections += 1
        writer.write(b"220 tiny ESMTP\r\n")
        await writer.drain()
        pending: list[str] = []
        while True:
            line = await reader.readline()
            if not line:
                break
            command = line.decode("utf-8", "replace").strip()
            upper = command.upper()
            if upper.startswith(("HELO", "EHLO")):
                writer.write(b"250-tiny\r\n250 SIZE 10240000\r\n")
            elif upper.startswith("MAIL FROM"):
                writer.write(b"250 OK\r\n")
            elif upper.startswith("RCPT TO"):
                address = command.split("<", 1)[-1].split(">", 1)[0]
                if address in self.refuse:
                    code, text = self.refuse[address]
                    writer.write(f"{code} {text}\r\n".encode())
                else:
                    pending.append(address)
                    writer.write(b"250 OK\r\n")
            elif upper == "DATA":
                writer.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                await writer.drain()
                while True:
                    chunk = await reader.readline()
                    if chunk in (b".\r\n", b".\n", b""):
                        break
                self.delivered.extend(pending)
                pending = []
                writer.write(b"250 Message accepted\r\n")
            elif upper == "QUIT":
                writer.write(b"221 Bye\r\n")
                await writer.drain()
                break
            elif upper == "RSET":
                pending = []
                writer.write(b"250 OK\r\n")
            else:
                writer.write(b"250 OK\r\n")
            await writer.drain()
        writer.close()


__all__: list[str] = ["TinySMTP"]
