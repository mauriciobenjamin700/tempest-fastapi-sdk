"""Mask the password embedded in a connection URL.

A DSN that carries credentials — ``postgresql+asyncpg://user:pass@host/db``,
``redis://:pass@host/0``, ``amqp://user:pass@host/`` — must never reach a
log line, an HTTP payload or a CLI report whole. This module is the single
place that turns one into its safe form, so ``AsyncDatabaseManager.db_url_safe``
and every diagnostic that prints a URL agree on the output.
"""

from __future__ import annotations

import re

from sqlalchemy.engine import make_url

_CREDENTIAL_PATTERN: re.Pattern[str] = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)"
    r"(?P<user>[^:@/?#\s]*):(?P<password>[^@/?#\s]*)@"
)
"""Fallback matcher for a ``scheme://user:password@`` prefix.

Used only when SQLAlchemy cannot parse the URL: a DSN the dialect registry
rejects still deserves masking rather than leaking the password verbatim.
"""


def mask_url_password(url: str) -> str:
    """Return ``url`` with its password replaced by ``***``.

    ``postgresql+asyncpg://user:pass@host:5432/db`` becomes
    ``postgresql+asyncpg://user:***@host:5432/db``. Only the password is
    replaced: the scheme, username, host, port, database and query string
    survive, so the reader still knows *which* endpoint was attempted. A
    URL without a password (SQLite, a DSN on the environment variable only)
    comes back unchanged.

    Args:
        url (str): The connection URL, possibly carrying credentials.

    Returns:
        str: The URL safe to surface outside the process.
    """
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return _CREDENTIAL_PATTERN.sub(r"\g<scheme>\g<user>:***@", url)


__all__: list[str] = [
    "mask_url_password",
]
