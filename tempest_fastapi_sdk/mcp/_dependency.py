"""The ``[fastmcp]`` extra, imported only when an MCP helper is called."""

from __future__ import annotations

MISSING_FASTMCP: str = (
    'MCP support needs the [fastmcp] extra: pip install "tempest-fastapi-sdk[fastmcp]"'
)
"""What the ``ImportError`` says when ``fastmcp`` is not installed."""


def require_fastmcp() -> None:
    """Fail with a message naming the extra when ``fastmcp`` is missing.

    Every public helper of :mod:`tempest_fastapi_sdk.mcp` calls this first,
    so ``import tempest_fastapi_sdk.mcp`` keeps working without the extra
    and the failure, when it comes, says what to install.

    Raises:
        ImportError: When ``fastmcp`` cannot be imported.
    """
    try:
        import fastmcp  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised without the extra
        raise ImportError(MISSING_FASTMCP) from exc


__all__: list[str] = ["MISSING_FASTMCP", "require_fastmcp"]
