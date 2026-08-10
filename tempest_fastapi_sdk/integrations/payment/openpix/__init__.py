"""The whole OpenPix surface, ready to import.

`pip install tempest-fastapi-sdk` and you have all 358 OpenPix schemas and
all 105 operations, plus the four things the specification does not say.
Nobody runs a generator, and nobody re-derives by hand which header carries
the webhook signature.

.. code-block:: python

    from tempest_fastapi_sdk import HTTPClient
    from tempest_fastapi_sdk.integrations.payment.openpix import (
        Charge,
        OpenPixClient,
        OpenPixEnvironment,
        to_cents,
    )

    http: HTTPClient = HTTPClient(
        base_url=OpenPixEnvironment.SANDBOX.base_url,
        default_headers={"Authorization": "<your AppID>"},
    )
    client: OpenPixClient = OpenPixClient(http)

The generated half is **checked in, not written by hand**:
``scripts/regen_openpix.py`` produces it from the pinned specification in
``vendor/openpix-openapi.yaml``, and a test fails if the files on disk drift
from what that script produces. Editing them directly is how checked-in
generated code rots.

Two halves, and it is worth knowing which is which:

- **Generated** — ``OpenPixClient``, ``DEFAULT_BASE_URL`` and the 358
  schema classes. Whatever the specification says, verbatim.
- **Hand-written** — ``OpenPixEnvironment`` (the spec's two hosts are
  different domains), ``OpenPixEvent`` (28 webhook events), the webhook
  verification, and the money helpers, because the spec says *"Value in
  cents"* and then types the field ``number``.

!!! note "The schemas load on first use, not on import"
    Building 358 Pydantic models costs the better part of a second. Importing
    this package for ``to_cents`` alone should not pay that, so the generated
    modules are resolved lazily through :pep:`562`. The first access to a
    generated name pays it; the thin layer never does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.integrations.payment.openpix.environment import (
    OpenPixEnvironment as OpenPixEnvironment,
)
from tempest_fastapi_sdk.integrations.payment.openpix.events import (
    OpenPixEvent as OpenPixEvent,
)
from tempest_fastapi_sdk.integrations.payment.openpix.money import (
    cents_to_reais as cents_to_reais,
)
from tempest_fastapi_sdk.integrations.payment.openpix.money import (
    reais_to_cents as reais_to_cents,
)
from tempest_fastapi_sdk.integrations.payment.openpix.money import to_cents as to_cents
from tempest_fastapi_sdk.integrations.payment.openpix.webhooks import (
    OPENPIX_WEBHOOK_PUBLIC_KEY as OPENPIX_WEBHOOK_PUBLIC_KEY,
)
from tempest_fastapi_sdk.integrations.payment.openpix.webhooks import (
    OPENPIX_WEBHOOK_SIGNATURE_HEADER as OPENPIX_WEBHOOK_SIGNATURE_HEADER,
)
from tempest_fastapi_sdk.integrations.payment.openpix.webhooks import (
    OpenPixWebhookEvent as OpenPixWebhookEvent,
)
from tempest_fastapi_sdk.integrations.payment.openpix.webhooks import (
    decode_public_key as decode_public_key,
)
from tempest_fastapi_sdk.integrations.payment.openpix.webhooks import (
    make_openpix_webhook_dependency as make_openpix_webhook_dependency,
)
from tempest_fastapi_sdk.integrations.payment.openpix.webhooks import (
    webhook_verifier as webhook_verifier,
)

if TYPE_CHECKING:  # pragma: no cover - import-time cost is the point
    from tempest_fastapi_sdk.integrations.payment.openpix.client import (
        DEFAULT_BASE_URL as DEFAULT_BASE_URL,
    )
    from tempest_fastapi_sdk.integrations.payment.openpix.client import (
        OpenPixClient as OpenPixClient,
    )
    from tempest_fastapi_sdk.integrations.payment.openpix.schemas import *  # noqa: F403

_HAND_WRITTEN: tuple[str, ...] = (
    "OPENPIX_WEBHOOK_PUBLIC_KEY",
    "OPENPIX_WEBHOOK_SIGNATURE_HEADER",
    "OpenPixEnvironment",
    "OpenPixEvent",
    "OpenPixWebhookEvent",
    "cents_to_reais",
    "decode_public_key",
    "make_openpix_webhook_dependency",
    "reais_to_cents",
    "to_cents",
    "webhook_verifier",
)
"""Names this package defines itself, always eagerly available."""


_GENERATED_MODULES: tuple[str, ...] = ("schemas", "client")
"""Submodules holding the generated code, in dependency order."""


def _generated_names() -> dict[str, str]:
    """Map every generated name to the submodule that defines it.

    Returns:
        dict[str, str]: ``{name: submodule}``, built by importing the
        generated modules. Called only from :func:`__getattr__` and
        :func:`__dir__`, so importing this package does not trigger it.

    Imports go through :func:`importlib.import_module` rather than
    ``from . import schemas``. The ``from`` form asks the **package** for
    the attribute, which lands back in :func:`__getattr__`, which calls
    this — unbounded recursion, and the traceback blames the last frame
    rather than the loop.
    """
    from importlib import import_module

    mapping: dict[str, str] = {}
    for module_name in _GENERATED_MODULES:
        module = import_module(f"{__name__}.{module_name}")
        mapping.update(dict.fromkeys(module.__all__, module_name))
    return mapping


def __getattr__(name: str) -> Any:
    """Resolve a generated name, or a generated submodule, on first access.

    Args:
        name (str): The attribute being looked up.

    Returns:
        Any: The generated class, client, constant or submodule.

    Raises:
        AttributeError: If no generated module defines ``name``.

    The 358 schema classes are not listed here one by one on purpose: the
    list is regenerated from the specification, and a hand-kept copy would
    be one more thing to drift. The cost is that a **typo** also loads the
    generated modules before failing — paid once, on an access that was
    going to raise anyway.
    """
    from importlib import import_module

    if name in _GENERATED_MODULES:
        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module

    module_name = _generated_names().get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """List everything importable from this package.

    Returns:
        list[str]: Hand-written names plus every generated one, sorted, so
        autocompletion and ``help()`` see the whole surface.

    This does load the generated modules. That is the right trade: someone
    running ``dir()`` is exploring the API, which is exactly when the full
    list is worth the wait.
    """
    return sorted({*_HAND_WRITTEN, *_generated_names()})


__all__: list[str] = [
    "DEFAULT_BASE_URL",
    "OPENPIX_WEBHOOK_PUBLIC_KEY",
    "OPENPIX_WEBHOOK_SIGNATURE_HEADER",
    "OpenPixClient",
    "OpenPixEnvironment",
    "OpenPixEvent",
    "OpenPixWebhookEvent",
    "cents_to_reais",
    "decode_public_key",
    "make_openpix_webhook_dependency",
    "reais_to_cents",
    "to_cents",
    "webhook_verifier",
]
"""The curated surface.

The 358 generated schema classes are importable by name
(``from tempest_fastapi_sdk.integrations.payment.openpix import Charge``) and appear in
:func:`__dir__`, but are deliberately not starred here: ``import *`` pulling
358 names into a caller's namespace is not a kindness, and listing them would
mean maintaining a copy of the generated ``__all__``.
"""
