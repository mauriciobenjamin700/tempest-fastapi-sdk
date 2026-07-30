"""Tests for the admin logs traceback view and the markdown/JSON export.

Diagnosing a 500 from the admin panel used to be impossible: the logs table
rendered four columns (timestamp, level, logger, message) and dropped every other
field, so the `exception` value — the formatted traceback the SDK's exception
handlers write with ``exc_info=True`` — was on disk but invisible. Reading it
meant shelling into the server. There was no way to hand the trace to someone
either.

These tests pin both halves: the traceback (and the request context around it)
reaches the page, and the export renders the *filtered* selection as markdown or
JSON with the traceback intact.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.api.routers.logs import (
    render_entries_json,
    render_entries_markdown,
)

SECRET = "x" * 48

TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/app/src/api/routers/sse.py", line 73, in broadcast_event\n'
    "    event=payload.event.value if payload.event else None,\n"
    "          ^^^^^^^^^^^^^^^^^^^\n"
    "AttributeError: 'str' object has no attribute 'value'"
)


class TraceUser(BaseUserModel):
    __tablename__ = "admin_trace_users"


def _client(app: FastAPI) -> AsyncClient:
    """Build an ASGI client bound to ``app``."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login(client: AsyncClient) -> None:
    """Authenticate as the seeded admin so the session cookie is set."""
    await client.post(
        "/admin/login",
        data={"identifier": "root@example.com", "password": "hunter2"},
    )


def _write_logs(log_dir: Path) -> None:
    """Seed one INFO record and one ERROR record carrying a traceback.

    The 500 record also lands in ``500.log``, mirroring what
    ``configure_logging`` does, so the ``source=500`` filter has something to
    select.
    """
    info = {
        "timestamp": "2026-07-30T12:00:00Z",
        "level": "INFO",
        "logger": "app.orders",
        "message": "order placed alpha",
    }
    error = {
        "timestamp": "2026-07-30T12:40:35Z",
        "level": "ERROR",
        "logger": "tempest_fastapi_sdk.api.handlers",
        "message": "Unhandled exception during POST /api/auth/password-change",
        "exception": TRACEBACK,
        "path": "/api/auth/password-change",
        "method": "POST",
        "request_id": "b1ddc2ad-3649-4306-82b9-d442dc8f864b",
        "http_500": True,
    }
    (log_dir / "info.log").write_text(json.dumps(info) + "\n", encoding="utf-8")
    (log_dir / "error.log").write_text(json.dumps(error) + "\n", encoding="utf-8")
    (log_dir / "500.log").write_text(json.dumps(error) + "\n", encoding="utf-8")


