"""Which OpenPix host a client talks to.

Production is ``api.woovi.com`` and the test environment is
``api.woovi-sandbox.com`` — one character of difference on a URL that
carries live money, which is exactly why the pair is named here instead of
retyped at each call site.

Production moved from ``api.openpix.com.br`` in v0.260.0, following the
``servers`` block of the refreshed specification. The old host is still
answering: measured 2026-08-28, ``GET /api/v1/charge`` returns ``401`` on
``api.openpix.com.br``, ``api.woovi.com`` and ``api.woovi-sandbox.com``
alike, so a service pinned to the old name keeps working.
"""

from __future__ import annotations

from tempest_fastapi_sdk.core.enums import BaseStrEnum


class OpenPixEnvironment(BaseStrEnum):
    """An OpenPix API environment.

    Attributes:
        PRODUCTION: Live money. ``https://api.woovi.com``.
        SANDBOX: Test environment. ``https://api.woovi-sandbox.com``.
    """

    PRODUCTION = "production"
    SANDBOX = "sandbox"

    @property
    def base_url(self) -> str:
        """Return the API base URL for this environment.

        Returns:
            str: The host, with no trailing slash, ready for
            ``HTTPClient(base_url=...)``.
        """
        return _BASE_URLS[self]


_BASE_URLS: dict[OpenPixEnvironment, str] = {
    OpenPixEnvironment.PRODUCTION: "https://api.woovi.com",
    OpenPixEnvironment.SANDBOX: "https://api.woovi-sandbox.com",
}
"""Base URL per environment.

Taken from the ``servers`` block of the OpenPix OpenAPI specification
(``API de Produção`` / ``API de Testes``), not from prose.
"""


__all__: list[str] = ["OpenPixEnvironment"]
