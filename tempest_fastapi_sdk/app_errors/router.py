"""The HTTP surface for client error reports.

One public write and one optional administrative read. The write is public
because an error in the login flow happens before a token exists — the most
valuable report is exactly the one no session can vouch for.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from enum import Enum
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from tempest_fastapi_sdk.api.dependencies._session import session_provider_for
from tempest_fastapi_sdk.api.route_docs import RouteDocs
from tempest_fastapi_sdk.app_errors.schemas import (
    AppErrorFilterSchema,
    AppErrorReportSchema,
    AppErrorResponseSchema,
)
from tempest_fastapi_sdk.app_errors.service import AppErrorService
from tempest_fastapi_sdk.exceptions import ValidationException
from tempest_fastapi_sdk.schemas.pagination import BasePaginationSchema


def make_app_error_router(
    *,
    service_factory: Callable[..., AppErrorService],
    session_factory: Callable[..., AsyncIterator[Any]],
    current_user_id_optional: Callable[..., Any] | None = None,
    admin_dependency: Callable[..., Any] | None = None,
    prefix: str = "/api/app-errors",
    tags: list[str | Enum] | None = None,
    docs_locale: str | None = None,
) -> APIRouter:
    """Build the app-error router.

    Endpoints:

    * ``POST {prefix}`` — record a report. Public.
    * ``GET {prefix}`` — page through the reports. Mounted **only** when
      ``admin_dependency`` is given.

    Args:
        service_factory (Callable[..., AppErrorService]): Builds a
            request-scoped :class:`AppErrorService` from the session.
        session_factory (Callable[..., AsyncIterator[Any]]): Yields a
            request-scoped database session.
        current_user_id_optional (Callable[..., Any] | None): Dependency
            resolving the caller's id, or ``None`` when the request carries
            no valid token. Without it every report is stored anonymous.
        admin_dependency (Callable[..., Any] | None): Dependency gating the
            listing. ``None`` leaves the ``GET`` unmounted — a service that
            reads its reports through ``AdminSite`` does not need the
            endpoint, and an ungated listing would expose stack traces and
            device identifiers to anyone.
        prefix (str): Path prefix for both routes.
        tags (list[str | Enum] | None): OpenAPI tags, in the shape
            ``APIRouter`` accepts.
        docs_locale (str | None): Language of the OpenAPI ``summary``
            and ``description`` text — ``"en-US"`` or ``"pt-BR"``.
            ``None`` (default) reads ``API_DOCS_LOCALE`` through
            :class:`~tempest_fastapi_sdk.settings.ServerSettings`,
            whose default is ``"en-US"``.

    Returns:
        APIRouter: The router, ready to include.

    Note:
        **The hourly ceiling is not here.** The write endpoint is public and
        does need one, but it belongs to ``RateLimitMiddleware`` configured
        for this prefix, not to a counter this module reimplements.

        What must not be lost in that move is the rule that made the
        original implementation correct: **fail open**. Losing an error
        report is worse than accepting traffic above the ceiling, because
        the moment the counter store is unwell is the moment errors spike.

        Measured: ``RateLimitMiddleware`` does **not** fail open on its own
        — a store whose ``hit`` raises propagates the exception and the
        caller gets an error. Wrap the store in
        :class:`~tempest_fastapi_sdk.api.middlewares.rate_limit.FailOpenRateLimitStore`
        for this prefix.
    """
    route_docs: RouteDocs = RouteDocs(docs_locale)
    router = APIRouter(prefix=prefix, tags=tags or ["app-errors"])
    session_provider = session_provider_for(session_factory)

    @router.post(
        "",
        description=route_docs.description("app_errors.report_app_error"),
        status_code=status.HTTP_201_CREATED,
        response_model=AppErrorResponseSchema,
        summary=route_docs.summary("app_errors.report_app_error"),
    )
    async def report_app_error(
        data: AppErrorReportSchema,
        session: Any = Depends(session_provider),
        user_id: Any = (
            Depends(current_user_id_optional)
            if current_user_id_optional is not None
            else None
        ),
    ) -> AppErrorResponseSchema:
        """Store one error report.

        Args:
            data (AppErrorReportSchema): The body the client sent.
            session (Any): The request-scoped database session.
            user_id (Any): The authenticated caller, when there is one.

        Returns:
            AppErrorResponseSchema: The stored report.

        Neither a plan nor an active account gates this: whoever has a
        broken app is exactly who most needs the report to get through.
        """
        service = service_factory(session)
        return await service.report_error(
            data, user_id=user_id if isinstance(user_id, UUID) else None
        )

    if admin_dependency is not None:

        @router.get(
            "",
            description=route_docs.description("app_errors.list_app_errors"),
            response_model=BasePaginationSchema[AppErrorResponseSchema],
            summary=route_docs.summary("app_errors.list_app_errors"),
            dependencies=[Depends(admin_dependency)],
        )
        async def list_app_errors(
            filters: AppErrorFilterSchema = Depends(),
            page: int = Query(
                default=1,
                ge=1,
                description=route_docs.text("app_errors.list_app_errors.page"),
            ),
            page_size: int = Query(
                default=20,
                ge=1,
                le=200,
                description=route_docs.text("app_errors.list_app_errors.page_size"),
            ),
            session: Any = Depends(session_provider),
        ) -> BasePaginationSchema[AppErrorResponseSchema]:
            """Page through the stored reports, newest first.

            Args:
                filters (AppErrorFilterSchema): Filters from the query
                    string.
                page (int): The page, 1-indexed.
                page_size (int): Items per page.
                session (Any): The request-scoped database session.

            Returns:
                BasePaginationSchema[AppErrorResponseSchema]: One page of
                reports, empty when nothing matches.

            Raises:
                ValidationException: When ``start_date`` is after
                    ``end_date``. The check lives here rather than in a
                    schema validator because a ``ValueError`` raised inside
                    a schema resolved through ``Depends()`` escapes as a
                    500 — the API would blame the server for the client's
                    input.
            """
            if (
                filters.start_date is not None
                and filters.end_date is not None
                and filters.start_date > filters.end_date
            ):
                raise ValidationException(
                    "end_date must not be earlier than start_date."
                )
            service = service_factory(session)
            return await service.list_errors(filters, page=page, page_size=page_size)

    return router


__all__: list[str] = ["make_app_error_router"]
