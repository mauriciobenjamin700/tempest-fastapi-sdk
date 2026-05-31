"""Structured log-reading endpoint backed by the on-disk JSON files."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from starlette.concurrency import run_in_threadpool

from tempest_fastapi_sdk.api.dependencies.auth import make_token_dependency
from tempest_fastapi_sdk.core.logging import (
    HTTP_500_LOG_FILE,
    LEVEL_LOG_FILES,
)
from tempest_fastapi_sdk.schemas.logs import LogEntrySchema
from tempest_fastapi_sdk.schemas.pagination import BasePaginationSchema

logger = logging.getLogger(__name__)

LogSource = Literal[
    "all",
    "debug",
    "info",
    "warning",
    "error",
    "critical",
    "500",
]
"""Selectable log source for the ``/logs`` endpoint.

``"all"`` merges every per-level file (excluding ``500.log`` to avoid
duplicating ``error.log`` rows); the rest map to a single file.
"""

_LEVEL_FILE_BY_NAME: dict[str, str] = {
    logging.getLevelName(levelno).lower(): filename
    for levelno, filename in LEVEL_LOG_FILES.items()
}


def _parse_timestamp(value: str) -> datetime | None:
    """Parse an ISO-8601 ``...Z`` timestamp to an aware ``datetime``.

    Args:
        value (str): The timestamp string from a log record.

    Returns:
        datetime | None: The parsed datetime, or ``None`` when the
        value is missing or malformed.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _resolve_files(log_dir: Path, source: LogSource) -> list[Path]:
    """Resolve the log files to read for a given ``source``.

    Args:
        log_dir (Path): Directory holding the log files.
        source (LogSource): The requested source selector.

    Returns:
        list[Path]: The files to read (existing or not — callers skip
        missing ones).
    """
    if source == "all":
        return [log_dir / filename for filename in LEVEL_LOG_FILES.values()]
    if source == "500":
        return [log_dir / HTTP_500_LOG_FILE]
    return [log_dir / _LEVEL_FILE_BY_NAME[source]]


def _read_entries(files: list[Path]) -> list[dict[str, Any]]:
    """Read and JSON-parse every non-empty line from ``files``.

    Malformed lines and missing files are skipped silently so a single
    corrupt line never breaks the endpoint.

    Args:
        files (list[Path]): The log files to read.

    Returns:
        list[dict[str, Any]]: The parsed records across all files.
    """
    entries: list[dict[str, Any]] = []
    for file_path in files:
        if not file_path.exists():
            continue
        with file_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    entries.append(parsed)
    return entries


def make_logs_router(
    *,
    log_dir: str | Path = "logs",
    token_secret: str = "",
    prefix: str = "/logs",
    tag: str = "logs",
    header_name: str = "X-Token",
    default_page_size: int = 20,
    max_page_size: int = 200,
) -> APIRouter:
    """Build a router that serves the on-disk JSON logs, paginated.

    Mounts ``GET <prefix>`` which reads the files produced by
    :func:`tempest_fastapi_sdk.configure_logging` (called with
    ``log_dir=...``), filters them, and returns a
    :class:`BasePaginationSchema` of :class:`LogEntrySchema`. Newest
    records come first.

    The endpoint is gated by a shared-secret ``X-Token`` header via
    :func:`make_token_dependency`. An empty ``token_secret`` disables
    the check (development only) — never ship log access unauthenticated
    in production, the payload exposes tracebacks and request metadata.

    Args:
        log_dir (str | Path): Directory holding the log files. Must
            match the ``log_dir`` passed to ``configure_logging``.
            Defaults to ``"logs"``.
        token_secret (str): Shared secret for the ``X-Token`` header.
            Empty disables auth (dev only).
        prefix (str): URL prefix for the router. Defaults to
            ``"/logs"`` — mount it at the application root, not under
            ``/api``.
        tag (str): OpenAPI tag applied to the endpoint.
        header_name (str): Auth header name. Defaults to ``"X-Token"``.
        default_page_size (int): Page size when the caller omits it.
        max_page_size (int): Upper bound enforced on ``page_size``.

    Returns:
        APIRouter: A router ready to ``include_router(...)`` on the app.
    """
    router = APIRouter(prefix=prefix, tags=[tag])
    base_dir = Path(log_dir)
    require_token = make_token_dependency(token_secret, header_name=header_name)

    @router.get(
        "",
        summary="Read structured application logs",
        response_model=BasePaginationSchema[LogEntrySchema],
        dependencies=[Depends(require_token)],
    )
    async def read_logs(
        source: LogSource = Query(
            default="all",
            description=(
                "Which log file(s) to read. 'all' merges every level; "
                "'500' returns only isolated unhandled-500 records."
            ),
        ),
        q: str | None = Query(
            default=None,
            description="Case-insensitive substring match on the message.",
        ),
        start: datetime | None = Query(
            default=None,
            description="Only records at or after this ISO-8601 instant.",
        ),
        end: datetime | None = Query(
            default=None,
            description="Only records at or before this ISO-8601 instant.",
        ),
        page: int = Query(default=1, ge=1, description="Page number (1-indexed)."),
        page_size: int = Query(
            default=default_page_size,
            ge=1,
            description="Items per page.",
        ),
    ) -> BasePaginationSchema[LogEntrySchema]:
        """Return a paginated, filtered page of log records (newest first).

        Args:
            source (LogSource): Which file(s) to read.
            q (str | None): Case-insensitive message substring filter.
            start (datetime | None): Lower bound on the record timestamp.
            end (datetime | None): Upper bound on the record timestamp.
            page (int): The 1-indexed page number.
            page_size (int): Items per page (capped at ``max_page_size``).

        Returns:
            BasePaginationSchema[LogEntrySchema]: The page of records
            with pagination metadata.
        """
        size = min(page_size, max_page_size)
        files = _resolve_files(base_dir, source)
        entries = await run_in_threadpool(_read_entries, files)

        needle = q.lower() if q else None
        filtered: list[dict[str, Any]] = []
        for entry in entries:
            if needle is not None:
                message = str(entry.get("message", "")).lower()
                if needle not in message:
                    continue
            if start is not None or end is not None:
                moment = _parse_timestamp(str(entry.get("timestamp", "")))
                if moment is None:
                    continue
                if start is not None and moment < start:
                    continue
                if end is not None and moment > end:
                    continue
            filtered.append(entry)

        filtered.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)

        total = len(filtered)
        pages = (total + size - 1) // size if total else 0
        offset = (page - 1) * size
        window = filtered[offset : offset + size]

        return BasePaginationSchema[LogEntrySchema](
            items=[LogEntrySchema.model_validate(item) for item in window],
            total=total,
            page=page,
            page_size=size,
            pages=pages,
        )

    return router


__all__: list[str] = [
    "LogSource",
    "make_logs_router",
]
