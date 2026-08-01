"""Structured log-reading endpoint backed by the on-disk JSON files."""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import UTC, datetime
from functools import partial
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

DEFAULT_MAX_RECORDS_PER_FILE: int = 20_000
"""How many of the newest records are read from each log file per request.

A bound is required, not tuning: without one the endpoint materialized every
line of every selected file before paginating, so a service whose log
directory had grown to gigabytes answered with a dead worker. 20k records per
file is far past any page a caller can reach (the largest page is
``max_page_size``) while keeping the read a bounded, predictable cost.
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


def _as_aware(moment: datetime) -> datetime:
    """Return ``moment`` in UTC, assuming UTC when it carries no offset.

    The ``start`` / ``end`` query parameters arrive through Pydantic, which
    accepts an offset-free value like ``2026-01-01T00:00:00`` and hands back
    a **naive** datetime. Log timestamps are always aware (the formatter
    writes ``...Z``), and comparing the two raises ``TypeError: can't
    compare offset-naive and offset-aware datetimes`` — an HTTP 500 on what
    is really a well-formed request. Reading a bare timestamp as UTC is the
    only interpretation consistent with the records it filters.

    Args:
        moment (datetime): The bound supplied by the caller.

    Returns:
        datetime: The same instant, guaranteed offset-aware.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


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


def _read_entries(
    files: list[Path],
    *,
    max_records_per_file: int = DEFAULT_MAX_RECORDS_PER_FILE,
) -> tuple[list[dict[str, Any]], bool]:
    """Read and JSON-parse the newest lines of each file in ``files``.

    Malformed lines and missing files are skipped silently so a single
    corrupt line never breaks the endpoint.

    Only the last ``max_records_per_file`` records of each file are kept,
    streamed through a bounded :class:`collections.deque`. The endpoint sorts
    newest-first and paginates, so older records were never reachable
    anyway — but reading the whole file to find that out meant a service with
    a multi-gigabyte log directory pinned that much memory per request, and
    the worker died before answering.

    Args:
        files (list[Path]): The log files to read.
        max_records_per_file (int): Cap on records kept per file.

    Returns:
        tuple[list[dict[str, Any]], bool]: The parsed records across all
        files, and whether any file was truncated by the cap — the caller
        surfaces that so a partial view never reads as a complete one.
    """
    entries: list[dict[str, Any]] = []
    truncated = False
    for file_path in files:
        if not file_path.exists():
            continue
        window: deque[dict[str, Any]] = deque(maxlen=max_records_per_file)
        seen = 0
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
                    seen += 1
                    window.append(parsed)
        if seen > max_records_per_file:
            truncated = True
        entries.extend(window)
    return entries, truncated


_MARKDOWN_CONTEXT_KEYS: tuple[str, ...] = (
    "path",
    "method",
    "status_code",
    "request_id",
    "http_500",
)
"""Record keys promoted to the summary table of an exported entry.

The formatter writes ``extra={...}`` fields as top-level keys, so a record
carries whatever the emitting call site attached. These are the ones worth
seeing first when reading a failure: what was requested and which correlation id
to grep for. Everything else still ships under "Other fields", so nothing an
application logged is lost in the export.
"""

_RESERVED_KEYS: frozenset[str] = frozenset(
    {"timestamp", "level", "logger", "name", "message", "exception"}
)
"""Keys rendered outside the extra-fields section, so they are not repeated."""


def _entry_to_markdown(entry: dict[str, Any], *, index: int) -> list[str]:
    """Render one log record as markdown lines.

    A traceback goes into a fenced block so it survives a paste into an issue
    with its indentation intact — which is the whole point of the export, since
    a traceback pasted as prose is unreadable.

    Args:
        entry (dict[str, Any]): The parsed log record.
        index (int): 1-based position in the export, used as the heading number
            so entries stay referenceable ("see #3") in a review thread.

    Returns:
        list[str]: The markdown lines for this record.
    """
    level = str(entry.get("level", "—"))
    timestamp = str(entry.get("timestamp", "—"))
    logger_name = str(entry.get("logger", entry.get("name", "—")))
    lines = [
        f"### {index}. `{level}` · {timestamp}",
        "",
        f"- **Logger:** `{logger_name}`",
        f"- **Message:** {entry.get('message', '')}",
    ]
    for key in _MARKDOWN_CONTEXT_KEYS:
        if key in entry and entry[key] is not None:
            lines.append(f"- **{key}:** `{entry[key]}`")

    extras = {
        key: value
        for key, value in entry.items()
        if key not in _RESERVED_KEYS and key not in _MARKDOWN_CONTEXT_KEYS
    }
    if extras:
        lines.append(
            "- **Other fields:** "
            + ", ".join(f"`{key}={value}`" for key, value in sorted(extras.items()))
        )

    traceback_text = entry.get("exception")
    if traceback_text:
        lines.extend(["", "```pytb", str(traceback_text).rstrip(), "```"])
    lines.append("")
    return lines


