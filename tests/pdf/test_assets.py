"""Tests for what a template is allowed to load.

This is the module's security boundary, so the tests are written from
the attacker's side: every one of them is a way to reach a file or a
host the policy did not name.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from tempest_fastapi_sdk.pdf.assets import (
    AssetPolicy,
    AssetRefused,
    build_url_fetcher,
)

TINY_PNG: bytes = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
)


@pytest.fixture
def assets_dir(tmp_path: Path) -> Path:
    """Create an allowed asset directory holding one image.

    Args:
        tmp_path (Path): pytest temporary directory.

    Returns:
        Path: The directory to allow.
    """
    directory = tmp_path / "assets"
    directory.mkdir()
    (directory / "logo.png").write_bytes(TINY_PNG)
    return directory


class TestDefaultPolicy:
    def test_denies_local_files(self, tmp_path: Path) -> None:
        """With no allowed directory, nothing local is reachable."""
        fetch = build_url_fetcher(AssetPolicy())
        secret = tmp_path / "secret.txt"
        secret.write_text("token")
        with pytest.raises(AssetRefused):
            fetch(secret.as_uri())

    def test_denies_remote(self) -> None:
        """A remote fetch is a request made from inside the network."""
        fetch = build_url_fetcher(AssetPolicy())
        with pytest.raises(AssetRefused, match="remote assets"):
            fetch("http://169.254.169.254/latest/meta-data/")

    def test_denies_unknown_schemes(self) -> None:
        """Anything not explicitly handled is refused, not attempted."""
        fetch = build_url_fetcher(AssetPolicy())
        with pytest.raises(AssetRefused, match="scheme"):
            fetch("ftp://example.com/logo.png")

    def test_allows_data_uris(self) -> None:
        """A data URI carries its own bytes and fetches nothing."""
        fetch = build_url_fetcher(AssetPolicy())
        encoded = base64.b64encode(TINY_PNG).decode()
        result = fetch(f"data:image/png;base64,{encoded}")
        assert result.read() == TINY_PNG


class TestAllowedDirectories:
    def test_serves_a_file_inside_the_allowed_directory(
        self,
        assets_dir: Path,
    ) -> None:
        """The whole point of naming a directory."""
        fetch = build_url_fetcher(AssetPolicy(allow_dirs=(assets_dir,)))
        result = fetch((assets_dir / "logo.png").as_uri())
        assert result.read() == TINY_PNG
        assert result.content_type == "image/png"

    def test_refuses_traversal_out_of_it(
        self,
        assets_dir: Path,
        tmp_path: Path,
    ) -> None:
        """``../`` must not reach a sibling the policy never named."""
        secret = tmp_path / "secret.txt"
        secret.write_text("token")
        fetch = build_url_fetcher(AssetPolicy(allow_dirs=(assets_dir,)))
        traversal = (assets_dir / ".." / "secret.txt").as_uri()
        with pytest.raises(AssetRefused, match="outside every allowed"):
            fetch(traversal)

    def test_refuses_a_symlink_pointing_outside(
        self,
        assets_dir: Path,
        tmp_path: Path,
    ) -> None:
        """The check is on the resolved path, not the one requested.

        A link inside the allowed directory is the cheapest way past a
        naive string-prefix comparison.
        """
        secret = tmp_path / "secret.txt"
        secret.write_text("token")
        link = assets_dir / "escape.txt"
        link.symlink_to(secret)
        fetch = build_url_fetcher(AssetPolicy(allow_dirs=(assets_dir,)))
        with pytest.raises(AssetRefused, match="outside every allowed"):
            fetch(link.as_uri())

    def test_refuses_a_missing_file(self, assets_dir: Path) -> None:
        """An absent asset is reported, not rendered as a hole."""
        fetch = build_url_fetcher(AssetPolicy(allow_dirs=(assets_dir,)))
        with pytest.raises(AssetRefused, match="does not exist"):
            fetch((assets_dir / "nope.png").as_uri())

    def test_refuses_a_file_host(self, assets_dir: Path) -> None:
        """``file://host/path`` is a UNC path, not a local one."""
        fetch = build_url_fetcher(AssetPolicy(allow_dirs=(assets_dir,)))
        with pytest.raises(AssetRefused, match="not local"):
            fetch("file://evil.example/etc/passwd")

    def test_enforces_the_size_ceiling(self, assets_dir: Path) -> None:
        """A huge asset is an outage per concurrent request."""
        big = assets_dir / "big.bin"
        big.write_bytes(b"x" * 2048)
        fetch = build_url_fetcher(AssetPolicy(allow_dirs=(assets_dir,), max_bytes=1024))
        with pytest.raises(AssetRefused, match="larger than"):
            fetch(big.as_uri())

    def test_a_missing_allowed_directory_fails_at_construction(
        self,
        tmp_path: Path,
    ) -> None:
        """A typo would otherwise read as 'nothing is allowed'."""
        with pytest.raises(ValueError, match="not a directory"):
            AssetPolicy(allow_dirs=(tmp_path / "absent",))


class TestRefusalCollection:
    def test_refusals_accumulate_and_can_be_taken(self) -> None:
        """The renderer reads these to turn a silent hole into an error."""
        policy = AssetPolicy()
        fetch = build_url_fetcher(policy)
        for url in ("http://a.test/1.png", "http://b.test/2.png"):
            with pytest.raises(AssetRefused):
                fetch(url)
        taken = policy.take_refusals()
        assert len(taken) == 2
        assert "a.test" in taken[0]
        assert policy.take_refusals() == []
