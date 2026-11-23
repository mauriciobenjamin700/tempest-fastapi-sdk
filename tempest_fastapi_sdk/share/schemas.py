"""Schemas describing one shareable item: its page tags and its card.

The resolver a service hands to
:func:`~tempest_fastapi_sdk.share.make_share_router` returns a
:class:`SharePreviewSchema` per item. Every URL field is validated as an
absolute ``http``/``https`` URL: escaping text does not make a
``javascript:`` URL inert inside ``href`` or ``<meta http-equiv=refresh>``,
so the scheme is refused before any HTML is written.
"""

from urllib.parse import urlsplit

from pydantic import Field, field_validator

from tempest_fastapi_sdk.core.enums import BaseStrEnum
from tempest_fastapi_sdk.schemas.base import BaseSchema

ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})
"""Schemes accepted in every URL a share page writes."""


def require_absolute_http_url(value: str) -> str:
    """Validate that ``value`` is an absolute ``http``/``https`` URL.

    Social crawlers resolve nothing relative to the page — ``og:image``
    and ``og:url`` must carry scheme and host — and a ``javascript:`` or
    ``data:`` URL inside ``href`` survives HTML escaping intact.

    Args:
        value (str): The URL to check.

    Returns:
        str: ``value`` unchanged.

    Raises:
        ValueError: When the scheme is not ``http``/``https`` or the host
            is missing.
    """
    parts = urlsplit(value)
    if parts.scheme.lower() not in ALLOWED_URL_SCHEMES or not parts.netloc:
        raise ValueError(f"must be an absolute http(s) URL with a host, got {value!r}")
    return value


def collapse_whitespace(value: str) -> str:
    """Turn every run of whitespace into one space.

    The card draws each text on one line, and Pillow refuses to measure
    text containing ``\\n`` (``ValueError: can't measure length of
    multiline text``) — so a description typed with line breaks would
    turn the image into a ``500``.

    Args:
        value (str): The text.

    Returns:
        str: The text on one line, without leading or trailing spaces.
    """
    return " ".join(value.split())


class ShareCardLineSchema(BaseSchema):
    """One information line on the card.

    Attributes:
        text (str): The line's text, shrunk and then cut with ``…`` when
            it does not fit.
        icon (str | None): A short glyph drawn before the text in the
            accent color (``"→"``, ``"★"``, ``"✓"``). Drawn with the
            card's font, so it must be a character that font has — a
            color emoji is not.
    """

    text: str = Field(
        title="Texto",
        description="Text of the line.",
        examples=["Sai sexta, 18h"],
    )
    icon: str | None = Field(
        default=None,
        max_length=4,
        title="Ícone",
        description="Glyph drawn before the text in the accent color.",
        examples=["→", None],
    )

    @field_validator("text", "icon")
    @classmethod
    def _one_line(cls, value: str | None) -> str | None:
        """Collapse line breaks and repeated spaces.

        Args:
            value (str | None): The submitted text.

        Returns:
            str | None: The text on one line.
        """
        return None if value is None else collapse_whitespace(value)


class ShareCardSchema(BaseSchema):
    """Content of the 1200x630 preview image.

    Attributes:
        title (str): The headline. Shrinks, then wraps to two lines, then
            is cut with ``…``.
        brand (str | None): Small text at the top left, in the accent
            color (the product name).
        kicker (str | None): Muted line under the brand.
        lines (list[ShareCardLineSchema]): Information lines under the
            title. Lines that do not fit above the chips are dropped.
        chips (list[str]): Rounded labels near the bottom, left to right.
            Chips that would cross the right margin are dropped.
        footer (str | None): Muted line at the bottom.

    Every text is collapsed to one line (line breaks and repeated spaces
    become one space); empty chips are dropped.
    """

    title: str = Field(
        title="Título",
        description="Headline of the card.",
        examples=["Teresina → Fortaleza"],
    )
    brand: str | None = Field(
        default=None,
        title="Marca",
        description="Product name at the top left.",
        examples=["BusCar", None],
    )
    kicker: str | None = Field(
        default=None,
        title="Chamada",
        description="Muted line under the brand.",
        examples=["Carona disponível", None],
    )
    lines: list[ShareCardLineSchema] = Field(
        default_factory=list,
        title="Linhas",
        description="Information lines under the title.",
    )
    chips: list[str] = Field(
        default_factory=list,
        title="Etiquetas",
        description="Rounded labels near the bottom.",
        examples=[["R$ 80 por vaga", "3 vagas"]],
    )
    footer: str | None = Field(
        default=None,
        title="Rodapé",
        description="Muted line at the bottom.",
        examples=["Motorista: Ana (4,9)", None],
    )

    @field_validator("title", "brand", "kicker", "footer")
    @classmethod
    def _one_line(cls, value: str | None) -> str | None:
        """Collapse line breaks and repeated spaces.

        Args:
            value (str | None): The submitted text.

        Returns:
            str | None: The text on one line.
        """
        return None if value is None else collapse_whitespace(value)

    @field_validator("chips")
    @classmethod
    def _one_line_chips(cls, value: list[str]) -> list[str]:
        """Collapse each chip to one line and drop the empty ones.

        Args:
            value (list[str]): The submitted chips.

        Returns:
            list[str]: Non-empty chips, each on one line.
        """
        return [chip for chip in map(collapse_whitespace, value) if chip]


