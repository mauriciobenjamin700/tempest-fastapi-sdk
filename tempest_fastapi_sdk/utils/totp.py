"""TOTP (RFC 6238) helper backed by ``pyotp``.

Wraps the small surface of ``pyotp`` the SDK actually needs —
generating a secret, building the ``otpauth://`` provisioning URI
(scanned as a QR code by Authenticator apps), and verifying a
6-digit code with a ±N-step clock-drift window. Verification comes
in two forms: :meth:`TOTPHelper.verify` answers *is this code
valid*, and :meth:`TOTPHelper.matching_step` also answers *which
30-second step it came from*, which is what lets a caller persist the
last accepted step and refuse the same code twice. The import of
``pyotp`` is deferred to first use so the rest of the SDK keeps
working when the ``[mfa]`` extra is not installed; the
``ImportError`` is raised with a clear hint the moment a
:class:`TOTPHelper` method is called.

The provisioning URI is drawn as a QR code by
:func:`provisioning_qr_terminal` (half-block characters for a CLI) and
:func:`provisioning_qr_data_uri` (an SVG or PNG ``data:`` URI for an app
or an HTML page). Both run on ``segno``, which the ``[mfa]`` extra
carries: Reed-Solomon coding and mask selection are the long correctness
tail the SDK does not reimplement, and ``segno`` has no runtime
dependency and no upper bound. It is imported at the first call, like
``pyotp``.

The QR code **is** the secret: anyone who photographs it can generate
every future code. Show it once, never log it and never store the image.
"""

from __future__ import annotations

import base64
import hmac
import io
import time
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    import segno

ProvisioningQrKind = Literal["svg", "png"]
"""Image format of :func:`provisioning_qr_data_uri`."""

ProvisioningQrBackground = Literal["dark", "light"]
"""Background colour of the terminal :func:`provisioning_qr_terminal` prints to."""

PROVISIONING_QR_ERROR: Final[Literal["l", "m", "q", "h"]] = "m"
"""Error-correction level of the provisioning QR code: ``M`` (15 %).

The symbol is scanned off a screen, not off a creased voucher, so the
recovery that :data:`tempest_fastapi_sdk.pdf.QR_ERROR_CORRECTION` buys
with ``H`` is spent on modules the camera does not need. ``segno`` still
raises the level when the chosen version has room for it
(``boost_error``), so the symbol is never denser than ``M`` requires.
"""

PROVISIONING_QR_BORDER: Final[int] = 4
"""Quiet zone, in modules, around the provisioning QR code.

The four modules ISO/IEC 18004 asks for. Unlike the voucher, nothing
around the symbol supplies white space: a terminal is usually dark and a
``data:`` URI lands wherever the app puts it, so the quiet zone has to
be part of the drawing.
"""

PROVISIONING_QR_SCALE: Final[int] = 5
"""Pixels (SVG user units) per module in :func:`provisioning_qr_data_uri`.

A 95-character ``otpauth://`` URI encodes at version 6 (41 modules), so
the default image is ``(41 + 2 * 4) * 5 = 245`` pixels wide — readable
in an ``<img>`` with no CSS. The SVG scales without loss either way.
"""

_HALF_BLOCKS: Final[dict[tuple[bool, bool], str]] = {
    (True, True): "\u2588",
    (True, False): "\u2580",
    (False, True): "\u2584",
    (False, False): " ",
}
"""Character for a (top, bottom) pair of drawn cells: full, upper, lower, none."""


