"""Where Mercado Pago lives, and what tells production from test.

Unlike OpenPix — whose sandbox is a different host — Mercado Pago declares
a single server in its specification. Measured on the pinned spec
(``73bc0e49``): ``servers`` has exactly one entry,
``https://api.mercadopago.com``. What separates a test charge from a real
one is **which access token** you send, not which host you call.

That difference matters at the call site: there is no environment switch to
get wrong, and equally no environment switch to protect you. A production
token pointed at this same URL moves real money.
"""

from typing import Final

DEFAULT_BASE_URL: Final[str] = "https://api.mercadopago.com"
"""The only server the specification declares.

Kept as a constant rather than an enum of environments precisely because
there is nothing to choose between. An enum here would suggest a safety
that does not exist.
"""

__all__: list[str] = ["DEFAULT_BASE_URL"]
