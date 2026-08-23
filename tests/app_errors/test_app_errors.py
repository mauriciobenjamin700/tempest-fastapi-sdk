"""Tests for the app_errors module — service, model and router.

Each test here pins a decision that a production service paid for: the
truncation that keeps a crashed client's report, the ``user_id`` a client
must not be able to set, the nullable FK that keeps login-flow errors, and
the half-open date range that keeps the index usable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import BaseModel, BaseRepository, BaseUserModel
from tempest_fastapi_sdk.app_errors import (
    APP_ERROR_CODE_MAX_LENGTH,
    APP_ERROR_MESSAGE_MAX_LENGTH,
    APP_ERROR_TEXT_FIELD_MAX_LENGTH,
    APP_ERROR_TRUNCATION_SUFFIX,
    AppErrorFilterSchema,
    AppErrorReportSchema,
    AppErrorService,
    AppPlatform,
    make_app_error_model,
    make_app_error_router,
)


class _AppErrorUser(BaseUserModel):
    __tablename__ = "app_error_users"


_AppError = make_app_error_model(
    user_table="app_error_users",
    tablename="app_error_reports",
    class_name="_AppErrorReport",
)


def _service(session: AsyncSession) -> AppErrorService:
    """Build the service over the test table.

    Args:
        session (AsyncSession): The test session.

    Returns:
        AppErrorService: The service.
    """
    return AppErrorService(BaseRepository(session, model=_AppError))


def _report(**overrides: object) -> AppErrorReportSchema:
    """Build a report with sane defaults.

    Args:
        **overrides (object): Fields to override.

    Returns:
        AppErrorReportSchema: The report.
    """
    fields: dict[str, object] = {
        "code": "AUTH_TOKEN_EXPIRED",
        "message": "TypeError: null is not an object",
        "platform": AppPlatform.IOS,
        "app_version": "1.4.2",
    }
    fields.update(overrides)
    return AppErrorReportSchema(**fields)


class TestTruncation:
    """A report over the limit is shortened, never refused."""

    def test_short_value_is_untouched(self) -> None:
        """Text that fits comes back identical."""
        assert AppErrorService.truncate("short", 100) == "short"

    def test_none_stays_none(self) -> None:
        """An absent field is not turned into a marker."""
        assert AppErrorService.truncate(None, 100) is None

    def test_long_value_is_cut_and_marked(self) -> None:
        """The cut fits the column and says it happened."""
        cut = AppErrorService.truncate("x" * 500, 120)

        assert cut is not None
        assert len(cut) == 120
        assert cut.endswith(APP_ERROR_TRUNCATION_SUFFIX)

    def test_limit_shorter_than_the_marker_still_fits(self) -> None:
        """A tiny limit does not overflow the column.

        Degenerate, but the alternative is a value longer than the limit
        reaching the database and failing the insert — turning a cosmetic
        edge case into a lost report.
        """
        cut = AppErrorService.truncate("abcdef", 3)

        assert cut is not None
        assert len(cut) == 3

    async def test_oversized_report_is_stored_not_rejected(
        self, session: AsyncSession
    ) -> None:
        """The whole point: a crashed client's report survives."""
        stored = await _service(session).report_error(
            _report(
                code="C" * (APP_ERROR_CODE_MAX_LENGTH + 50),
                message="M" * (APP_ERROR_MESSAGE_MAX_LENGTH + 500),
                app_version="V" * (APP_ERROR_TEXT_FIELD_MAX_LENGTH + 10),
            )
        )

        assert len(stored.code) == APP_ERROR_CODE_MAX_LENGTH
        assert len(stored.message) == APP_ERROR_MESSAGE_MAX_LENGTH
        assert stored.app_version is not None
        assert len(stored.app_version) == APP_ERROR_TEXT_FIELD_MAX_LENGTH


