"""DTOs for the errors a client application reports.

Four schemas rather than one, and the split is load-bearing:
:class:`AppErrorReportSchema` is what the client may send,
:class:`AppErrorCreateSchema` adds the ``user_id`` the **server** resolves
from the token, and keeping them apart is what stops a client from
attributing its error to somebody else's account.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from tempest_fastapi_sdk import BaseSchema, BaseStrEnum


class AppPlatform(BaseStrEnum):
    """The client platform an error was reported from.

    A closed set on purpose: filtering the listing by platform is the
    reason the column exists at all, because an error that only shows up
    on iOS is not the same bug as one that only shows up on Android.

    Attributes:
        IOS (str): The iOS application.
        ANDROID (str): The Android application.
        WEB (str): A web client.
        UNKNOWN (str): The client did not state a platform.
    """

    IOS = "ios"
    ANDROID = "android"
    WEB = "web"
    UNKNOWN = "unknown"


class AppErrorDeviceSchema(BaseSchema):
    """Device data travelling with a report.

    Every field is optional: the app sends whatever it managed to collect
    at the moment of the failure, and requiring any of them would turn an
    incomplete collection into a lost report.

    Attributes:
        platform (AppPlatform): The reporting client's platform.
        os_version (str | None): The operating system version.
        app_version (str | None): The application version or build.
        device_model (str | None): The device model.
        device_id (str | None): An anonymous device identifier, used to
            group reports coming from the same device.
    """

    platform: AppPlatform = Field(
        default=AppPlatform.UNKNOWN,
        description="Plataforma do cliente que reportou o erro.",
        examples=["ios"],
    )
    os_version: str | None = Field(
        default=None,
        description="Versão do sistema operacional do dispositivo.",
        examples=["18.2"],
    )
    app_version: str | None = Field(
        default=None,
        description="Versão ou build do aplicativo.",
        examples=["1.4.2+310"],
    )
    device_model: str | None = Field(
        default=None,
        description="Modelo do aparelho.",
        examples=["iPhone 15 Pro"],
    )
    device_id: str | None = Field(
        default=None,
        description="Identificador anônimo do aparelho.",
        examples=["b3f1c0d2-8a41-4d0e-9f5a-2c7e1f9b0a44"],
    )


class AppErrorReportSchema(AppErrorDeviceSchema):
    """The body a client sends when reporting an error.

    ``code`` and ``message`` are the only required fields. Values above the
    column limit are truncated on the way in, never refused — see
    :meth:`~tempest_fastapi_sdk.app_errors.service.AppErrorService.truncate`.

    Attributes:
        code (str): The error code the app emits.
        message (str): The message or stack trace.
    """

    code: str = Field(
        min_length=1,
        description="Código do erro emitido pelo aplicativo.",
        examples=["AUTH_TOKEN_EXPIRED"],
    )
    message: str = Field(
        min_length=1,
        description="Mensagem ou stack trace associado ao erro.",
        examples=["TypeError: null is not an object (evaluating 'user.id')"],
    )


class AppErrorCreateSchema(AppErrorReportSchema):
    """A report already resolved for persistence.

    Differs from :class:`AppErrorReportSchema` by carrying ``user_id``,
    which comes from the token and never from the body. Letting the client
    say whose error it is would allow attributing a report to another user,
    and this split is what makes that impossible rather than merely
    discouraged.

    Attributes:
        user_id (UUID | None): The user authenticated at the moment of the
            error, when there was one.
    """

    user_id: UUID | None = Field(
        default=None,
        description="Usuário autenticado no momento do erro, se houver.",
    )


class AppErrorResponseSchema(AppErrorDeviceSchema):
    """A persisted report, as the API returns it.

    Attributes:
        id (UUID): The report's primary key.
        code (str): The error code.
        message (str): The message or stack trace, already truncated if it
            was over the limit.
        user_id (UUID | None): The associated user, when there was one.
        created_at (datetime): When the report was stored.
    """

    id: UUID = Field(description="Identificador do relato.")
    code: str = Field(description="Código do erro emitido pelo aplicativo.")
    message: str = Field(description="Mensagem ou stack trace do erro.")
    user_id: UUID | None = Field(
        default=None, description="Usuário associado ao relato, se houver."
    )
    created_at: datetime = Field(description="Quando o relato foi gravado.")


class AppErrorFilterSchema(BaseSchema):
    """Filters for the administrative listing.

    All optional, combined with ``AND``. The cut that resolves most
    investigations is ``code`` + ``app_version``: it isolates one defect in
    one build.

    The range check does **not** live in a validator. A ``ValueError``
    raised by a schema resolved through ``Depends()`` escapes as a 500, and
    the API would blame the server for the client's input — so the router
    answers 422 for an inverted range instead.

    Attributes:
        code (str | None): Filters by exact code.
        platform (AppPlatform | None): Filters by platform.
        app_version (str | None): Filters by application version.
        user_id (UUID | None): Filters by one user's reports.
        start_date (date | None): Start of the creation range, inclusive.
        end_date (date | None): End of the creation range, inclusive.
    """

    code: str | None = Field(default=None, description="Filtra pelo código exato.")
    platform: AppPlatform | None = Field(
        default=None, description="Filtra pela plataforma do cliente."
    )
    app_version: str | None = Field(
        default=None, description="Filtra pela versão do aplicativo."
    )
    user_id: UUID | None = Field(
        default=None, description="Filtra pelos relatos de um usuário."
    )
    start_date: date | None = Field(
        default=None, description="Início do intervalo de criação, inclusivo."
    )
    end_date: date | None = Field(
        default=None, description="Fim do intervalo de criação, inclusivo."
    )

    def as_filters(self) -> dict[str, Any]:
        """Build the equality filters this schema carries.

        Returns:
            dict[str, Any]: Only the fields that were set, ready for
            ``BaseRepository``. The date range is not here — it is a range,
            not an equality, and it is applied separately.
        """
        filters: dict[str, Any] = {}
        if self.code:
            filters["code"] = self.code
        if self.platform is not None:
            filters["platform"] = self.platform
        if self.app_version:
            filters["app_version"] = self.app_version
        if self.user_id is not None:
            filters["user_id"] = self.user_id
        return filters


__all__: list[str] = [
    "AppErrorCreateSchema",
    "AppErrorDeviceSchema",
    "AppErrorFilterSchema",
    "AppErrorReportSchema",
    "AppErrorResponseSchema",
    "AppPlatform",
]