@pytest.fixture
async def app_with_logs(tmp_path: Path) -> AsyncIterator[FastAPI]:
    """Boot an admin app whose log directory holds a 500 with a traceback."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_logs(log_dir)

    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = TraceUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        await session.commit()

    app = FastAPI()
    app.include_router(
        make_admin_router(
            AdminSite(title="Trace Admin"),
            db=db,
            auth_backend=UserModelAuthBackend(TraceUser),
            secret_key=SECRET,
            cookie_secure=False,
            show_logs=True,
            log_dir=str(log_dir),
        )
    )
    yield app
    await db.drop_tables()
    await db.disconnect()


class TestTracebackOnThePage:
    """The traceback and its request context must render, not be dropped."""

    async def test_traceback_is_rendered(self, app_with_logs: FastAPI) -> None:
        """The stored traceback reaches the HTML instead of staying on disk."""
        async with _client(app_with_logs) as client:
            await _login(client)
            response = await client.get("/admin/logs")

        assert response.status_code == 200
        assert (
            "AttributeError: &#39;str&#39; object has no attribute &#39;value&#39;"
            in (response.text)
        )
        assert "tempest-admin-logs__trace" in response.text

    async def test_traceback_starts_collapsed(self, app_with_logs: FastAPI) -> None:
        """It ships inside ``<details>`` so a page of 500s stays scannable."""
        async with _client(app_with_logs) as client:
            await _login(client)
            response = await client.get("/admin/logs")

        assert "<details" in response.text
        assert "<details open" not in response.text
        assert "tempest-admin-logs__trace-hint" in response.text

    async def test_whole_item_is_the_toggle(self, app_with_logs: FastAPI) -> None:
        """The record's own message is the click target, not a separate link.

        The message lives inside the ``<summary>``, so clicking anywhere on the
        entry reveals its traceback — fewer, larger targets instead of hunting a
        word at the bottom of the cell.
        """
        async with _client(app_with_logs) as client:
            await _login(client)
            response = await client.get("/admin/logs", params={"source": "500"})

        summary_open = response.text.index("<summary>")
        summary_close = response.text.index("</summary>", summary_open)
        summary = response.text[summary_open:summary_close]
        assert "Unhandled exception during POST /api/auth/password-change" in summary
        assert "b1ddc2ad-3649-4306-82b9-d442dc8f864b" in summary

    async def test_request_context_is_rendered(self, app_with_logs: FastAPI) -> None:
        """``path`` / ``method`` / ``request_id`` are the correlation keys."""
        async with _client(app_with_logs) as client:
            await _login(client)
            response = await client.get("/admin/logs")

        assert "/api/auth/password-change" in response.text
        assert "b1ddc2ad-3649-4306-82b9-d442dc8f864b" in response.text

    async def test_record_without_traceback_has_no_details_block(
        self, app_with_logs: FastAPI
    ) -> None:
        """An INFO row must not grow an empty Traceback toggle."""
        async with _client(app_with_logs) as client:
            await _login(client)
            response = await client.get("/admin/logs", params={"source": "info"})

        assert "order placed alpha" in response.text
        assert "tempest-admin-logs__trace" not in response.text

    async def test_export_links_are_offered(self, app_with_logs: FastAPI) -> None:
        """Both formats are reachable from the page."""
        async with _client(app_with_logs) as client:
            await _login(client)
            response = await client.get("/admin/logs")

        assert "format=md" in response.text
        assert "format=json" in response.text

    async def test_export_links_carry_the_active_filter(
        self, app_with_logs: FastAPI
    ) -> None:
        """Exporting from a filtered view must export that view."""
        async with _client(app_with_logs) as client:
            await _login(client)
            response = await client.get(
                "/admin/logs", params={"source": "500", "q": "password"}
            )

        assert "source=500" in response.text
        assert "q=password" in response.text


class TestMarkdownExport:
    """``?format=md`` renders a document ready to paste into an issue."""

    async def test_downloads_as_an_attachment(self, app_with_logs: FastAPI) -> None:
        """The response is a file, not a page."""
        async with _client(app_with_logs) as client:
            await _login(client)
            response = await client.get("/admin/logs/export", params={"format": "md"})

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert "attachment" in response.headers["content-disposition"]
        assert ".md" in response.headers["content-disposition"]

    async def test_traceback_is_in_a_fenced_block(self, app_with_logs: FastAPI) -> None:
        """A fence is what keeps the indentation readable after a paste."""
        async with _client(app_with_logs) as client:
            await _login(client)
            response = await client.get("/admin/logs/export", params={"format": "md"})

        assert "```pytb" in response.text
        assert "AttributeError: 'str' object has no attribute 'value'" in response.text

    async def test_context_fields_are_listed(self, app_with_logs: FastAPI) -> None:
        """The request that failed is named next to the trace."""
        async with _client(app_with_logs) as client:
            await _login(client)
            response = await client.get("/admin/logs/export", params={"format": "md"})

        assert "**path:**" in response.text
        assert "/api/auth/password-change" in response.text
        assert "**request_id:**" in response.text

    async def test_header_states_source_and_count(self, app_with_logs: FastAPI) -> None:
        """A pasted document has to say what it is."""
        async with _client(app_with_logs) as client:
            await _login(client)
            response = await client.get(
                "/admin/logs/export", params={"format": "md", "source": "500"}
            )

        assert "# Application logs" in response.text
        assert "**Source:** `500`" in response.text
        assert "**Records:** 1" in response.text

    async def test_message_filter_is_honored_and_echoed(
        self, app_with_logs: FastAPI
    ) -> None:
        """The export covers the filtered selection, and says it was filtered."""
        async with _client(app_with_logs) as client:
            await _login(client)
            response = await client.get(
                "/admin/logs/export", params={"format": "md", "q": "password"}
            )

        assert "**Message filter:** `password`" in response.text
        assert "**Records:** 1" in response.text
        assert "order placed alpha" not in response.text

    async def test_empty_selection_is_self_describing(
        self, app_with_logs: FastAPI
    ) -> None:
        """No matches still yields a document, not an empty file."""
        async with _client(app_with_logs) as client:
            await _login(client)
            response = await client.get(
                "/admin/logs/export", params={"format": "md", "q": "nothing-matches"}
            )

        assert response.status_code == 200
        assert "**Records:** 0" in response.text
        assert "_No records matched the current filter._" in response.text


class TestJsonExport:
    """``?format=json`` ships the records verbatim for tooling."""

    async def test_downloads_parseable_json(self, app_with_logs: FastAPI) -> None:
        """The payload is a JSON array of the raw records."""
        async with _client(app_with_logs) as client:
            await _login(client)
            response = await client.get(
                "/admin/logs/export", params={"format": "json", "source": "500"}
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert "attachment" in response.headers["content-disposition"]
        payload = json.loads(response.text)
        assert len(payload) == 1
        assert payload[0]["exception"] == TRACEBACK
        assert payload[0]["request_id"] == "b1ddc2ad-3649-4306-82b9-d442dc8f864b"

    async def test_no_field_is_dropped(self, app_with_logs: FastAPI) -> None:
        """Whatever the application logged survives the round trip."""
        async with _client(app_with_logs) as client:
            await _login(client)
            response = await client.get(
                "/admin/logs/export", params={"format": "json", "source": "500"}
            )

        record = json.loads(response.text)[0]
        assert record["http_500"] is True
        assert record["method"] == "POST"


class TestExportIsGuarded:
    """The export inherits the admin session gate."""

    async def test_anonymous_cannot_export(self, app_with_logs: FastAPI) -> None:
        """Tracebacks are exactly the payload that must not be public."""
        async with _client(app_with_logs) as client:
            response = await client.get(
                "/admin/logs/export",
                params={"format": "md"},
                follow_redirects=False,
            )

        assert response.status_code in (302, 303, 307, 401, 403)
        assert TRACEBACK not in response.text


class TestRenderers:
    """The renderers are unit-testable without an app."""

    def test_markdown_states_truncation(self) -> None:
        """A capped export must not read as an exhaustive one."""
        entries = [
            {"timestamp": "2026-07-30T12:00:00Z", "level": "INFO", "message": "x"}
        ]

        rendered = render_entries_markdown(entries, truncated_from=1200)

        assert "**Truncated:** showing the 1 most recent of 1200" in rendered

    def test_markdown_numbers_entries(self) -> None:
        """Numbered headings let a reviewer point at one record."""
        entries = [
            {"timestamp": "t1", "level": "ERROR", "message": "first"},
            {"timestamp": "t2", "level": "ERROR", "message": "second"},
        ]

        rendered = render_entries_markdown(entries)

        assert "### 1. `ERROR` · t1" in rendered
        assert "### 2. `ERROR` · t2" in rendered

    def test_markdown_keeps_unknown_extra_fields(self) -> None:
        """An application's own ``extra=`` keys are not silently dropped."""
        entries = [
            {
                "timestamp": "t",
                "level": "WARNING",
                "message": "m",
                "tenant_id": "acme",
            }
        ]

        rendered = render_entries_markdown(entries)

        assert "`tenant_id=acme`" in rendered

    def test_json_survives_non_serializable_values(self) -> None:
        """One exotic value must not fail the whole export."""
        entries = [{"timestamp": "t", "level": "INFO", "message": "m", "obj": object()}]

        rendered = render_entries_json(entries)

        assert "<object object at" in rendered
