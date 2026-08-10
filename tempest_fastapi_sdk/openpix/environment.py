"""Which OpenPix host a client talks to.

The two hosts are not variants of one name — production is
``api.openpix.com.br`` and the test environment is ``api.woovi-sandbox.com``,
a different domain entirely. Nothing about one spells the other, so the pair
is worth naming instead of retyping.
"""

from __future__ import annotations

from tempest_fastapi_sdk.core.enums import BaseStrEnum


class OpenPixEnvironment(BaseStrEnum):
    """An OpenPix API environment.

    Attributes:
        PRODUCTION: Live money. ``https://api.openpix.com.br``.
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
    OpenPixEnvironment.PRODUCTION: "https://api.openpix.com.br",
    OpenPixEnvironment.SANDBOX: "https://api.woovi-sandbox.com",
}
"""Base URL per environment.

Taken from the ``servers`` block of the OpenPix OpenAPI specification
(``API de Produção`` / ``API de Testes``), not from prose.
"""


__all__: list[str] = ["OpenPixEnvironment"]
