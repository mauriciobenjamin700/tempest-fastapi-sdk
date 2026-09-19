"""The file surface reads and writes real files, confined to the allowed bases."""

from pathlib import Path

import pytest

from tempest_fastapi_sdk.hostbridge import (
    FileDeleteSchema,
    FileWriteSchema,
    HostBridge,
    HostBridgeConfig,
    HostFileDecodeError,
    HostFileNotFoundError,
    HostFileTooLargeError,
    InvalidHostPathError,
)


class TestReadText:
    """Reading answers with the file, or names why it will not."""

    async def test_returns_content_and_size(
        self, bridge: HostBridge, tmp_path: Path
    ) -> None:
        """A readable file comes back decoded, with its size on disk."""
        target = tmp_path / "notes.txt"
        target.write_text("olá mundo", encoding="utf-8")
        result = await bridge.read_text(str(target))
        assert result.content == "olá mundo"
        assert result.size_bytes == target.stat().st_size
        assert result.path == str(target)

    async def test_missing_file_is_not_found(
        self, bridge: HostBridge, tmp_path: Path
    ) -> None:
        """A path that is allowed but empty is a 404, not a 400."""
        with pytest.raises(HostFileNotFoundError):
            await bridge.read_text(str(tmp_path / "gone.txt"))

    async def test_a_directory_is_not_a_file(
        self, bridge: HostBridge, tmp_path: Path
    ) -> None:
        """Pointing the text reader at a directory fails the same way."""
        with pytest.raises(HostFileNotFoundError):
            await bridge.read_text(str(tmp_path))

    async def test_outside_the_base_is_refused(self, bridge: HostBridge) -> None:
        """Confinement applies before anything touches the disk."""
        with pytest.raises(InvalidHostPathError):
            await bridge.read_text("/etc/passwd")

    async def test_oversized_file_names_the_limit(self, tmp_path: Path) -> None:
        """The refusal carries the size and the cap, so it is actionable."""
        target = tmp_path / "big.txt"
        target.write_text("x" * 100)
        bridge = HostBridge(
            HostBridgeConfig(
                allowed_base_paths=(str(tmp_path),), max_file_read_bytes=10
            )
        )
        with pytest.raises(HostFileTooLargeError) as excinfo:
            await bridge.read_text(str(target))
        assert excinfo.value.message_params["size"] == 100
        assert excinfo.value.message_params["limit"] == 10

    async def test_binary_content_is_a_client_error(
        self, bridge: HostBridge, tmp_path: Path
    ) -> None:
        """Undecodable bytes mean a PDF or an image was passed to a text reader.

        Letting the ``UnicodeDecodeError`` escape turned this into an opaque
        500 for something the caller can fix.
        """
        target = tmp_path / "scan.pdf"
        target.write_bytes(b"%PDF-1.7\n\xff\xfe\x00binary")
        with pytest.raises(HostFileDecodeError) as excinfo:
            await bridge.read_text(str(target))
        assert excinfo.value.message_params["encoding"] == "utf-8"

    async def test_unknown_encoding_is_a_client_error(
        self, bridge: HostBridge, tmp_path: Path
    ) -> None:
        """An encoding Python does not know is the caller's mistake, not a crash."""
        target = tmp_path / "notes.txt"
        target.write_text("hello")
        with pytest.raises(HostFileDecodeError):
            await bridge.read_text(str(target), encoding="not-a-codec")


class TestWriteText:
    """Writing creates, overwrites or appends, and confines like everything else."""

    async def test_creates_the_file_and_its_parents(
        self, bridge: HostBridge, tmp_path: Path
    ) -> None:
        """Missing directories are created when the caller asks."""
        target = tmp_path / "deep" / "nested" / "out.txt"
        await bridge.write_text(FileWriteSchema(path=str(target), content="hi"))
        assert target.read_text() == "hi"

    async def test_overwrites_by_default(
        self, bridge: HostBridge, tmp_path: Path
    ) -> None:
        """A second write replaces the first."""
        target = tmp_path / "out.txt"
        await bridge.write_text(FileWriteSchema(path=str(target), content="first"))
        await bridge.write_text(FileWriteSchema(path=str(target), content="second"))
        assert target.read_text() == "second"

    async def test_appends_when_asked(self, bridge: HostBridge, tmp_path: Path) -> None:
        """``append`` keeps what was there."""
        target = tmp_path / "log.txt"
        await bridge.write_text(FileWriteSchema(path=str(target), content="a"))
        await bridge.write_text(
            FileWriteSchema(path=str(target), content="b", append=True)
        )
        assert target.read_text() == "ab"

    async def test_outside_the_base_is_refused(self, bridge: HostBridge) -> None:
        """A write cannot reach outside the allowed bases either."""
        with pytest.raises(InvalidHostPathError):
            await bridge.write_text(
                FileWriteSchema(path="/etc/cron.d/evil", content="x")
            )


