"""Business rules for the errors a client application reports.

Two operations and one rule. The operations are "store a report" and "page
through the reports"; the rule is that a report over the column limit is
**shortened, never refused**.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import and_

from tempest_fastapi_sdk.app_errors.constants import (
    APP_ERROR_CODE_MAX_LENGTH,
    APP_ERROR_DEVICE_TEXT_FIELDS,
    APP_ERROR_MESSAGE_MAX_LENGTH,
    APP_ERROR_TEXT_FIELD_MAX_LENGTH,
    APP_ERROR_TRUNCATION_SUFFIX,
)
from tempest_fastapi_sdk.app_errors.schemas import (
    AppErrorCreateSchema,
    AppErrorFilterSchema,
    AppErrorReportSchema,
    AppErrorResponseSchema,
)
from tempest_fastapi_sdk.schemas.pagination import BasePaginationSchema

if TYPE_CHECKING:
    from tempest_fastapi_sdk.db.repository import BaseRepository


class AppErrorService:
    """Store and read the errors a client application reports.

    Attributes:
        repository (BaseRepository[Any]): Repository for the concrete
            app-error table.
    """

    def __init__(self, repository: BaseRepository[Any]) -> None:
        """Initialize the service.

        Args:
            repository (BaseRepository[Any]): Repository for the concrete
                app-error table.
        """
        self.repository: BaseRepository[Any] = repository

    @staticmethod
    def truncate(value: str | None, limit: int) -> str | None:
        """Shorten ``value`` to fit ``limit``, marking where it was cut.

        Args:
            value (str | None): Text sent by the client.
            limit (int): Maximum size of the destination column.

        Returns:
            str | None: The original text when it already fits, the cut
            version with a suffix when it does not, or ``None`` when there
            is no value.

        A report over the limit is shortened rather than refused: the
        sender is an app that has just crashed and cannot handle a 422, so
        refusing would throw the whole report away. The suffix tells
        whoever reads the listing that content is missing.
        """
        if value is None or len(value) <= limit:
            return value
        keep = max(limit - len(APP_ERROR_TRUNCATION_SUFFIX), 0)
        return f"{value[:keep]}{APP_ERROR_TRUNCATION_SUFFIX}"[:limit]

    async def report_error(
        self,
        data: AppErrorReportSchema,
        *,
        user_id: UUID | None = None,
    ) -> AppErrorResponseSchema:
        """Persist a report sent by the client application.

        Args:
            data (AppErrorReportSchema): The body the client sent.
            user_id (UUID | None): The authenticated user, when there was
                one. It comes from the token, never from the request body —
                which is why this is an argument and not a field the client
                can set.

        Returns:
            AppErrorResponseSchema: The persisted report.
        """
        payload = data.model_dump()
        payload["code"] = self.truncate(payload["code"], APP_ERROR_CODE_MAX_LENGTH)
        payload["message"] = self.truncate(
            payload["message"], APP_ERROR_MESSAGE_MAX_LENGTH
        )
        for field in APP_ERROR_DEVICE_TEXT_FIELDS:
            payload[field] = self.truncate(
                payload.get(field), APP_ERROR_TEXT_FIELD_MAX_LENGTH
            )

        create = AppErrorCreateSchema(**payload, user_id=user_id)
        model = self.repository.model(**create.model_dump())
        persisted = await self.repository.add(model)
        return AppErrorResponseSchema.model_validate(persisted)

    async def list_errors(
        self,
        filters: AppErrorFilterSchema,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> BasePaginationSchema[AppErrorResponseSchema]:
        """Page through the reports matching ``filters``, newest first.

        Args:
            filters (AppErrorFilterSchema): Filters from the query string.
            page (int): The page, 1-indexed.
            page_size (int): Items per page.

        Returns:
            BasePaginationSchema[AppErrorResponseSchema]: One page of
            reports. Empty when nothing matches — a filtered query with no
            results is a success, not a 404.
        """
        result = await self.repository.paginate(
            filters=filters.as_filters(),
            page=page,
            page_size=page_size,
            where=self._date_range(filters),
        )
        return BasePaginationSchema[AppErrorResponseSchema](
            items=[
                AppErrorResponseSchema.model_validate(item) for item in result["items"]
            ],
            total=result["total"],
            page=result["page"],
            page_size=result["page_size"],
            pages=result["pages"],
        )

    def _date_range(self, filters: AppErrorFilterSchema) -> Any | None:
        """Build the creation-date condition, if the caller asked for one.

        Args:
            filters (AppErrorFilterSchema): Filters from the query string.

        Returns:
            Any | None: A SQLAlchemy condition, or ``None`` when no date
            was given.

        The cut is a **half-open range over the column itself**
        (``created_at >= start`` and ``created_at < end + 1 day``) rather
        than ``func.date(created_at) == ...``. Applying a function to the
        column discards the ``created_at`` index, and this is the table
        that grows with no natural bound.

        The boundary is a **UTC** one, and deliberately not the server's:
        ``created_at`` is written by ``utcnow``, and the dates here are
        combined with midnight and compared against it. Reading the local
        calendar instead would make the same query answer differently on
        two machines in different zones — a filter that depends on where
        the process runs. Callers building a range from "today" should take
        it from UTC.
        """
        conditions: list[Any] = []
        column = self.repository.model.created_at
        if filters.start_date is not None:
            conditions.append(column >= datetime.combine(filters.start_date, time.min))
        if filters.end_date is not None:
            conditions.append(
                column
                < datetime.combine(filters.end_date + timedelta(days=1), time.min)
            )
        if not conditions:
            return None
        return conditions[0] if len(conditions) == 1 else and_(*conditions)


__all__: list[str] = ["AppErrorService"]
