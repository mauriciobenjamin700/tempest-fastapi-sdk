"""The checked-in WebSocket client is what the generator produces today.

The sibling of the zap HTTP integration's drift test, and it exists for the
same reason: the generated files are committed so a consumer does not run
codegen, which means a hand edit here survives review and vanishes at the
next regeneration. This suite makes it fail first.

One thing it pins that the HTTP one has no equivalent for: the vendored
document declares whose point of view its `action` fields record. Without
that the loader refuses it, and the refusal is deliberate — inverting the
directions by assumption produces a client that compiles and does the
opposite.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT: Path = Path(__file__).resolve().parents[4]
"""Repository root, resolved from this file."""


def _script() -> Any:
    """Import the regeneration script as a module.

    Returns:
        Any: The imported `regen_zap_ws` module.
    """
    path = REPO_ROOT / "scripts" / "regen_zap_ws.py"
    spec = importlib.util.spec_from_file_location("regen_zap_ws", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["regen_zap_ws"] = module
    spec.loader.exec_module(module)
    return module


SCRIPT: Any = _script()
PACKAGE_DIR: Path = SCRIPT.PACKAGE_DIR
SPEC_PATH: Path = SCRIPT.SPEC_PATH


class TestVendoredDocument:
    """The pinned document is present, usable, and out of the wheel."""

    def test_document_is_vendored(self) -> None:
        """Regeneration and this suite both have to work offline."""
        assert SPEC_PATH.exists(), f"missing {SPEC_PATH}"
        assert SPEC_PATH.stat().st_size > 1_000

    def test_document_is_the_bytes_that_were_generated_from(self) -> None:
        """A refresh has to update the digest in the same commit.

        Refresh with ``make zap-ws-fetch`` — which needs the gateway
        running with ``DOCS_ENABLED=true``, or ``ZAP_ASYNCAPI_URL``
        pointing at one — and paste the digest it prints into
        ``SPEC_SHA256``.
        """
        assert SCRIPT.spec_digest() == SCRIPT.SPEC_SHA256

    def test_document_is_not_in_the_wheel(self) -> None:
        """It is build-time input, not something a service loads."""
        assert REPO_ROOT / "tempest_fastapi_sdk" not in SPEC_PATH.parents

    def test_document_declares_its_perspective(self) -> None:
        """Without it the loader refuses, and it is right to.

        A document that does not say which end wrote it cannot have its
        `action` fields inverted with confidence, and a wrong inversion is
        invisible — the client still compiles.
        """
        import yaml

        document = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
        assert document["x-tempest-perspective"] == "server"


@pytest.fixture(scope="module")
def freshly_generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Regenerate the modules into a temporary directory.

    Args:
        tmp_path_factory (pytest.TempPathFactory): pytest's factory.

    Returns:
        Path: Directory holding the freshly generated modules.
    """
    destination = tmp_path_factory.mktemp("zap_ws_regen")
    SCRIPT.regenerate(destination)
    return destination


class TestGeneratedFilesAreNotStale:
    """The files on disk are what the generator produces today."""

    @pytest.mark.parametrize("filename", SCRIPT.GENERATED_FILES)
    def test_matches_byte_for_byte(
        self, freshly_generated: Path, filename: str
    ) -> None:
        """A hand edit here is reverted by the next regeneration.

        Args:
            freshly_generated (Path): The regenerated output.
            filename (str): The generated module under test.
        """
        checked_in = (PACKAGE_DIR / filename).read_text(encoding="utf-8")
        produced = (freshly_generated / filename).read_text(encoding="utf-8")
        assert checked_in == produced, (
            f"{filename} differs from `make zap-ws-regen` output — "
            f"regenerate rather than editing it by hand"
        )


class TestTheSurfaceIsReachable:
    """What the document declares is importable from the package."""

    def test_every_exported_name_resolves(self) -> None:
        """A name in `__all__` that does not exist is an import error later."""
        from tempest_fastapi_sdk.integrations.messaging import zap_ws

        for name in zap_ws.__all__:
            assert hasattr(zap_ws, name), name

    def test_the_client_and_both_unions_are_exported(self) -> None:
        """A consumer imports from the package, not from inside it."""
        from tempest_fastapi_sdk.integrations.messaging import zap_ws

        for name in (
            "ZapStream",
            "ZapStreamClientFrame",
            "ZapStreamServerFrame",
            "ZapStreamFrameError",
            "DEFAULT_URL",
        ):
            assert name in zap_ws.__all__, name

    def test_the_directions_match_the_gateway(self) -> None:
        """The frames the client sends are the ones the server receives.

        Pinned against the real protocol rather than a fixture: `subscribe`
        is something a consumer sends, and `ack` is something it gets back.
        Reversed, both would still type-check.
        """
        import typing

        from tempest_fastapi_sdk.integrations.messaging import zap_ws

        outbound = typing.get_args(zap_ws.ZapStreamClientFrame)
        inbound = typing.get_args(zap_ws.ZapStreamServerFrame)
        assert zap_ws.SubscribeFrame in outbound
        assert zap_ws.SubscribeFrame not in inbound
        assert zap_ws.AckFrame in inbound
        assert zap_ws.AckFrame not in outbound
