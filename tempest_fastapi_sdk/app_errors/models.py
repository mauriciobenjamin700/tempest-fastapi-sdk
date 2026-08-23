"""The table a client application's error reports land in.

Exists so that investigating a field problem does not depend on
reproducing it on the device: the app sends what it knows at the moment of
the failure, and the information becomes queryable.

As with the SDK's other reusable tables, the abstract row lives here and
the project ships the concrete table, so the user FK and ``__tablename__``
live in the application's metadata.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.app_errors.constants import (
    APP_ERROR_CODE_MAX_LENGTH,
    APP_ERROR_TEXT_FIELD_MAX_LENGTH,
)
from tempest_fastapi_sdk.app_errors.schemas import AppPlatform
from tempest_fastapi_sdk.db.model import BaseModel


class BaseAppErrorModel(BaseModel):
    """Abstract table for an error reported by a client application.

    ``user_id`` is declared here so the service and the listing can read
    it, and **re-declared** by the concrete subclass with the foreign key:
    the user table's name belongs to the application, not to the SDK.

    Attributes:
        code (str): The error code the app emits — a stable identifier,
            indexed because grouping by it is the first thing anyone does.
        message (str): The message or stack trace.
        platform (str): An :class:`AppPlatform` value, indexed so the
            listing can isolate one platform.
        os_version (str | None): The device's operating system version.
        app_version (str | None): The application version or build,
            indexed: ``code`` + ``app_version`` is the cut that resolves
            most investigations.
        device_model (str | None): The device model.
        device_id (str | None): An anonymous device identifier, used to
            group reports from the same device.
        user_id (UUID | None): The user authenticated at the moment of the
            error. Nullable, and the concrete subclass re-declares it with
            the FK to the project's user table.
    """

    __abstract__ = True

    code: Mapped[str] = mapped_column(
        String(APP_ERROR_CODE_MAX_LENGTH),
        nullable=False,
        index=True,
        doc="The error code the app emits.",
    )
    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="The message or stack trace of the error.",
    )
    platform: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=AppPlatform.UNKNOWN.value,
        index=True,
        doc="An AppPlatform value.",
    )
    os_version: Mapped[str | None] = mapped_column(
        String(APP_ERROR_TEXT_FIELD_MAX_LENGTH),
        nullable=True,
        doc="The device's operating system version.",
    )
    app_version: Mapped[str | None] = mapped_column(
        String(APP_ERROR_TEXT_FIELD_MAX_LENGTH),
        nullable=True,
        index=True,
        doc="The application version or build.",
    )
    device_model: Mapped[str | None] = mapped_column(
        String(APP_ERROR_TEXT_FIELD_MAX_LENGTH),
        nullable=True,
        doc="The device model.",
    )
    device_id: Mapped[str | None] = mapped_column(
        String(APP_ERROR_TEXT_FIELD_MAX_LENGTH),
        nullable=True,
        doc="An anonymous device identifier.",
    )
    user_id: Mapped[UUID | None] = mapped_column(
        nullable=True,
        index=True,
        doc="The user authenticated at the moment of the error, if any.",
    )


def make_app_error_model(
    *,
    user_table: str = "users",
    tablename: str = "app_errors",
    class_name: str = "AppErrorModel",
) -> type[BaseAppErrorModel]:
    """Build a concrete app-error model bound to the project's user table.

    Args:
        user_table (str): Table name of the concrete user model the
            ``user_id`` FK references.
        tablename (str): ``__tablename__`` for the generated class.
        class_name (str): Python class name.

    Returns:
        type[BaseAppErrorModel]: A concrete mapped class.

    Two column decisions are made here rather than left to the caller,
    because getting either wrong is silent:

    ``user_id`` is **nullable**. An error in the login or signup flow
    happens before an authenticated user exists, and that is precisely the
    error hardest to debug from the app — refusing the report for lack of a
    user would drop the most valuable case.

    The FK uses ``ON DELETE SET NULL`` rather than the ``CASCADE`` the rest
    of the schema uses: the report describes a defect of the application,
    not of the user, so deleting the account must not delete the evidence
    of the bug.

    ``created_at`` gets its own index because the standard read is "newest
    first", paginated, and this is the table that grows with no natural
    bound — without it every page costs a sort of the whole table.
    """
    attrs: dict[str, object] = {
        "__tablename__": tablename,
        "user_id": mapped_column(
            ForeignKey(f"{user_table}.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
            doc="The user authenticated at the moment of the error, if any.",
        ),
        "__table_args__": (Index(f"ix_{tablename}_created_at", "created_at"),),
        "__module__": __name__,
        "__qualname__": class_name,
    }
    return type(class_name, (BaseAppErrorModel,), attrs)


__all__: list[str] = ["BaseAppErrorModel", "make_app_error_model"]