def render_entries_markdown(
    entries: list[dict[str, Any]],
    *,
    source: str = "all",
    query: str | None = None,
    truncated_from: int | None = None,
) -> str:
    """Render log records as a markdown document ready to paste into an issue.

    Built for the "a 500 happened, hand me the trace" loop: the on-disk records
    already carry the formatted traceback under ``exception`` (the SDK's
    exception handlers log with ``exc_info=True``), but reading it out of a
    paginated HTML table or a JSON blob is painful. This lays each record out
    with its context fields and puts the traceback in a fenced ``pytb`` block.

    Args:
        entries (list[dict[str, Any]]): The records to render, in the order they
            should appear (the callers pass newest-first).
        source (str): The log source the export came from, echoed in the header
            so a pasted document says what it is.
        query (str | None): The message filter that produced the selection, also
            echoed — an export of a filtered view is otherwise indistinguishable
            from a complete one.
        truncated_from (int | None): When the selection was capped, the total
            number of matching records. Stated in the header rather than left
            implicit, so a partial export never reads as an exhaustive one.

    Returns:
        str: The markdown document. A header is always present, so an empty
            selection still produces something self-describing.
    """
    header = [
        "# Application logs",
        "",
        f"- **Source:** `{source}`",
        f"- **Records:** {len(entries)}",
    ]
    if query:
        header.append(f"- **Message filter:** `{query}`")
    if truncated_from is not None:
        header.append(
            f"- **Truncated:** showing the {len(entries)} most recent of "
            f"{truncated_from} matching records"
        )
    header.append("")

    if not entries:
        header.extend(["_No records matched the current filter._", ""])
        return "\n".join(header)

    body: list[str] = []
    for position, entry in enumerate(entries, start=1):
        body.extend(_entry_to_markdown(entry, index=position))
    return "\n".join(header + body)


def render_entries_json(entries: list[dict[str, Any]]) -> str:
    """Render log records as a pretty-printed JSON array.

    The records are emitted verbatim — every field the application logged,
    traceback included — so the output can be fed to tooling rather than read.
    Non-serializable values fall back to their ``str()`` form so one exotic
    ``extra=`` value cannot fail the whole export.

    Args:
        entries (list[dict[str, Any]]): The records to render.

    Returns:
        str: The JSON document, indented for diff-friendliness.
    """
    return json.dumps(entries, indent=2, ensure_ascii=False, default=str)


def make_logs_router(
    *,
    log_dir: str | Path = "logs",
    token_secret: str = "",
    prefix: str = "/logs",
    tag: str = "logs",
    header_name: str = "X-Token",
    default_page_size: int = 20,
    max_page_size: int = 200,
    max_records_per_file: int = DEFAULT_MAX_RECORDS_PER_FILE,
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
        max_records_per_file (int): How many of the newest records are read
            from each file per request. See
            :data:`DEFAULT_MAX_RECORDS_PER_FILE` — the bound is what keeps a
            large log directory from being read into memory whole. When it
            bites, a warning is logged naming the source.

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
        entries, truncated = await run_in_threadpool(
            partial(_read_entries, files, max_records_per_file=max_records_per_file)
        )
        if truncated:
            logger.warning(
                "Log source %r exceeds %d records per file; older records were "
                "not read for this request.",
                source,
                max_records_per_file,
            )

        lower = _as_aware(start) if start is not None else None
        upper = _as_aware(end) if end is not None else None
        needle = q.lower() if q else None
        filtered: list[dict[str, Any]] = []
        for entry in entries:
            if needle is not None:
                message = str(entry.get("message", "")).lower()
                if needle not in message:
                    continue
            if lower is not None or upper is not None:
                moment = _parse_timestamp(str(entry.get("timestamp", "")))
                if moment is None:
                    continue
                if lower is not None and moment < lower:
                    continue
                if upper is not None and moment > upper:
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
    "DEFAULT_MAX_RECORDS_PER_FILE",
    "LogSource",
    "make_logs_router",
    "render_entries_json",
    "render_entries_markdown",
]
