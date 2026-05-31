"""Tests for tempest_fastapi_sdk.api.routers.logs."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import configure_logging, make_logs_router
from tempest_fastapi_sdk.core.logging import HTTP_500_MARKER


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
        make_logs_router(log_dir=tmp_path, token_secret=token_secret),
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
        allowed = await client.get("/logs", headers={"X-Token": "s3cret"})
    assert denied.status_code == 401
    assert allowed.status_code == 200