class TestListDir:
    """A listing is sorted, survivable, and empty when the directory is."""

    async def test_lists_directories_first_then_names(
        self, bridge: HostBridge, tmp_path: Path
    ) -> None:
        """Order is directories first, then case-insensitive by name."""
        (tmp_path / "Zeta").mkdir()
        (tmp_path / "alpha.txt").write_text("a")
        (tmp_path / "Beta.txt").write_text("b")
        listing = await bridge.list_dir(str(tmp_path))
        assert [entry.name for entry in listing.entries] == [
            "Zeta",
            "alpha.txt",
            "Beta.txt",
        ]

    async def test_an_empty_directory_is_success(
        self, bridge: HostBridge, tmp_path: Path
    ) -> None:
        """Nothing to list is an empty list, never a 404."""
        empty = tmp_path / "empty"
        empty.mkdir()
        assert (await bridge.list_dir(str(empty))).entries == []

    async def test_a_broken_entry_is_skipped(
        self, bridge: HostBridge, tmp_path: Path
    ) -> None:
        """A dangling symlink must not fail the whole listing."""
        (tmp_path / "real.txt").write_text("x")
        (tmp_path / "dangling").symlink_to(tmp_path / "does-not-exist")
        listing = await bridge.list_dir(str(tmp_path))
        assert [entry.name for entry in listing.entries] == ["real.txt"]

    async def test_a_file_is_not_a_directory(
        self, bridge: HostBridge, tmp_path: Path
    ) -> None:
        """Listing a file is a not-found, matching the read path's shape."""
        target = tmp_path / "file.txt"
        target.write_text("x")
        with pytest.raises(HostFileNotFoundError):
            await bridge.list_dir(str(target))


class TestDelete:
    """Deleting removes one thing, and never a tree."""

    async def test_removes_a_file(self, bridge: HostBridge, tmp_path: Path) -> None:
        """The file is gone afterwards."""
        target = tmp_path / "x.txt"
        target.write_text("x")
        await bridge.delete(FileDeleteSchema(path=str(target)))
        assert not target.exists()

    async def test_missing_is_an_error_by_default(
        self, bridge: HostBridge, tmp_path: Path
    ) -> None:
        """Deleting nothing is reported, so a caller is not told it worked."""
        with pytest.raises(HostFileNotFoundError):
            await bridge.delete(FileDeleteSchema(path=str(tmp_path / "gone")))

    async def test_missing_ok_succeeds(
        self, bridge: HostBridge, tmp_path: Path
    ) -> None:
        """``missing_ok`` makes the call idempotent."""
        assert await bridge.delete(
            FileDeleteSchema(path=str(tmp_path / "gone"), missing_ok=True)
        )

    async def test_a_populated_directory_is_not_erased(
        self, bridge: HostBridge, tmp_path: Path
    ) -> None:
        """A non-empty directory raises rather than being removed recursively.

        Erasing a tree as a side effect of a delete call is the accident
        this refuses to make possible.
        """
        populated = tmp_path / "keep"
        populated.mkdir()
        (populated / "inside.txt").write_text("x")
        with pytest.raises(OSError):
            await bridge.delete(FileDeleteSchema(path=str(populated)))
        assert (populated / "inside.txt").exists()


class TestLiteralContent:
    """Content read off a disk or a pipe survives the schema unchanged."""

    async def test_a_trailing_newline_is_preserved(
        self, bridge: HostBridge, tmp_path: Path
    ) -> None:
        """A file ending in a newline reads back with it.

        ``BaseSchema`` strips whitespace from strings, which would quietly
        rewrite the file the moment the content was written back.
        """
        target = tmp_path / "config.ini"
        target.write_text("[core]\nvalue = 1\n")
        result = await bridge.read_text(str(target))
        assert result.content == "[core]\nvalue = 1\n"

    async def test_a_round_trip_does_not_change_the_file(
        self, bridge: HostBridge, tmp_path: Path
    ) -> None:
        """Reading a file and writing it back leaves the bytes alone."""
        target = tmp_path / "script.sh"
        original = "#!/bin/sh\n\n  echo hi\n\n"
        target.write_text(original)
        content = (await bridge.read_text(str(target))).content
        await bridge.write_text(FileWriteSchema(path=str(target), content=content))
        assert target.read_text() == original
