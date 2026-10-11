"""Mask the password embedded in a connection URL.

A DSN that carries credentials — ``postgresql+asyncpg://user:pass@host/db``,
``redis://:pass@host/0``, ``amqp://user:pass@host/`` — must never reach a
log line, an HTTP payload or a CLI report whole. This module is the single
place that turns one into its safe form, so ``AsyncDatabaseManager.db_url_safe``
and every diagnostic that prints a URL agree on the output.

The regex pass is authoritative and runs even when SQLAlchemy parses the
URL: ``make_url`` mis-reads a password that contains ``@``
(``amqp://u:p@ss@h/`` becomes password ``p`` and host ``ss@h``) and rejects
a host list (``amqp://u:pw@h1,u:pw2@h2/``); neither shape is masked by
``render_as_string(hide_password=True)`` alone. The query string is masked
separately, since ``?password=`` there is not part of the URL's userinfo.
"""

from __future__ import annotations

import contextlib
import re

from sqlalchemy.engine import make_url

_URL_PATTERN: re.Pattern[str] = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)(?P<authority>[^/?#\s]*)"
)
"""Finds a ``scheme://authority`` anywhere in a string.

Unanchored on purpose: the diagnostics that print a URL often embed it in
a sentence (``cannot reach redis://...``), so the mask has to find it in
the middle of the text, not only at the start.
"""

_QUERY_SECRET_PATTERN: re.Pattern[str] = re.compile(
    r"(?P<prefix>[?&](?:password|passwd|pwd|secret)=)(?P<value>[^&#\s]*)",
    re.IGNORECASE,
)
"""A credential-bearing query parameter in a URL."""


def _mask_host_segment(segment: str) -> str:
    """Mask the password of one host segment of a URL authority.

    A host list repeats ``user:password@host`` per segment; each one is
    masked independently so a second ``user:pass@`` does not survive. The
    password runs to the **last** ``@`` of the segment, because an ``@``
    may be part of it.

    Args:
        segment (str): One comma-separated piece of the authority, e.g.
            ``user:p@ss@host``.

    Returns:
        str: The segment with its password replaced by ``***`` (or the
        segment unchanged when it carries no ``user:password``).
    """
    if "@" not in segment:
        return segment
    userinfo, _, host = segment.rpartition("@")
    if ":" not in userinfo:
        return segment
    user, _, _password = userinfo.partition(":")
    return f"{user}:***@{host}"


def _mask_credentials(url: str) -> str:
    """Mask every ``user:password@`` pair in the authority of ``url``.

    Every ``scheme://authority`` in the string is matched — the URL may sit
    inside a sentence — and the authority is split on ``,`` so each host of
    a host list is masked separately.

    Args:
        url (str): The connection URL, possibly embedded in other text.

    Returns:
        str: The text with every authority password replaced by ``***``.
    """

    def _replace(match: re.Match[str]) -> str:
        authority = match["authority"]
        masked = ",".join(
            _mask_host_segment(segment) for segment in authority.split(",")
        )
        return match["scheme"] + masked

    return _URL_PATTERN.sub(_replace, url)


def mask_url_password(url: str) -> str:
    """Return ``url`` with its password replaced by ``***``.

    ``postgresql+asyncpg://user:pass@host:5432/db`` becomes
    ``postgresql+asyncpg://user:***@host:5432/db``. Only the password is
    replaced: the scheme, username, host, port, database and query string
    survive, so the reader still knows *which* endpoint was attempted. A
    password embedded with an ``@``, a comma-separated host list and a
    credential in the query string are all masked; a URL without a
    password (SQLite, a DSN on the environment variable only) comes back
    unchanged.

    Args:
        url (str): The connection URL, possibly carrying credentials.

    Returns:
        str: The URL safe to surface outside the process.
    """
    with contextlib.suppress(Exception):
        url = make_url(url).render_as_string(hide_password=True)
    url = _mask_credentials(url)
    return _QUERY_SECRET_PATTERN.sub(r"\g<prefix>***", url)


__all__: list[str] = [
    "mask_url_password",
]