class TestReporting:
    """Storing a report."""

    async def test_anonymous_report_is_accepted(self, session: AsyncSession) -> None:
        """An error before login is the one hardest to debug from the app."""
        stored = await _service(session).report_error(_report())

        assert stored.user_id is None
        assert stored.code == "AUTH_TOKEN_EXPIRED"

    async def test_user_id_comes_from_the_caller_not_the_body(
        self, session: AsyncSession
    ) -> None:
        """The client cannot attribute its error to somebody else.

        ``AppErrorReportSchema`` has no ``user_id`` field at all, so a body
        carrying one is dropped by ``extra="ignore"`` before the service
        ever sees it — and the service takes the id from its own argument.
        """
        someone_else = uuid4()
        caller = uuid4()
        report = AppErrorReportSchema.model_validate(
            {
                "code": "X",
                "message": "boom",
                "user_id": str(someone_else),
            }
        )

        stored = await _service(session).report_error(report, user_id=caller)

        assert stored.user_id == caller

    async def test_platform_defaults_to_unknown(self, session: AsyncSession) -> None:
        """A client that states no platform still gets its report stored.

        Compared with ``==`` rather than ``is``: ``BaseSchema`` sets
        ``use_enum_values=True``, so the field holds the value. That is the
        SDK-wide convention and it is kept here on purpose — unlike
        ``PixCharge.status``, this label carries no branch of business
        logic, only a filter and a column.
        """
        stored = await _service(session).report_error(
            AppErrorReportSchema(code="X", message="boom")
        )

        assert stored.platform == AppPlatform.UNKNOWN


class TestListing:
    """Reading the reports back."""

    async def test_empty_listing_is_success(self, session: AsyncSession) -> None:
        """A filter that matches nothing is 200 with zero items, not a 404."""
        page = await _service(session).list_errors(AppErrorFilterSchema())

        assert page.total == 0
        assert page.items == []

    async def test_filters_combine_with_and(self, session: AsyncSession) -> None:
        """``code`` + ``app_version`` isolates one defect in one build."""
        service = _service(session)
        await service.report_error(_report(code="A", app_version="1.0.0"))
        await service.report_error(_report(code="A", app_version="2.0.0"))
        await service.report_error(_report(code="B", app_version="1.0.0"))

        page = await service.list_errors(
            AppErrorFilterSchema(code="A", app_version="1.0.0")
        )

        assert page.total == 1

    async def test_filters_by_platform(self, session: AsyncSession) -> None:
        """An iOS-only bug is not the same bug as an Android-only one."""
        service = _service(session)
        await service.report_error(_report(platform=AppPlatform.IOS))
        await service.report_error(_report(platform=AppPlatform.ANDROID))

        page = await service.list_errors(
            AppErrorFilterSchema(platform=AppPlatform.ANDROID)
        )

        assert page.total == 1

    async def test_date_range_includes_both_ends(self, session: AsyncSession) -> None:
        """The range is half-open in SQL but inclusive to the caller.

        A report created today must appear when ``end_date`` is today —
        which only works because the condition is ``< end + 1 day`` rather
        than ``<= end``, where ``end`` would mean midnight.
        """
        service = _service(session)
        await service.report_error(_report())
        today = datetime.now().date()

        page = await service.list_errors(
            AppErrorFilterSchema(start_date=today, end_date=today)
        )

        assert page.total == 1

    async def test_date_range_excludes_outside(self, session: AsyncSession) -> None:
        """A window that ended yesterday returns nothing stored today."""
        service = _service(session)
        await service.report_error(_report())
        yesterday = datetime.now().date() - timedelta(days=1)

        page = await service.list_errors(
            AppErrorFilterSchema(start_date=yesterday, end_date=yesterday)
        )

        assert page.total == 0

    async def test_pagination_reports_totals(self, session: AsyncSession) -> None:
        """The page envelope carries what a table needs to render."""
        service = _service(session)
        for index in range(5):
            await service.report_error(_report(code=f"C{index}"))

        page = await service.list_errors(AppErrorFilterSchema(), page_size=2)

        assert page.total == 5
        assert page.pages == 3
        assert len(page.items) == 2


ADMIN_ID = uuid4()