class TOTPHelper:
    """Stateless TOTP issuer + verifier.

    Construct one per app/process. Every method is pure — the
    helper holds only the ``issuer`` string shown in Authenticator
    apps next to the account name. Secrets and verification codes
    are passed in by the caller (typically loaded from the user
    row).

    Requires the ``[mfa]`` extra (``pyotp``).

    Example:

        >>> totp = TOTPHelper(issuer="My App")
        >>> secret = totp.generate_secret()                   # base32
        >>> uri = totp.provisioning_uri(secret, "ana@example.com")
        >>> # render `uri` as a QR code; user scans it
        >>> totp.verify(secret, "123456")                     # True / False
    """

    def __init__(self, *, issuer: str) -> None:
        """Initialize.

        Args:
            issuer (str): Label rendered by the Authenticator app
                alongside the account name. Use the human-readable
                product name (``"My App"``) — pyotp URL-encodes it
                for you.
        """
        self.issuer: str = issuer

    def generate_secret(self) -> str:
        """Return a fresh URL-safe base32 secret (16 chars, 80 bits).

        Returns:
            str: Base32-encoded TOTP secret ready to feed
            :meth:`provisioning_uri` and :meth:`verify`. Persist it
            on the user row (encrypted at rest is preferable —
            consider Postgres ``pgcrypto`` for the column).

        Raises:
            ImportError: When the ``[mfa]`` extra is not installed.
        """
        try:
            import pyotp
        except ImportError as exc:  # pragma: no cover - guarded by extra
            raise ImportError(
                "TOTPHelper requires the [mfa] extra. "
                "Install with `pip install tempest-fastapi-sdk[mfa]`."
            ) from exc
        return str(pyotp.random_base32())

    def provisioning_uri(self, secret: str, account_name: str) -> str:
        """Build the ``otpauth://`` URI for ``account_name``.

        The returned URI encodes the secret + issuer + account
        name; render it as a QR code (most apps support it
        directly via a `<img>` referencing a QR generator route on
        the backend).

        Args:
            secret (str): Base32 secret from :meth:`generate_secret`.
            account_name (str): Identifier shown next to ``issuer``
                in the user's Authenticator — typically the email.

        Returns:
            str: ``otpauth://totp/<issuer>:<account>?secret=…&issuer=…``.

        Raises:
            ImportError: When the ``[mfa]`` extra is not installed.
        """
        try:
            import pyotp
        except ImportError as exc:  # pragma: no cover - guarded by extra
            raise ImportError(
                "TOTPHelper requires the [mfa] extra. "
                "Install with `pip install tempest-fastapi-sdk[mfa]`."
            ) from exc
        return str(
            pyotp.TOTP(secret).provisioning_uri(
                name=account_name,
                issuer_name=self.issuer,
            )
        )

    def verify(self, secret: str, code: str, *, window: int = 1) -> bool:
        """Constant-time check that ``code`` matches ``secret`` now.

        Says nothing about whether ``code`` was **already** used: a step
        stays valid for the whole window, so the same 6 digits verify
        again until the window moves past it. A caller that needs
        single-use semantics compares :meth:`matching_step` against the
        last accepted step — which is what
        :meth:`~tempest_fastapi_sdk.UserAuthService.mfa_verify` does.

        Args:
            secret (str): Base32 secret persisted on the user row.
            code (str): 6-digit code submitted by the user.
            window (int): Tolerance in 30-second steps to absorb
                clock drift between client and server. ``1``
                (default) accepts the previous + current + next
                step (90s window total). Setting it to ``0`` is
                strict; values above ``2`` weaken security
                noticeably.

        Returns:
            bool: ``True`` when the code is valid for the current
            window, ``False`` otherwise (also when ``code`` is
            non-numeric or wrong length).

        Raises:
            ImportError: When the ``[mfa]`` extra is not installed.
        """
        return self.matching_step(secret, code, window=window) is not None

    def matching_step(self, secret: str, code: str, *, window: int = 1) -> int | None:
        """Return the 30-second step ``code`` was generated from.

        The step is the TOTP counter value, an integer that only moves
        forward — so persisting the last accepted one is what makes a code
        single-use: a code whose step is at or below that one has already
        been spent, whatever the clock says now.

        Args:
            secret (str): Base32 secret persisted on the user row.
            code (str): 6-digit code submitted by the user. Spacing and
                dashes are stripped, as in :meth:`verify`.
            window (int): Tolerance in 30-second steps, as in
                :meth:`verify`.

        Returns:
            int | None: The step whose code equals ``code``, or ``None``
            when no step in the window produces it (also when ``code`` is
            non-numeric or the wrong length). Steps are scanned oldest
            first, the order ``pyotp`` itself uses, so two steps that
            happen to render the same 6 digits resolve to the older one.

        Raises:
            ImportError: When the ``[mfa]`` extra is not installed.
        """
        try:
            import pyotp
        except ImportError as exc:  # pragma: no cover - guarded by extra
            raise ImportError(
                "TOTPHelper requires the [mfa] extra. "
                "Install with `pip install tempest-fastapi-sdk[mfa]`."
            ) from exc
        cleaned = code.strip().replace(" ", "").replace("-", "")
        if not cleaned.isdigit() or len(cleaned) != 6:
            return None
        totp = pyotp.TOTP(secret)
        current = int(time.time()) // totp.interval
        for step in range(current - window, current + window + 1):
            if hmac.compare_digest(cleaned, totp.generate_otp(step)):
                return step
        return None


def _make_symbol(uri: str) -> segno.QRCode:
    """Encode ``uri`` as a QR code symbol at :data:`PROVISIONING_QR_ERROR`.

    Args:
        uri (str): The ``otpauth://`` provisioning URI.

    Returns:
        segno.QRCode: The encoded symbol. Never a Micro QR.

    Raises:
        ValueError: When ``uri`` is empty, or too long for any QR
            version (``segno`` raises ``DataOverflowError``, a
            ``ValueError``).
        ImportError: When ``segno`` (the ``[mfa]`` extra) is missing.
    """
    if not uri:
        raise ValueError("provisioning URI must not be empty")
    try:
        import segno
    except ImportError as exc:  # pragma: no cover - guarded by extra
        raise ImportError(
            "Provisioning QR codes require the [mfa] extra (segno). "
            "Install with `pip install tempest-fastapi-sdk[mfa]`."
        ) from exc
    return segno.make_qr(uri, error=PROVISIONING_QR_ERROR)


