"""The ``typ`` claim vocabulary shared by every JWT the SDK mints.

A single signing secret backs the access token, the refresh token and the
intermediate token that bridges the two steps of an MFA login. Without a
type marker all three verify identically, so a route guard that only reads
``sub`` cannot tell them apart — and the MFA-pending token, handed out
after the password step but *before* the second factor, would authorize
every request as a full session.

These constants are the marker. :class:`~tempest_fastapi_sdk.UserAuthService`
stamps one on every token it issues, and
:func:`~tempest_fastapi_sdk.make_bearer_token_dependency` accepts only
:data:`ACCESS_TOKEN_TYPE` unless told otherwise.

They live in ``utils`` rather than next to either side because both the
issuing service and the verifying dependency import them, and a shared
low-level module keeps that from becoming an import cycle.
"""

from collections.abc import Collection, Mapping
from typing import Any

ACCESS_TOKEN_TYPE: str = "access"
"""``typ`` of a token that authorizes API calls."""

REFRESH_TOKEN_TYPE: str = "refresh"
"""``typ`` of a token that only buys a new access/refresh pair."""

MFA_TOKEN_TYPE: str = "mfa"
"""``typ`` of the token bridging step one and step two of an MFA login."""


def token_type_allowed(
    payload: Mapping[str, Any],
    accepted: Collection[str],
    *,
    strict: bool = False,
    legacy_claims: Collection[str] = (),
) -> bool:
    """Return whether a decoded token's type is one of ``accepted``.

    Tokens minted by this version carry ``typ``, so the check is a plain
    membership test. Tokens minted **before** ``typ`` existed — or by a
    project calling :meth:`~tempest_fastapi_sdk.JWTUtils.encode` itself —
    have no ``typ``; those pass by default, because rejecting them would
    log every existing session out on upgrade. The two markers the SDK
    already stamped, ``refresh: True`` and ``purpose: "mfa_pending"``, are
    read as a fallback so an old refresh or MFA-pending token is still
    classified instead of being waved through as an access token.

    That default is right for tokens **this SDK** minted and wrong for
    everyone else's. A service that already separated access from refresh
    under a claim of its own — ``type``, ``token_type`` — has no ``typ`` on
    any legacy token and no SDK fallback marker either, so every one of
    them would reach ``return True`` and authorize any call site for the
    length of the refresh TTL. ``legacy_claims`` names where else to read
    the type from, and ``strict`` refuses what stays unclassified:

        >>> token_type_allowed({"type": "refresh"}, [ACCESS_TOKEN_TYPE])
        True
        >>> token_type_allowed(
        ...     {"type": "refresh"},
        ...     [ACCESS_TOKEN_TYPE],
        ...     strict=True,
        ...     legacy_claims=("type",),
        ... )
        False

    Args:
        payload (Mapping[str, Any]): Decoded, signature-verified claims.
        accepted (Collection[str]): Token types the call site allows.
        strict (bool): Reject a payload whose type cannot be determined
            instead of accepting it. Leaves the migration window closed at
            the cost of logging out sessions minted before ``typ``.
        legacy_claims (Collection[str]): Extra claim names to read the
            type from, in order, when ``typ`` is absent. For the consumer
            whose own tokens spell it differently.

    Returns:
        bool: ``True`` when the token may be used at this call site.
    """
    declared = payload.get("typ")
    if not isinstance(declared, str):
        for claim in legacy_claims:
            candidate = payload.get(claim)
            if isinstance(candidate, str):
                declared = candidate
                break
    if isinstance(declared, str):
        return declared in accepted
    if payload.get("refresh") is True:
        return REFRESH_TOKEN_TYPE in accepted
    if payload.get("purpose") == "mfa_pending":
        return MFA_TOKEN_TYPE in accepted
    return not strict


AMR_CLAIM: str = "amr"
"""Claim listing how the session was authenticated (RFC 8176, ``amr``)."""

AMR_PASSWORD: str = "pwd"
"""``amr`` value for a password check (RFC 8176)."""

AMR_OTP: str = "otp"
"""``amr`` value for a one-time code — a TOTP code or a single-use
recovery code, both of which RFC 8176 files under "one-time password"."""

AMR_HARDWARE_KEY: str = "hwk"
"""``amr`` value for a WebAuthn assertion (RFC 8176: proof of possession of
a hardware-secured key)."""

AMR_EXTERNAL: str = "ext"
"""``amr`` value for a session that an OAuth provider authenticated. Not in
the RFC 8176 registry: the SDK cannot see which methods the provider used,
so it records only that the proof happened elsewhere."""

PASSWORD_AMR: tuple[str, ...] = (AMR_PASSWORD,)
"""``amr`` of a session opened with the password alone."""

MFA_AMR: tuple[str, ...] = (AMR_PASSWORD, AMR_OTP)
"""``amr`` of a session that passed the password and the second factor."""

MFA_AMR_METHODS: frozenset[str] = frozenset({AMR_OTP})
"""The ``amr`` values that prove a second factor by default."""


def token_amr(payload: Mapping[str, Any]) -> tuple[str, ...] | None:
    """Return the ``amr`` methods of a decoded token.

    Args:
        payload (Mapping[str, Any]): Decoded, signature-verified claims.

    Returns:
        tuple[str, ...] | None: The methods, in the order they were
        stamped. ``None`` when the claim is absent or is not a list of
        strings — a token minted before the claim existed, or by someone
        else. Callers treat ``None`` as "no second factor proven", so the
        rollout window never counts an unknown session as MFA.
    """
    raw = payload.get(AMR_CLAIM)
    if not isinstance(raw, list | tuple):
        return None
    if not all(isinstance(method, str) for method in raw):
        return None
    return tuple(raw)


__all__: list[str] = [
    "ACCESS_TOKEN_TYPE",
    "AMR_CLAIM",
    "AMR_EXTERNAL",
    "AMR_HARDWARE_KEY",
    "AMR_OTP",
    "AMR_PASSWORD",
    "MFA_AMR",
    "MFA_AMR_METHODS",
    "MFA_TOKEN_TYPE",
    "PASSWORD_AMR",
    "REFRESH_TOKEN_TYPE",
    "token_amr",
    "token_type_allowed",
]
