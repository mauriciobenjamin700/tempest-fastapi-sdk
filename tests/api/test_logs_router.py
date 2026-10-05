"""Tests for tempest_fastapi_sdk.api.routers.logs."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import configure_logging, make_logs_router
from tempest_fastapi_sdk.api.routers.logs import resolve_log_files
from tempest_fastapi_sdk.core.logging import HTTP_500_LOG_FILE, HTTP_500_MARKER


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _seed_logs(tmp_path: Path) -> None:
    logger = configure_logging(
        level="DEBUG",
        logger_name="tempest.logs.router.test",
        log_dir=tmp_path,
    )
    logger.debug("a debug line")
    logger.info("hello info")
    logger.warning("a warning here")
    logger.error("boom error")
    logger.critical("critical meltdown")
    logger.error(
        "Unhandled exception during GET /x",
        extra={HTTP_500_MARKER: True, "request_id": "rid-1"},
    )


def _app(tmp_path: Path, *, token_secret: str = "") -> FastAPI:
    app = FastAPI()
    app.include_router(
        make_logs_router(
            log_dir=tmp_path,
            token_secret=token_secret,
            allow_unauthenticated=not token_secret,
        ),
    )
    return app


@pytest.mark.asyncio
async def test_all_source_merges_every_level(tmp_path: Path) -> None:
    _seed_logs(tmp_path)
    async with _client(_app(tmp_path)) as client:
        response = await client.get("/logs")
    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 6


@pytest.mark.asyncio
async def test_500_source_returns_only_marked_records(tmp_path: Path) -> None:
    _seed_logs(tmp_path)
    async with _client(_app(tmp_path)) as client:
        response = await client.get("/logs", params={"source": "500"})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["request_id"] == "rid-1"


@pytest.mark.asyncio
async def test_error_source_includes_the_500(tmp_path: Path) -> None:
    _seed_logs(tmp_path)
    async with _client(_app(tmp_path)) as client:
        response = await client.get("/logs", params={"source": "error"})
    assert response.json()["total"] == 2


@pytest.mark.asyncio
async def test_message_substring_filter(tmp_path: Path) -> None:
    _seed_logs(tmp_path)
    async with _client(_app(tmp_path)) as client:
        response = await client.get("/logs", params={"q": "WARNING"})
    body = response.json()
    assert body["total"] == 1
    assert "warning" in body["items"][0]["message"].lower()


@pytest.mark.asyncio
async def test_newest_first_ordering(tmp_path: Path) -> None:
    _seed_logs(tmp_path)
    async with _client(_app(tmp_path)) as client:
        response = await client.get("/logs")
    items = response.json()["items"]
    timestamps = [item["timestamp"] for item in items]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.asyncio
async def test_pagination_slices_and_counts_pages(tmp_path: Path) -> None:
    _seed_logs(tmp_path)
    async with _client(_app(tmp_path)) as client:
        response = await client.get("/logs", params={"page": 2, "page_size": 4})
    body = response.json()
    assert body["total"] == 6
    assert body["pages"] == 2
    assert body["page"] == 2
    assert len(body["items"]) == 2


@pytest.mark.asyncio
async def test_missing_files_return_empty_page(tmp_path: Path) -> None:
    async with _client(_app(tmp_path)) as client:
        response = await client.get("/logs")
    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 0
    assert body["items"] == []


@pytest.mark.asyncio
async def test_token_required_when_secret_set(tmp_path: Path) -> None:
    _seed_logs(tmp_path)
    app = _app(tmp_path, token_secret="s3cret")
    async with _client(app) as client:
        denied = await client.get("/logs")
        denied_delete = await client.delete("/logs")
        allowed = await client.get("/logs", headers={"X-Token": "s3cret"})
    assert denied.status_code == 401
    assert denied_delete.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["total"] == 6


class TestEmptySecretIsRefused:
    """``make_logs_router`` fails closed when no secret is configured.

    ``make_token_dependency`` reads an empty secret as "nothing to check",
    so a router built from an unset ``TOKEN_SECRET`` used to answer
    ``GET`` and ``DELETE`` with ``200`` to anyone. The factory now refuses
    that at construction unless the caller opts in by name.
    """

    @pytest.mark.parametrize("secret", ["", " ", "\t", " \t\n "])
    def test_empty_or_blank_secret_raises(self, tmp_path: Path, secret: str) -> None:
        with pytest.raises(ValueError, match="non-empty token_secret"):
            make_logs_router(log_dir=tmp_path, token_secret=secret)

    def test_default_secret_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="allow_unauthenticated=True"):
            make_logs_router(log_dir=tmp_path)

    def test_message_names_the_prefix_and_header(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError) as caught:
            make_logs_router(
                log_dir=tmp_path,
                prefix="/audit",
                header_name="X-Audit-Key",
            )

        message = str(caught.value)
        assert "GET /audit" in message
        assert "DELETE /audit" in message
        assert "X-Audit-Key" in message
        assert "tempest secrets init" in message

    @pytest.mark.asyncio
    async def test_opt_in_answers_without_a_token(self, tmp_path: Path) -> None:
        _seed_logs(tmp_path)
        app = FastAPI()
        app.include_router(
            make_logs_router(log_dir=tmp_path, allow_unauthenticated=True),
        )
        async with _client(app) as client:
            read = await client.get("/logs")
            deleted = await client.delete("/logs")

        assert read.status_code == 200
        assert read.json()["total"] == 6
        assert deleted.status_code == 200

    @pytest.mark.asyncio
    async def test_opt_in_does_not_weaken_a_secret(self, tmp_path: Path) -> None:
        app = FastAPI()
        app.include_router(
            make_logs_router(
                log_dir=tmp_path,
                token_secret="s3cret",
                allow_unauthenticated=True,
            ),
        )
        async with _client(app) as client:
            denied = await client.get("/logs")
            denied_delete = await client.delete("/logs")
            allowed = await client.get("/logs", headers={"X-Token": "s3cret"})

        assert denied.status_code == 401
        assert denied_delete.status_code == 401
        assert allowed.status_code == 200

    @pytest.mark.asyncio
    async def test_padded_secret_is_compared_stripped(self, tmp_path: Path) -> None:
        """A padded ``TOKEN_SECRET`` still matches the value clients send.

        ``httpx`` refuses to send a padded header value and uvicorn's
        ``h11`` parser strips one on arrival, so comparing against the
        padded string would lock those clients out.
        """
        app = FastAPI()
        app.include_router(
            make_logs_router(log_dir=tmp_path, token_secret="  s3cret\n"),
        )
        async with _client(app) as client:
            allowed = await client.get("/logs", headers={"X-Token": "s3cret"})
            denied = await client.get("/logs", headers={"X-Token": "wrong"})

        assert allowed.status_code == 200
        assert denied.status_code == 401


@pytest.mark.asyncio
async def test_naive_start_bound_is_read_as_utc(tmp_path: Path) -> None:
    """An offset-free bound must filter, not crash.

    Pydantic accepts ``2020-01-01T00:00:00`` and hands back a **naive**
    datetime; log timestamps are always aware (the formatter writes ``...Z``).
    Comparing the two raised ``TypeError`` — a 500 on a well-formed request.
    """
    _seed_logs(tmp_path)
    async with _client(_app(tmp_path)) as client:
        response = await client.get("/logs", params={"start": "2020-01-01T00:00:00"})
    assert response.status_code == 200
    assert response.json()["total"] == 6


@pytest.mark.asyncio
async def test_naive_date_only_bound_is_accepted(tmp_path: Path) -> None:
    _seed_logs(tmp_path)
    async with _client(_app(tmp_path)) as client:
        response = await client.get("/logs", params={"start": "2020-01-01"})
    assert response.status_code == 200
    assert response.json()["total"] == 6


@pytest.mark.asyncio
async def test_naive_future_bound_filters_everything_out(tmp_path: Path) -> None:
    _seed_logs(tmp_path)
    async with _client(_app(tmp_path)) as client:
        response = await client.get("/logs", params={"start": "2999-01-01T00:00:00"})
    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_aware_bound_still_works(tmp_path: Path) -> None:
    _seed_logs(tmp_path)
    async with _client(_app(tmp_path)) as client:
        response = await client.get(
            "/logs", params={"end": "2999-01-01T00:00:00+00:00"}
        )
    assert response.status_code == 200
    assert response.json()["total"] == 6


@pytest.mark.asyncio
async def test_per_file_cap_keeps_the_newest_records(tmp_path: Path) -> None:
    """The read is bounded, and it is the tail that survives.

    Without a cap the endpoint materialized every line of every selected file
    before paginating, so a multi-gigabyte log directory took the worker down.
    """
    logger = configure_logging(
        level="INFO",
        logger_name="tempest.logs.router.cap",
        log_dir=tmp_path,
    )
    for index in range(10):
        logger.info("line %d", index)

    app = FastAPI()
    app.include_router(
        make_logs_router(
            log_dir=tmp_path,
            max_records_per_file=3,
            allow_unauthenticated=True,
        ),
    )
    async with _client(app) as client:
        response = await client.get("/logs", params={"source": "info"})

    body = response.json()
    assert body["total"] == 3
    assert [item["message"] for item in body["items"]] == [
        "line 9",
        "line 8",
        "line 7",
    ]


class TestResolveLogFiles:
    """The level-to-filename map is the SDK's layout, not the caller's."""

    def test_all_excludes_the_500_stream_by_default(self, tmp_path: Path) -> None:
        """Reading ``all`` must not list a 500 record twice.

        Every record in ``500.log`` is also in ``error.log``, so a read
        that merged both would show it once per file.
        """
        names = [path.name for path in resolve_log_files(tmp_path, "all")]

        assert "error.log" in names
        assert HTTP_500_LOG_FILE not in names

    def test_all_includes_it_when_asked(self, tmp_path: Path) -> None:
        """Truncating ``all`` and leaving the 500 stream is the surprise."""
        names = [
            path.name
            for path in resolve_log_files(tmp_path, "all", include_http_500=True)
        ]

        assert HTTP_500_LOG_FILE in names

    def test_a_level_resolves_to_its_own_file(self, tmp_path: Path) -> None:
        assert [path.name for path in resolve_log_files(tmp_path, "warning")] == [
            "warning.log",
        ]

    def test_paths_are_rooted_at_the_given_directory(self, tmp_path: Path) -> None:
        for path in resolve_log_files(tmp_path, "all"):
            assert path.parent == tmp_path

    def test_accepts_a_string_directory(self, tmp_path: Path) -> None:
        """Callers hold ``LOG_DIR`` as a ``str``; making them wrap it is noise."""
        assert resolve_log_files(str(tmp_path), "info") == resolve_log_files(
            tmp_path,
            "info",
        )


@pytest.mark.asyncio
async def test_delete_truncates_one_level(tmp_path: Path) -> None:
    _seed_logs(tmp_path)
    async with _client(_app(tmp_path)) as client:
        response = await client.delete("/logs", params={"source": "info"})

        assert response.status_code == 200
        assert response.json()["cleared"] == ["info.log"]
        assert (tmp_path / "info.log").read_text() == ""
        assert (tmp_path / "error.log").read_text() != ""


@pytest.mark.asyncio
async def test_delete_all_reaches_the_500_stream(tmp_path: Path) -> None:
    """A clear that left the isolated 500 file behind is the surprise."""
    _seed_logs(tmp_path)
    async with _client(_app(tmp_path)) as client:
        response = await client.delete("/logs")

        assert HTTP_500_LOG_FILE in response.json()["cleared"]
        assert (tmp_path / HTTP_500_LOG_FILE).read_text() == ""


@pytest.mark.asyncio
async def test_delete_leaves_the_files_in_place(tmp_path: Path) -> None:
    """Truncate, never unlink.

    ``configure_logging`` handlers hold an open descriptor on each path;
    deleting the file leaves them writing to an inode nothing can read
    back, and the endpoint would look like it worked exactly once.
    """
    _seed_logs(tmp_path)
    async with _client(_app(tmp_path)) as client:
        await client.delete("/logs")

    assert (tmp_path / "info.log").exists()
    assert (tmp_path / "error.log").exists()


@pytest.mark.asyncio
async def test_delete_then_read_returns_nothing(tmp_path: Path) -> None:
    _seed_logs(tmp_path)
    async with _client(_app(tmp_path)) as client:
        await client.delete("/logs")
        response = await client.get("/logs")

        assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_delete_is_gated_by_the_same_token(tmp_path: Path) -> None:
    """The destructive verb must not be looser than the read."""
    _seed_logs(tmp_path)
    async with _client(_app(tmp_path, token_secret="s3cret")) as client:
        refused = await client.delete("/logs")

        assert refused.status_code == 401
        assert (tmp_path / "info.log").read_text() != ""

        allowed = await client.delete("/logs", headers={"X-Token": "s3cret"})

        assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_delete_creates_a_missing_file(tmp_path: Path) -> None:
    """The post-condition is "empty", which an absent file already is."""
    async with _client(_app(tmp_path)) as client:
        response = await client.delete("/logs", params={"source": "critical"})

        assert response.status_code == 200
        assert (tmp_path / "critical.log").read_text() == ""
