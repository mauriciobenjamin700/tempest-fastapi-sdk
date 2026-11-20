"""The VAPID ``sub`` claim: the format a push signature accepts.

``py_vapid`` signs a VAPID JWT only when its ``sub`` claim is a ``mailto:``
address or an ``https://`` URL, and it rejects anything else with the same
message it uses for an absent claim (``Missing 'sub' from claims``), so an
e-mail without its ``mailto:`` prefix reads as a missing setting, and only
when the first push is sent. :func:`check_vapid_subject` applies the same
rule up front, where a configuration is read, with a message that names what
is wrong. It imports nothing beyond the standard library, so settings can
call it without the ``[webpush]`` extra.
"""

from __future__ import annotations

import re
from typing import Final

VAPID_SUBJECT_PATTERN: Final[str] = (
    r"^(mailto:.+@((localhost|[%\w-]+(\.[%\w-]+)+|([0-9a-f]{1,4}):+"
    r"([0-9a-f]{1,4})?)))|https:\/\/(localhost|[\w-]+\.[\w\.-]+|"
    r"([0-9a-f]{1,4}:+)+([0-9a-f]{1,4})?)$"
)
"""The pattern a VAPID ``sub`` must match, matched case-insensitively.

Ported from ``py_vapid`` 1.9.4's ``_check_sub``, character for character, so
what passes here is exactly what the signature accepts. Its quirk is ported
too: the trailing ``$`` anchors only the ``https://`` branch, so a
``mailto:`` address may be followed by anything. ``tests/utils/test_vapid.py``
compares this string with the installed ``py_vapid`` source, so an upstream
change fails a test instead of drifting.
"""

_COMPILED: Final[re.Pattern[str]] = re.compile(VAPID_SUBJECT_PATTERN, re.IGNORECASE)
"""The pattern compiled once."""


def is_valid_vapid_subject(subject: str) -> bool:
    """Return whether ``py_vapid`` would sign with this ``sub`` claim.

    Args:
        subject (str): The candidate ``sub`` claim.

    Returns:
        bool: ``True`` for a ``mailto:<email>`` or ``https://<host>`` value.
    """
    return _COMPILED.match(subject) is not None


def _describe(subject: str) -> str:
    """Say what is wrong with an invalid subject, for the error message.

    Args:
        subject (str): The rejected value.

    Returns:
        str: ``"an empty value"``, ``"a bare e-mail"``, ``"an http:// URL"``
        or the value itself, quoted.
    """
    stripped = subject.strip()
    if not stripped:
        return "an empty value"
    if "@" in stripped and ":" not in stripped:
        return "a bare e-mail; prefix it with 'mailto:'"
    if stripped.lower().startswith("http://"):
        return "an http:// URL; use https://"
    return repr(subject)


def check_vapid_subject(subject: str, *, name: str = "VAPID_SUBJECT") -> None:
    """Refuse a ``sub`` claim ``py_vapid`` would not sign with.

    Args:
        subject (str): The candidate ``sub`` claim.
        name (str): What the value is called in the message (the setting or
            the argument).

    Raises:
        ValueError: When ``subject`` is not ``mailto:<email>`` nor
            ``https://<host>``, naming what it is instead (``got a bare
            e-mail; prefix it with 'mailto:'``).
    """
    if is_valid_vapid_subject(subject):
        return
    raise ValueError(
        f"{name} must be 'mailto:<email>' or 'https://<host>' "
        f"(got {_describe(subject)})"
    )


__all__: list[str] = [
    "VAPID_SUBJECT_PATTERN",
    "check_vapid_subject",
    "is_valid_vapid_subject",
]