@pytest.fixture
async def client_factory() -> AsyncIterator[object]:
    """Yield a builder for an app with or without the listing mounted.

    Returns:
        object: A callable taking ``admin`` and returning an ``AsyncClient``
        context manager.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def session_factory() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    def service_factory(session: AsyncSession) -> AppErrorService:
        return _service(session)

    def current_user_id_optional() -> UUID | None:
        return None

    def build(*, admin: bool) -> AsyncClient:
        def admin_dependency() -> UUID:
            return ADMIN_ID

        app = FastAPI()
        app.include_router(
            make_app_error_router(
                service_factory=service_factory,
                session_factory=session_factory,
                current_user_id_optional=current_user_id_optional,
                admin_dependency=admin_dependency if admin else None,
            )
        )
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    yield build
    await engine.dispose()


class TestRouter:
    """The HTTP surface."""

    async def test_public_post_stores_the_report(self, client_factory: object) -> None:
        """No token needed: the broken app is who most needs to be heard."""
        async with client_factory(admin=True) as client:  # type: ignore[operator]
            response = await client.post(
                "/api/app-errors",
                json={"code": "AUTH_TOKEN_EXPIRED", "message": "boom"},
            )

        assert response.status_code == 201
        assert response.json()["code"] == "AUTH_TOKEN_EXPIRED"

    async def test_listing_is_absent_without_an_admin_dependency(
        self, client_factory: object
    ) -> None:
        """An ungated listing would expose traces and device identifiers.

        So the ``GET`` is not merely unprotected-but-present: it is never
        mounted, and the route answers 405 instead of returning data.
        """
        async with client_factory(admin=False) as client:  # type: ignore[operator]
            response = await client.get("/api/app-errors")

        assert response.status_code == 405

    async def test_listing_is_mounted_with_an_admin_dependency(
        self, client_factory: object
    ) -> None:
        """With the gate in place, the page is served."""
        async with client_factory(admin=True) as client:  # type: ignore[operator]
            await client.post("/api/app-errors", json={"code": "A", "message": "boom"})
            response = await client.get("/api/app-errors")

        assert response.status_code == 200
        assert response.json()["total"] == 1

    async def test_inverted_date_range_answers_422_not_500(
        self, client_factory: object
    ) -> None:
        """The client's mistake is the client's status code.

        A validator raising ``ValueError`` inside a schema resolved through
        ``Depends()`` escapes as a 500 — the API would blame the server for
        the caller's input. The check lives in the route for exactly this
        reason.
        """
        async with client_factory(admin=True) as client:  # type: ignore[operator]
            response = await client.get(
                "/api/app-errors",
                params={"start_date": "2026-08-10", "end_date": "2026-08-01"},
            )

        assert response.status_code == 422


class TestModel:
    """Column decisions that are silent when wrong."""

    def test_user_fk_sets_null_on_delete(self) -> None:
        """Deleting the account must not delete the evidence of the bug."""
        foreign_keys = list(_AppError.__table__.c.user_id.foreign_keys)

        assert len(foreign_keys) == 1
        assert foreign_keys[0].ondelete == "SET NULL"

    def test_user_id_is_nullable(self) -> None:
        """Login-flow errors happen before an authenticated user exists."""
        assert _AppError.__table__.c.user_id.nullable is True

    def test_created_at_is_indexed(self) -> None:
        """The standard read is newest-first over an unbounded table."""
        indexed = {
            column.name
            for index in _AppError.__table__.indexes
            for column in index.columns
        }

        assert "created_at" in indexed

    def test_code_and_app_version_are_indexed(self) -> None:
        """``code`` + ``app_version`` is the cut investigations start from."""
        assert _AppError.__table__.c.code.index is True
        assert _AppError.__table__.c.app_version.index is True


def test_filter_schema_has_no_user_settable_dates_validator() -> None:
    """The range check is deliberately absent from the schema.

    Pinning the absence, because "add a validator" is the obvious change
    that reintroduces the 500.
    """
    schema = AppErrorFilterSchema(
        start_date=date(2026, 8, 10), end_date=date(2026, 8, 1)
    )

    assert schema.start_date == date(2026, 8, 10)
    assert schema.end_date == date(2026, 8, 1)
