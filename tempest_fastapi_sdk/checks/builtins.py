"""Built-in checks for common settings misconfigurations.

Each check reads attributes off the ``context`` (the settings object)
defensively with ``getattr`` and simply returns nothing when the
attribute is absent — so they work against any ``*Settings`` shape and
never assume a field exists. They are registered on the process-wide
:data:`~tempest_fastapi_sdk.checks.registry.default_registry` at import
time under the ``"security"`` / ``"database"`` / ``"deployment"`` tags.
"""

from __future__ import annotations

from typing import Any

from tempest_fastapi_sdk.checks.messages import CheckMessage, info, warning
from tempest_fastapi_sdk.checks.registry import default_registry

#: Minimum acceptable length for a signing secret.
MIN_SECRET_LENGTH: int = 32

#: Attributes that, if present, are treated as signing secrets.
_SECRET_ATTRS: tuple[str, ...] = (
    "JWT_SECRET",
    "JWT_SECRET_KEY",
    "SECRET_KEY",
    "TOKEN_SECRET",
)


def _first_present(context: Any, *names: str) -> tuple[str, Any] | None:
    """Return the first ``(name, value)`` present and non-``None``.

    Args:
        context (Any): The settings object.
        *names (str): Candidate attribute names, in priority order.

    Returns:
        tuple[str, Any] | None: The first hit, or ``None``.
    """
    for name in names:
        value = getattr(context, name, None)
        if value is not None:
            return name, value
    return None


def check_secrets(context: Any) -> list[CheckMessage]:
    """Flag empty or weak signing secrets.

    Args:
        context (Any): The settings object.

    Returns:
        list[CheckMessage]: One message per problematic secret.
    """
    if context is None:
        return []
    messages: list[CheckMessage] = []
    for name in _SECRET_ATTRS:
        value = getattr(context, name, None)
        if value is None:
            continue
        text = str(value)
        if text == "":
            messages.append(
                warning(
                    f"{name} is empty — token verification is effectively disabled.",
                    hint="Set a random secret in production (dev-only when empty).",
                    id="security.W001",
                )
            )
        elif len(text) < MIN_SECRET_LENGTH:
            messages.append(
                warning(
                    f"{name} is only {len(text)} chars; use at least "
                    f"{MIN_SECRET_LENGTH}.",
                    hint="Generate one with `tempest secrets rotate`.",
                    id="security.W002",
                )
            )
    return messages


def check_debug(context: Any) -> list[CheckMessage]:
    """Note when debug mode is on.

    Args:
        context (Any): The settings object.

    Returns:
        list[CheckMessage]: A single info message when debug is on.
    """
    if context is None:
        return []
    debug = getattr(context, "DEBUG", None)
    if debug:
        return [
            info(
                "DEBUG is enabled.",
                hint="Ensure DEBUG is off in production (it leaks internals).",
                id="deployment.I001",
            )
        ]
    return []


def check_cors(context: Any) -> list[CheckMessage]:
    """Flag wildcard CORS origins combined with credentials.

    A browser rejects ``Access-Control-Allow-Origin: *`` together with
    credentials, and allowing both is a security smell.

    Args:
        context (Any): The settings object.

    Returns:
        list[CheckMessage]: A warning when the combination is present.
    """
    if context is None:
        return []
    origins_hit = _first_present(context, "CORS_ORIGINS", "ALLOW_ORIGINS")
    creds_hit = _first_present(context, "CORS_ALLOW_CREDENTIALS", "ALLOW_CREDENTIALS")
    if origins_hit is None:
        return []
    _, origins = origins_hit
    wildcard = origins == "*" or (
        isinstance(origins, (list, tuple, set)) and "*" in origins
    )
    credentials = bool(creds_hit and creds_hit[1])
    if wildcard and credentials:
        return [
            warning(
                "CORS allows all origins ('*') with credentials enabled.",
                hint="List explicit origins; browsers block '*' + credentials.",
                id="security.W003",
            )
        ]
    return []


def check_database(context: Any) -> list[CheckMessage]:
    """Warn when SQLite is configured outside debug mode.

    Args:
        context (Any): The settings object.

    Returns:
        list[CheckMessage]: A warning when a non-debug build points at
        SQLite.
    """
    if context is None:
        return []
    hit = _first_present(context, "DATABASE_URL", "DB_URL", "database_url")
    if hit is None:
        return []
    _, url = hit
    if str(url).startswith("sqlite") and not getattr(context, "DEBUG", False):
        return [
            warning(
                "DATABASE_URL points at SQLite while DEBUG is off.",
                hint="Use PostgreSQL in production; SQLite is for development.",
                id="database.W001",
            )
        ]
    return []


def check_bind_host(context: Any) -> list[CheckMessage]:
    """Note when the server binds to all interfaces.

    Args:
        context (Any): The settings object.

    Returns:
        list[CheckMessage]: An info message when bound to ``0.0.0.0``.
    """
    if context is None:
        return []
    hit = _first_present(context, "SERVER_HOST", "HOST")
    if hit is not None and str(hit[1]) == "0.0.0.0":
        return [
            info(
                f"{hit[0]} binds to 0.0.0.0 (all interfaces).",
                hint="Prefer 127.0.0.1 for internal services; 0.0.0.0 only "
                "when a separate origin must reach it.",
                id="deployment.I002",
            )
        ]
    return []


def register_builtin_checks() -> None:
    """Register every built-in check on the default registry.

    Idempotent-safe to call once at import; re-registering would
    duplicate messages, so it is guarded by the module-level flag.
    """
    default_registry.register(check_secrets, "settings", "security")
    default_registry.register(check_debug, "settings", "deployment")
    default_registry.register(check_cors, "settings", "security")
    default_registry.register(check_database, "settings", "database")
    default_registry.register(check_bind_host, "settings", "deployment")


register_builtin_checks()


__all__: list[str] = [
    "MIN_SECRET_LENGTH",
    "check_bind_host",
    "check_cors",
    "check_database",
    "check_debug",
    "check_secrets",
    "register_builtin_checks",
]
