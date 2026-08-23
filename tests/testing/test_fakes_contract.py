"""Guards for the fakes: they have to match the seam they stand in for.

A fake whose signature drifts from its protocol is worse than no fake: the
service under test passes against it and fails against the real provider,
which is the exact failure the fake was adopted to prevent. So every fake is
compared to its seam by ``inspect.signature``, the same way
``tests/integrations/payment/test_contract.py`` compares the real adapters.

The first test is the one that keeps this file honest: a fake added to the
package without an entry in ``SEAMS`` fails here instead of shipping
unchecked.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from tempest_fastapi_sdk.genai.moderation import ModerationBackend
from tempest_fastapi_sdk.genai.rag.search import WebSearchBackend
from tempest_fastapi_sdk.genai.text import TextBackend
from tempest_fastapi_sdk.geo.geocoding import GeocodingBackend
from tempest_fastapi_sdk.geo.routing import RoutingBackend
from tempest_fastapi_sdk.integrations.payment import PixProvider
from tempest_fastapi_sdk.push.dispatcher import PushDispatcher
from tempest_fastapi_sdk.testing import fakes
from tempest_fastapi_sdk.testing.fakes import (
    FakeEmailUtils,
    FakeGeocodingBackend,
    FakeModerationBackend,
    FakePixProvider,
    FakePushDispatcher,
    FakeRoutingBackend,
    FakeTextBackend,
    FakeWebSearchBackend,
)
from tempest_fastapi_sdk.utils.email import EmailUtils

SEAMS: list[tuple[type[Any], type[Any]]] = [
    (FakePixProvider, PixProvider),
    (FakeTextBackend, TextBackend),
    (FakeModerationBackend, ModerationBackend),
    (FakePushDispatcher, PushDispatcher),
    (FakeGeocodingBackend, GeocodingBackend),
    (FakeRoutingBackend, RoutingBackend),
    (FakeWebSearchBackend, WebSearchBackend),
    (FakeEmailUtils, EmailUtils),
]
"""Every fake, beside the seam it stands in for."""


def _protocol_methods(seam: type[Any]) -> list[str]:
    """List the callables a seam requires.

    Args:
        seam (type[Any]): The protocol (or concrete class) being stood in
            for.

    Returns:
        list[str]: Public method names declared directly on the seam.
    """
    return [
        name
        for name, member in vars(seam).items()
        if not name.startswith("_") and callable(member)
    ]


def test_every_exported_fake_has_a_seam() -> None:
    """A fake added without an entry in ``SEAMS`` fails here.

    Without this, the file below silently stops covering the package the day
    someone adds the ninth fake.
    """
    exported = {name for name in fakes.__all__ if name.startswith("Fake")}
    covered = {fake.__name__ for fake, _ in SEAMS}

    assert exported == covered, f"uncovered: {sorted(exported - covered)}"


@pytest.mark.parametrize(
    "fake,seam",
    SEAMS,
    ids=lambda item: getattr(item, "__name__", ""),
)
def test_fake_implements_every_method_of_its_seam(
    fake: type[Any], seam: type[Any]
) -> None:
    """The fake declares every callable the seam declares."""
    missing = [name for name in _protocol_methods(seam) if not hasattr(fake, name)]

    assert not missing, f"{fake.__name__} is missing {missing}"


@pytest.mark.parametrize(
    "fake,seam",
    SEAMS,
    ids=lambda item: getattr(item, "__name__", ""),
)
def test_fake_signatures_match_their_seam(fake: type[Any], seam: type[Any]) -> None:
    """Parameter names and return annotations agree, method by method.

    Comparing names rather than the whole ``Signature`` is deliberate: the
    seams annotate with strings under ``from __future__ import annotations``,
    so equality on the objects would compare quoting, not shape.
    """
    for name in _protocol_methods(seam):
        expected = inspect.signature(getattr(seam, name))
        actual = inspect.signature(getattr(fake, name))

        assert list(actual.parameters) == list(expected.parameters), (
            f"{fake.__name__}.{name} takes {list(actual.parameters)}, "
            f"{seam.__name__} declares {list(expected.parameters)}"
        )
        assert str(actual.return_annotation) == str(expected.return_annotation), (
            f"{fake.__name__}.{name} returns {actual.return_annotation}, "
            f"{seam.__name__} declares {expected.return_annotation}"
        )


@pytest.mark.parametrize(
    "fake,seam",
    SEAMS,
    ids=lambda item: getattr(item, "__name__", ""),
)
def test_fake_is_async_exactly_where_the_seam_is(
    fake: type[Any], seam: type[Any]
) -> None:
    """A sync stand-in for an async call would block the event loop.

    It would also pass a name-only check, which is why this is separate from
    the signature comparison.
    """
    for name in _protocol_methods(seam):
        seam_is_async = inspect.iscoroutinefunction(getattr(seam, name))
        fake_is_async = inspect.iscoroutinefunction(getattr(fake, name))

        assert seam_is_async == fake_is_async, f"{fake.__name__}.{name}"


def test_the_push_fake_declares_the_platforms_attribute() -> None:
    """``PushDispatcher`` requires an attribute, not only methods."""
    dispatcher = FakePushDispatcher()

    assert isinstance(dispatcher.platforms, frozenset)
    assert dispatcher.platforms


def test_the_pix_fake_declares_provider_name() -> None:
    """``PixProvider`` requires ``provider_name`` on the instance."""
    provider = FakePixProvider(provider_name="sandbox")

    assert provider.provider_name == "sandbox"


def test_importing_the_package_does_not_import_every_seam() -> None:
    """The lazy resolution the package docstring claims is real.

    Importing ``testing.fakes`` for a Pix fake must not drag genai, push and
    geo into the process.
    """
    import subprocess
    import sys

    code = (
        "import sys\n"
        "from tempest_fastapi_sdk.testing import fakes\n"
        "prefix = 'tempest_fastapi_sdk.genai'\n"
        "loaded = [m for m in sys.modules if m.startswith(prefix)]\n"
        "print(len(loaded))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "0", result.stdout
