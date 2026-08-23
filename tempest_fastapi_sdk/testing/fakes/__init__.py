"""Stand-ins for the third parties a service talks to.

Every fake here implements one of the SDK's provider seams and talks to
nobody: no credential, no sandbox account, no network. Two uses, one object —
run the app locally without signing up for anything, and assert on a flow in
a test without a hand-written mock.

What separates these from a mock is that they are **steerable**. A mock
answers the call you set up; these hold state and let you move it: mark a Pix
charge paid, retire a push token, make the next send fail with the error the
real client raises. The branch worth testing is usually the failing one, and
against a real provider it is the branch you cannot reach on purpose.

Resolution is lazy, for the reason it is lazy in ``integrations``: a fake
imports its seam's module, so eager imports here would make
``import tempest_fastapi_sdk.testing`` pull genai, push and geo into a
process that asked for a database fixture.

Example:

    >>> from tempest_fastapi_sdk.testing.fakes import FakePixProvider
    >>> provider = FakePixProvider()
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tempest_fastapi_sdk.testing.fakes.email import (
        FakeEmailUtils as FakeEmailUtils,
    )
    from tempest_fastapi_sdk.testing.fakes.email import SentEmail as SentEmail
    from tempest_fastapi_sdk.testing.fakes.genai import (
        FakeModerationBackend as FakeModerationBackend,
    )
    from tempest_fastapi_sdk.testing.fakes.genai import (
        FakeTextBackend as FakeTextBackend,
    )
    from tempest_fastapi_sdk.testing.fakes.geo import (
        FakeGeocodingBackend as FakeGeocodingBackend,
    )
    from tempest_fastapi_sdk.testing.fakes.geo import (
        FakeRoutingBackend as FakeRoutingBackend,
    )
    from tempest_fastapi_sdk.testing.fakes.payment import (
        FakePixProvider as FakePixProvider,
    )
    from tempest_fastapi_sdk.testing.fakes.push import (
        FakePushDispatcher as FakePushDispatcher,
    )
    from tempest_fastapi_sdk.testing.fakes.push import SentPush as SentPush
    from tempest_fastapi_sdk.testing.fakes.search import (
        FakeWebSearchBackend as FakeWebSearchBackend,
    )

_EXPORTS: dict[str, str] = {
    "FakeEmailUtils": "email",
    "FakeGeocodingBackend": "geo",
    "FakeModerationBackend": "genai",
    "FakePixProvider": "payment",
    "FakePushDispatcher": "push",
    "FakeRoutingBackend": "geo",
    "FakeTextBackend": "genai",
    "FakeWebSearchBackend": "search",
    "SentEmail": "email",
    "SentPush": "push",
}


def __getattr__(name: str) -> Any:
    """Import a fake's module the first time the fake is asked for.

    Args:
        name (str): The attribute being looked up.

    Returns:
        Any: The requested fake.

    Raises:
        AttributeError: When this package exports no such name.
    """
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(f"{__name__}.{module_name}")
    return getattr(module, name)


def __dir__() -> list[str]:
    """List what this package exports.

    Returns:
        list[str]: Every fake name, sorted.
    """
    return sorted(_EXPORTS)


__all__: list[str] = sorted(_EXPORTS)
