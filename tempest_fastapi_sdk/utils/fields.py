"""Reusable Pydantic field types with built-in validation rules.

These are generic ``Annotated`` aliases that bake a validation rule into
the type itself, so a schema field reads as *what it is* instead of
repeating ``Field(gt=0, ...)`` at every use site:

    from tempest_fastapi_sdk.utils import CentsField, PercentField, SlugField

    class ProductCreateSchema(BaseSchema):
        price_cents: CentsField        # int >= 0 (money in minor units)
        discount: PercentField         # float in [0, 100]
        slug: SlugField                # lower-kebab-case string

They follow the same ``*Field`` naming convention as the BR field types
(``CPFField`` / ``UFField`` / ...), so anything ending in ``Field`` is a
drop-in schema field type. Invalid values raise ``ValidationError`` ->
HTTP 422 via the SDK exception handler.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from annotated_types import Ge, Gt, Le
from pydantic import Field, StringConstraints

# --- Integers -------------------------------------------------------------

PositiveIntField = Annotated[int, Gt(0)]
"""Integer strictly greater than zero (quantities, counts, 1-based ids)."""

NonNegativeIntField = Annotated[int, Ge(0)]
"""Integer greater than or equal to zero."""

CentsField = Annotated[int, Ge(0)]
"""Monetary amount in minor units (cents): a non-negative integer.

Storing money as integer cents avoids binary float rounding. Use this for
prices/balances and divide by 100 only at the presentation edge.
"""

PortField = Annotated[int, Ge(1), Le(65535)]
"""TCP/UDP port number in the valid range ``1..65535``."""

RatingField = Annotated[int, Ge(0), Le(5)]
"""Star rating as an integer in the inclusive range ``0..5``.

The canonical 0-to-5-star review score: ``0`` means "no stars", ``5`` the
maximum. Use it for a rating column/field so the ``0..5`` bound lives in
the type instead of a hand-written validator.
"""

# --- Floats ---------------------------------------------------------------

PositiveFloatField = Annotated[float, Gt(0)]
"""Float strictly greater than zero."""

NonNegativeFloatField = Annotated[float, Ge(0)]
"""Float greater than or equal to zero."""

PercentField = Annotated[float, Ge(0), Le(100)]
"""Percentage in the inclusive range ``0..100``."""

RatioField = Annotated[float, Ge(0), Le(1)]
"""Fraction/probability in the inclusive range ``0..1``."""

LatitudeField = Annotated[float, Ge(-90), Le(90)]
"""Geographic latitude in degrees, ``-90..90``."""

LongitudeField = Annotated[float, Ge(-180), Le(180)]
"""Geographic longitude in degrees, ``-180..180``."""

# --- Decimals -------------------------------------------------------------

PriceField = Annotated[Decimal, Field(ge=0, decimal_places=2)]
"""Non-negative monetary amount as a :class:`~decimal.Decimal`, 2 places.

Prefer :data:`CentsField` (integer cents) for storage; use ``PriceField``
when an exact decimal with two fractional digits is the contract (e.g. a
payment gateway payload).
"""

# --- Strings --------------------------------------------------------------

NonEmptyStrField = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
"""String trimmed of surrounding whitespace, rejected when empty.

Whitespace-only input fails because it is stripped to ``""`` before the
``min_length=1`` check.
"""

SlugField = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"),
]
"""URL-safe slug: lowercase alphanumerics in kebab-case (``my-post-1``)."""

HexColorField = Annotated[
    str,
    StringConstraints(pattern=r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$"),
]
"""CSS hex color, 3 or 6 hex digits with a leading ``#`` (``#fff`` / ``#abc123``)."""


__all__: list[str] = [
    "CentsField",
    "HexColorField",
    "LatitudeField",
    "LongitudeField",
    "NonEmptyStrField",
    "NonNegativeFloatField",
    "NonNegativeIntField",
    "PercentField",
    "PortField",
    "PositiveFloatField",
    "PositiveIntField",
    "PriceField",
    "RatingField",
    "RatioField",
    "SlugField",
]
