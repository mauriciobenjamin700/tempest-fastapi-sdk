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

from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated, Any

from annotated_types import Ge, Gt, Le
from pydantic import (
    BeforeValidator,
    Field,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    StringConstraints,
)
from pydantic.fields import FieldInfo
from pydantic_core import core_schema

from tempest_fastapi_sdk.core.enums import Locale, normalize_locale_tag
from tempest_fastapi_sdk.utils.media_types import ImageMediaType

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

DecimalRatioField = Annotated[Decimal, Field(ge=0, le=1)]
"""Exact fraction in the inclusive range ``0..1``.

The :class:`~decimal.Decimal` counterpart of :data:`RatioField`. Use it —
never the float one — wherever the fraction multiplies money.
``Decimal("0.28")`` is exactly twenty-eight percent; ``0.28`` is not, and
the difference surfaces the moment it meets a :data:`PriceField`: Python
raises ``TypeError`` on ``Decimal * float``, and the "fix" of casting one
side is what moves the cent a bid is judged on.
"""

DecimalPercentField = Annotated[Decimal, Field(ge=0, le=100)]
"""Exact percentage in the inclusive range ``0..100``.

The :class:`~decimal.Decimal` counterpart of :data:`PercentField`, for the
same reason as :data:`DecimalRatioField`. Divide by
:data:`~tempest_fastapi_sdk.utils.currency.HUNDRED` to reach the ratio.
"""

SignedDecimalRatioField = Annotated[Decimal, Field(le=1)]
"""Exact fraction, capped at ``1``, that may go below zero.

For a *derived* share that can legitimately come out negative: a balancing
line the premises left no slack for, or a closing difference a document
must disclose rather than clamp to zero. Bounding it above but not below
is deliberate — over 100% is a bug, under 0% is news.
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

# --- Enums ----------------------------------------------------------------

LocaleField = Annotated[Locale, BeforeValidator(normalize_locale_tag)]
"""Pydantic type that accepts a loose locale string and yields a :class:`Locale`.

The :class:`~pydantic.BeforeValidator` runs ahead of the enum coercion, so
case and separator variants (``"pt_BR"``, ``"PT-BR"``) and the bare primary
subtag (``"pt"`` -> ``PT_BR``) are accepted; an unsupported tag raises a
clear ``invalid locale`` error (HTTP 422). Use it on a request schema the
way :data:`UFField` is used for state — the stored/response value can stay a
plain ``str``.
"""


class AllowedImageMediaTypes:
    """Pydantic metadata restricting an ``ImageMediaType`` field to a subset.

    Validates after the enum itself, so the normalization the enum already
    does — case, surrounding whitespace, aliases such as ``image/jpg`` — runs
    first, and narrows the OpenAPI schema to the allowed values only.

    Attributes:
        allowed (tuple[ImageMediaType, ...]): The accepted types, in the
            order the schema lists them.
    """

    def __init__(self, allowed: Sequence[ImageMediaType]) -> None:
        """Store the accepted types.

        Args:
            allowed (Sequence[ImageMediaType]): The accepted types.

        Raises:
            ValueError: When ``allowed`` is empty.
        """
        if not allowed:
            raise ValueError("image_media_type_field needs at least one type")
        self.allowed: tuple[ImageMediaType, ...] = tuple(allowed)

    def __get_pydantic_core_schema__(
        self,
        source: Any,
        handler: GetCoreSchemaHandler,
    ) -> core_schema.CoreSchema:
        """Validate as ``ImageMediaType``, then refuse what is not allowed.

        Args:
            source (Any): The annotated type.
            handler (GetCoreSchemaHandler): Pydantic's schema handler.

        Returns:
            core_schema.CoreSchema: The enum schema plus the membership check.
        """
        allowed = self.allowed

        def check(value: ImageMediaType) -> ImageMediaType:
            """Refuse a recognized type that is not in ``allowed``.

            Args:
                value (ImageMediaType): The normalized type.

            Returns:
                ImageMediaType: ``value``, unchanged.

            Raises:
                ValueError: When ``value`` is not allowed (``422``).
            """
            if value not in allowed:
                accepted = ", ".join(item.value for item in allowed)
                raise ValueError(f"image type must be one of: {accepted}")
            return value

        return core_schema.no_info_after_validator_function(check, handler(source))

    def __get_pydantic_json_schema__(
        self,
        schema: core_schema.CoreSchema,
        handler: GetJsonSchemaHandler,
    ) -> dict[str, Any]:
        """List only the allowed values in the OpenAPI schema.

        Args:
            schema (core_schema.CoreSchema): The core schema.
            handler (GetJsonSchemaHandler): Pydantic's JSON-schema handler.

        Returns:
            dict[str, Any]: A string schema enumerating :attr:`allowed`.
        """
        return {"type": "string", "enum": [item.value for item in self.allowed]}


def image_media_type_field(
    *allowed: ImageMediaType,
    title: str = "Image type",
    description: str | None = None,
    examples: list[str] | None = None,
) -> FieldInfo:
    """Build the metadata of a field accepting some ``ImageMediaType`` values.

    For a schema that takes the declared type of an upload — the photo of
    an evidence, of a product — and must refuse the types it does not
    store. Put it in ``Annotated`` next to ``ImageMediaType``, so the static
    type is the enum::

        class EvidenceCreateSchema(BaseSchema):
            photo_type: Annotated[
                ImageMediaType,
                image_media_type_field(
                    ImageMediaType.JPEG,
                    ImageMediaType.PNG,
                    ImageMediaType.WEBP,
                    ImageMediaType.HEIC,
                    title="Photo type",
                ),
            ]

    The value is normalized by :class:`ImageMediaType` (``"IMAGE/JPG "``
    becomes ``image/jpeg``), a type outside ``allowed`` is a ``422``, and
    the OpenAPI schema lists ``allowed`` only.

    Args:
        *allowed (ImageMediaType): The accepted types. None means every
            member of :class:`ImageMediaType`.
        title (str): The field title.
        description (str | None): The field description. ``None`` names
            the accepted types.
        examples (list[str] | None): The field examples. ``None`` uses the
            first accepted type.

    Returns:
        FieldInfo: The field metadata, carrying the restriction.
    """
    accepted: tuple[ImageMediaType, ...] = allowed or tuple(ImageMediaType)
    info: FieldInfo = Field(
        title=title,
        description=description
        or "Image type: " + ", ".join(item.value for item in accepted) + ".",
        examples=examples if examples is not None else [accepted[0].value],
    )
    info.metadata.append(AllowedImageMediaTypes(accepted))
    return info


__all__: list[str] = [
    "AllowedImageMediaTypes",
    "CentsField",
    "DecimalPercentField",
    "DecimalRatioField",
    "HexColorField",
    "LatitudeField",
    "LocaleField",
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
    "SignedDecimalRatioField",
    "SlugField",
    "image_media_type_field",
]