def provisioning_qr_terminal(
    uri: str,
    *,
    background: ProvisioningQrBackground = "dark",
    border: int = PROVISIONING_QR_BORDER,
) -> str:
    """Draw the provisioning URI as a QR code made of terminal characters.

    Each character covers one module across and two modules down, using
    the half blocks ``\u2580`` (upper), ``\u2584`` (lower), ``\u2588``
    (full) and a space, so the symbol keeps its square shape in a
    terminal whose cells are about twice as tall as they are wide. A
    95-character URI (issuer ``Tempest``, ``ana@example.com``, 32-char
    secret) encodes at version 6 and prints 49 columns by 25 lines with
    the default quiet zone, and a 159-character one (a 22-character
    issuer, a 41-character e-mail) prints 61 columns by 31 lines — both
    within 80 columns (``segno`` 1.6.6).

    The characters paint in the terminal's foreground colour, so which
    modules get drawn depends on the background: on a dark terminal the
    *light* modules (and the quiet zone) are drawn, which is what makes
    the symbol read dark-on-light to the camera; on a light terminal the
    dark modules are drawn.

    Example:

        >>> from tempest_fastapi_sdk import TOTPHelper, provisioning_qr_terminal
        >>> totp = TOTPHelper(issuer="Tempest")
        >>> uri = totp.provisioning_uri(totp.generate_secret(), "ana@example.com")
        >>> print(provisioning_qr_terminal(uri))  # doctest: +SKIP

    Args:
        uri (str): The ``otpauth://`` URI from
            :meth:`TOTPHelper.provisioning_uri`.
        background (ProvisioningQrBackground): ``"dark"`` (default, the
            usual terminal) or ``"light"``.
        border (int): Quiet zone in modules. See
            :data:`PROVISIONING_QR_BORDER`.

    Returns:
        str: The lines of the drawing joined by ``"\\n"``, without a
        trailing newline — ready for ``print`` or ``typer.echo``.

    Raises:
        ValueError: When ``uri`` is empty or too long for a QR code.
        ImportError: When ``segno`` (the ``[mfa]`` extra) is missing.
    """
    symbol = _make_symbol(uri)
    draw_dark = background == "light"
    rows: list[list[bool]] = [
        [bool(module) == draw_dark for module in row]
        for row in symbol.matrix_iter(scale=1, border=border)
    ]
    if len(rows) % 2:
        rows.append([not draw_dark] * len(rows[0]))
    lines: list[str] = [
        "".join(
            _HALF_BLOCKS[(top_cell, bottom_cell)]
            for top_cell, bottom_cell in zip(top, bottom, strict=True)
        )
        for top, bottom in zip(rows[0::2], rows[1::2], strict=True)
    ]
    return "\n".join(lines)


def provisioning_qr_data_uri(
    uri: str,
    *,
    kind: ProvisioningQrKind = "svg",
    scale: int = PROVISIONING_QR_SCALE,
    border: int = PROVISIONING_QR_BORDER,
) -> str:
    """Encode the provisioning URI as a QR code image in a ``data:`` URI.

    Goes straight into ``<img src="...">`` or a mobile image view, so
    the app shows the QR code without a QR library of its own. With
    ``segno`` 1.6.6 and the defaults, the SVG ``data:`` URI for a
    95-character URI measured 3 642 to 4 062 characters across 500
    random secrets — the path length follows the module pattern.

    Example:

        >>> from tempest_fastapi_sdk import TOTPHelper, provisioning_qr_data_uri
        >>> totp = TOTPHelper(issuer="Tempest")
        >>> uri = totp.provisioning_uri(totp.generate_secret(), "ana@example.com")
        >>> provisioning_qr_data_uri(uri).startswith("data:image/svg+xml;base64,")
        True

    Args:
        uri (str): The ``otpauth://`` URI from
            :meth:`TOTPHelper.provisioning_uri`.
        kind (ProvisioningQrKind): ``"svg"`` (default) or ``"png"``.
        scale (int): Pixels (SVG user units) per module. See
            :data:`PROVISIONING_QR_SCALE`.
        border (int): Quiet zone in modules. See
            :data:`PROVISIONING_QR_BORDER`.

    Returns:
        str: ``data:image/svg+xml;base64,...`` or
        ``data:image/png;base64,...``.

    Raises:
        ValueError: When ``uri`` is empty or too long for a QR code, or
            ``kind`` is neither ``"svg"`` nor ``"png"``.
        ImportError: When ``segno`` (the ``[mfa]`` extra) is missing.
    """
    if kind not in ("svg", "png"):
        raise ValueError(f"kind must be 'svg' or 'png', got {kind!r}")
    symbol = _make_symbol(uri)
    buffer = io.BytesIO()
    if kind == "svg":
        symbol.save(buffer, kind="svg", scale=scale, border=border, xmldecl=False)
        media_type = "image/svg+xml"
    else:
        symbol.save(buffer, kind="png", scale=scale, border=border)
        media_type = "image/png"
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


__all__: list[str] = [
    "PROVISIONING_QR_BORDER",
    "PROVISIONING_QR_ERROR",
    "PROVISIONING_QR_SCALE",
    "ProvisioningQrBackground",
    "ProvisioningQrKind",
    "TOTPHelper",
    "provisioning_qr_data_uri",
    "provisioning_qr_terminal",
]
