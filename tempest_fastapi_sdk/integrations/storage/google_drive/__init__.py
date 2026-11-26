"""Google Drive: upload files into a shared folder as an ``UploadUtils`` backend.

.. code-block:: python

    from tempest_fastapi_sdk import UploadUtils
    from tempest_fastapi_sdk.integrations.storage.google_drive import (
        GoogleDriveUploadStorage,
    )

    storage = GoogleDriveUploadStorage.from_service_account(
        **settings.google_drive_kwargs()
    )
    uploads = UploadUtils(backend=storage)

Three pieces, each written from Google's documentation (the URL is in every
module docstring; the suite checks the requests on the wire against an
``httpx.MockTransport``, and a live test runs only when a real service
account is configured):

- **auth** -- :class:`ServiceAccountTokenProvider`, the JWT bearer grant of a
  service account (``RS256`` over ``pyjwt`` + ``cryptography``), cached and
  refreshed under a lock, behind the :class:`AccessTokenProvider` protocol.
- **client** -- :class:`GoogleDriveClient`, the Drive API v3 calls the
  backend needs, with the resumable upload protocol and retries.
- **storage** -- :class:`GoogleDriveUploadStorage`, the
  :class:`~tempest_fastapi_sdk.utils.UploadStorage` implementation: the
  key lives in ``appProperties``, a repeated key replaces the content of the
  same file, and ``presigned_url`` raises
  :class:`~tempest_fastapi_sdk.exceptions.PresignedUrlUnsupportedException`.

Requires the ``[google-drive]`` extra (``pyjwt`` and ``cryptography``;
``httpx`` is a base dependency).
"""

from tempest_fastapi_sdk.integrations.storage.google_drive.auth import (
    ASSERTION_LIFETIME_SECONDS as ASSERTION_LIFETIME_SECONDS,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.auth import (
    DRIVE_FILE_SCOPE as DRIVE_FILE_SCOPE,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.auth import (
    DRIVE_SCOPE as DRIVE_SCOPE,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.auth import (
    GOOGLE_DRIVE_EXTRA_HINT as GOOGLE_DRIVE_EXTRA_HINT,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.auth import (
    GOOGLE_TOKEN_URI as GOOGLE_TOKEN_URI,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.auth import (
    JWT_BEARER_GRANT_TYPE as JWT_BEARER_GRANT_TYPE,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.auth import (
    TOKEN_REFRESH_MARGIN_SECONDS as TOKEN_REFRESH_MARGIN_SECONDS,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.auth import (
    AccessTokenProvider as AccessTokenProvider,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.auth import (
    ServiceAccountCredentials as ServiceAccountCredentials,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.auth import (
    ServiceAccountTokenProvider as ServiceAccountTokenProvider,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.client import (
    DEFAULT_MAX_RETRIES as DEFAULT_MAX_RETRIES,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.client import (
    DEFAULT_UPLOAD_CHUNK_SIZE as DEFAULT_UPLOAD_CHUNK_SIZE,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.client import (
    DRIVE_API_BASE_URL as DRIVE_API_BASE_URL,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.client import (
    DRIVE_UPLOAD_BASE_URL as DRIVE_UPLOAD_BASE_URL,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.client import (
    FILE_FIELDS as FILE_FIELDS,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.client import (
    RESUME_INCOMPLETE as RESUME_INCOMPLETE,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.client import (
    RETRYABLE_STATUSES as RETRYABLE_STATUSES,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.client import (
    UPLOAD_CHUNK_GRANULARITY as UPLOAD_CHUNK_GRANULARITY,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.client import (
    GoogleDriveClient as GoogleDriveClient,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.client import (
    GoogleDriveFile as GoogleDriveFile,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.client import (
    escape_query_value as escape_query_value,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.storage import (
    APP_PROPERTY_MAX_BYTES as APP_PROPERTY_MAX_BYTES,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.storage import (
    DEFAULT_HTTP_TIMEOUT_SECONDS as DEFAULT_HTTP_TIMEOUT_SECONDS,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.storage import (
    GOOGLE_DRIVE_BACKEND as GOOGLE_DRIVE_BACKEND,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.storage import (
    TEMPEST_KEY_PROPERTY as TEMPEST_KEY_PROPERTY,
)
from tempest_fastapi_sdk.integrations.storage.google_drive.storage import (
    GoogleDriveUploadStorage as GoogleDriveUploadStorage,
)

__all__: list[str] = [
    "APP_PROPERTY_MAX_BYTES",
    "ASSERTION_LIFETIME_SECONDS",
    "DEFAULT_HTTP_TIMEOUT_SECONDS",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_UPLOAD_CHUNK_SIZE",
    "DRIVE_API_BASE_URL",
    "DRIVE_FILE_SCOPE",
    "DRIVE_SCOPE",
    "DRIVE_UPLOAD_BASE_URL",
    "FILE_FIELDS",
    "GOOGLE_DRIVE_BACKEND",
    "GOOGLE_DRIVE_EXTRA_HINT",
    "GOOGLE_TOKEN_URI",
    "JWT_BEARER_GRANT_TYPE",
    "RESUME_INCOMPLETE",
    "RETRYABLE_STATUSES",
    "TEMPEST_KEY_PROPERTY",
    "TOKEN_REFRESH_MARGIN_SECONDS",
    "UPLOAD_CHUNK_GRANULARITY",
    "AccessTokenProvider",
    "GoogleDriveClient",
    "GoogleDriveFile",
    "GoogleDriveUploadStorage",
    "ServiceAccountCredentials",
    "ServiceAccountTokenProvider",
    "escape_query_value",
]
