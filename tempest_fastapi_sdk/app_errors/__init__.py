"""Errors the client application reports back to the service.

A mobile app or SPA that breaks in a user's hand has nowhere to put the
stack trace unless the backend offers one. This module is that place:
abstract table plus ``make_*`` factory, the DTOs, an
:class:`AppErrorService` that stores and pages the reports, and an opt-in
:func:`make_app_error_router`.

Two rules shape everything here, and both come from a service that ran this
in production:

* **A truncated report beats a lost report.** A payload over the column
  limit is shortened with a marker, never refused — the sender has just
  crashed and cannot handle a 422.
* **``user_id`` comes from the token, never from the body.** Letting the
  client say whose error it is would allow attributing a report to somebody
  else's account.

The hourly ceiling the public write needs is **not** here: configure
``RateLimitMiddleware`` for the router's prefix. See
:func:`make_app_error_router` for what that move must not lose.
"""

from tempest_fastapi_sdk.app_errors.constants import (
    APP_ERROR_CODE_MAX_LENGTH as APP_ERROR_CODE_MAX_LENGTH,
)
from tempest_fastapi_sdk.app_errors.constants import (
    APP_ERROR_DEVICE_TEXT_FIELDS as APP_ERROR_DEVICE_TEXT_FIELDS,
)
from tempest_fastapi_sdk.app_errors.constants import (
    APP_ERROR_MESSAGE_MAX_LENGTH as APP_ERROR_MESSAGE_MAX_LENGTH,
)
from tempest_fastapi_sdk.app_errors.constants import (
    APP_ERROR_TEXT_FIELD_MAX_LENGTH as APP_ERROR_TEXT_FIELD_MAX_LENGTH,
)
from tempest_fastapi_sdk.app_errors.constants import (
    APP_ERROR_TRUNCATION_SUFFIX as APP_ERROR_TRUNCATION_SUFFIX,
)
from tempest_fastapi_sdk.app_errors.models import (
    BaseAppErrorModel as BaseAppErrorModel,
)
from tempest_fastapi_sdk.app_errors.models import (
    make_app_error_model as make_app_error_model,
)
from tempest_fastapi_sdk.app_errors.router import (
    make_app_error_router as make_app_error_router,
)
from tempest_fastapi_sdk.app_errors.schemas import (
    AppErrorCreateSchema as AppErrorCreateSchema,
)
from tempest_fastapi_sdk.app_errors.schemas import (
    AppErrorDeviceSchema as AppErrorDeviceSchema,
)
from tempest_fastapi_sdk.app_errors.schemas import (
    AppErrorFilterSchema as AppErrorFilterSchema,
)
from tempest_fastapi_sdk.app_errors.schemas import (
    AppErrorReportSchema as AppErrorReportSchema,
)
from tempest_fastapi_sdk.app_errors.schemas import (
    AppErrorResponseSchema as AppErrorResponseSchema,
)
from tempest_fastapi_sdk.app_errors.schemas import AppPlatform as AppPlatform
from tempest_fastapi_sdk.app_errors.service import (
    AppErrorService as AppErrorService,
)

__all__: list[str] = [
    "APP_ERROR_CODE_MAX_LENGTH",
    "APP_ERROR_DEVICE_TEXT_FIELDS",
    "APP_ERROR_MESSAGE_MAX_LENGTH",
    "APP_ERROR_TEXT_FIELD_MAX_LENGTH",
    "APP_ERROR_TRUNCATION_SUFFIX",
    "AppErrorCreateSchema",
    "AppErrorDeviceSchema",
    "AppErrorFilterSchema",
    "AppErrorReportSchema",
    "AppErrorResponseSchema",
    "AppErrorService",
    "AppPlatform",
    "BaseAppErrorModel",
    "make_app_error_model",
    "make_app_error_router",
]