class SharePreviewSchema(BaseSchema):
    """What the share page says about one item.

    Attributes:
        title (str): ``og:title`` and the page ``<title>``.
        description (str): ``og:description``.
        redirect_url (str): Where a human lands — the app screen of the
            item. Absolute ``http``/``https``.
        canonical_url (str | None): ``og:url`` and ``<link rel=canonical>``.
            ``None`` uses the share page's own public URL.
        image_url (str | None): ``og:image``. ``None`` uses the router's
            own ``og.png`` when it has a renderer and the preview has a
            ``card``; otherwise the page goes out without an image.
        image_alt (str | None): ``og:image:alt``; ``None`` reuses the
            title.
        version (str | None): Changes whenever the item's preview
            changes (an ``updated_at``). Goes into the image ETag and
            into ``?v=`` on the generated ``og:image`` — crawlers cache
            the image by URL, so a 304 alone never refreshes a preview
            already shared.
        card (ShareCardSchema | None): Content of the generated image.
    """

    title: str = Field(
        title="Título",
        description="Value of og:title and of the page title.",
        examples=["Carona Teresina → Fortaleza"],
    )
    description: str = Field(
        title="Descrição",
        description="Value of og:description.",
        examples=["Sexta, 18h · R$ 80 por vaga · 3 vagas"],
    )
    redirect_url: str = Field(
        title="URL de destino",
        description="Absolute http(s) URL a human is sent to.",
        examples=["https://app.example.com/caronas/42"],
    )
    canonical_url: str | None = Field(
        default=None,
        title="URL canônica",
        description="Absolute http(s) og:url; defaults to the share page.",
        examples=[None],
    )
    image_url: str | None = Field(
        default=None,
        title="URL da imagem",
        description="Absolute http(s) og:image; defaults to the generated card.",
        examples=[None],
    )
    image_alt: str | None = Field(
        default=None,
        title="Texto alternativo",
        description="Value of og:image:alt; defaults to the title.",
        examples=[None],
    )
    version: str | None = Field(
        default=None,
        title="Versão",
        description="Changes when the preview changes; keys the image cache.",
        examples=["2026-11-23T10:00:00+00:00", None],
    )
    card: ShareCardSchema | None = Field(
        default=None,
        title="Card",
        description="Content of the generated preview image.",
    )

    @field_validator("redirect_url", "canonical_url", "image_url")
    @classmethod
    def _absolute_http_url(cls, value: str | None) -> str | None:
        """Refuse a URL that is relative or not ``http``/``https``.

        Args:
            value (str | None): The submitted URL.

        Returns:
            str | None: ``value`` unchanged.
        """
        if value is None:
            return None
        return require_absolute_http_url(value)


class ShareFrameLayoutEnum(BaseStrEnum):
    """Shape of the shareable frame.

    Attributes:
        STORY: 1080x1920, the status/story format.
        SQUARE: 1080x1080, for a chat or a group.
    """

    STORY = "story"
    SQUARE = "square"


class ShareFrameSchema(BaseSchema):
    """Content of the shareable frame: a QR code with who and what around it.

    Attributes:
        url (str): Absolute ``http``/``https`` URL the QR code encodes,
            usually the item's share page.
        title (str): Main line under the photo — the person's name.
        subtitle (str | None): Muted line under the title (the route).
        brand (str | None): Product name next to the logo, in the
            accent color.
        caption (str | None): Call to action under the link
            (``"Escaneie e reserve"``).
        show_url (bool): Write the URL under the QR code, without the
            scheme, for whoever cannot scan.

    Every text is collapsed to one line, as on the card.
    """

    url: str = Field(
        title="URL",
        description="Absolute http(s) URL the QR code encodes.",
        examples=["https://app.example.com/s/42"],
    )
    title: str = Field(
        title="Título",
        description="Main line, usually the person's name.",
        examples=["Ana Souza"],
    )
    subtitle: str | None = Field(
        default=None,
        title="Subtítulo",
        description="Muted line under the title.",
        examples=["Teresina → Fortaleza · sexta, 18h", None],
    )
    brand: str | None = Field(
        default=None,
        title="Marca",
        description="Product name next to the logo.",
        examples=["BusCar", None],
    )
    caption: str | None = Field(
        default=None,
        title="Chamada",
        description="Call to action under the link.",
        examples=["Escaneie e reserve sua vaga", None],
    )
    show_url: bool = Field(
        default=True,
        title="Mostrar link",
        description="Write the URL under the QR code.",
    )

    @field_validator("url")
    @classmethod
    def _absolute_http_url(cls, value: str) -> str:
        """Refuse a URL that is relative or not ``http``/``https``.

        Args:
            value (str): The submitted URL.

        Returns:
            str: ``value`` unchanged.
        """
        return require_absolute_http_url(value)

    @field_validator("title", "subtitle", "brand", "caption")
    @classmethod
    def _one_line(cls, value: str | None) -> str | None:
        """Collapse line breaks and repeated spaces.

        Args:
            value (str | None): The submitted text.

        Returns:
            str | None: The text on one line.
        """
        return None if value is None else collapse_whitespace(value)


__all__: list[str] = [
    "ALLOWED_URL_SCHEMES",
    "ShareCardLineSchema",
    "ShareCardSchema",
    "ShareFrameLayoutEnum",
    "ShareFrameSchema",
    "SharePreviewSchema",
    "collapse_whitespace",
    "require_absolute_http_url",
]
