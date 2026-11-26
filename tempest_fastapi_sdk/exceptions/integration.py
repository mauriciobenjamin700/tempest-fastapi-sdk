"""503s raised when an integration is switched off or cannot be reached."""

import logging
from typing import Any, ClassVar

from tempest_fastapi_sdk.exceptions.base import AppException


class IntegrationDisabledException(AppException):
    """Raised when code uses an integration its ``<NAME>_ENABLED`` turned off.

    A deployment that ships in phases boots with some third-party
    integrations not configured yet (``STORAGE_ENABLED=false``,
    ``EMAIL_ENABLED=false``, ``FIREBASE_ENABLED=false``). The production
    guard skips them, and every SDK client built from those settings
    answers each operation with this exception instead of talking to a
    server that was never set up — so the failure is the same ``503`` with
    the same ``code`` everywhere, never a silent no-op and never a
    connection error against a development default.

    The integration name in ``details["integration"]`` is the same string
    :meth:`~tempest_fastapi_sdk.settings.BaseAppSettings.disabled_integrations`
    lists (``"storage"``, ``"email"``, ``"firebase"``, ``"revenuecat"``).

    Logged at ``WARNING``, without a traceback and outside ``500.log``
    (``log_level``): the ``503`` is the configured answer, not a failure.
    Logged as a 5xx error, eight of eleven records in one service's "500"
    source were this exception, with storage and AI switched off, and the
    one real bug was buried among them.

    Attributes:
        integration (str): The integration that refused the call.
    """

    status_code: int = 503
    message: str = "This integration is disabled in this environment"
    code: str = "INTEGRATION_DISABLED"
    details_example: ClassVar[dict[str, Any]] = {"integration": "email"}
    log_level: ClassVar[int | None] = logging.WARNING

    def __init__(self, integration: str, *, message: str | None = None) -> None:
        """Initialize the exception.

        Args:
            integration (str): Name of the disabled integration, as listed
                by ``disabled_integrations()``.
            message (str | None): Override the message. ``None`` names the
                integration: ``"The email integration is disabled in this
                environment"``.
        """
        self.integration: str = integration
        super().__init__(
            message=message
            or f"The {integration} integration is disabled in this environment",
            details={"integration": integration},
            message_params={"integration": integration},
        )


class EmailDeliveryException(AppException):
    """Raised when the SMTP server could not take a message.

    :meth:`~tempest_fastapi_sdk.EmailUtils.send` raises it for every
    transport failure: the host does not resolve, the connection is
    refused or times out, the TLS handshake fails (``SMTP_USE_SSL=true``
    against the STARTTLS port ``587`` answers ``[SSL:
    WRONG_VERSION_NUMBER]``), the server rejects the login, or it refuses
    the message. The original error is chained as ``__cause__``, so code
    that needs the SMTP reply code still reaches it.

    The response carries no ``details``: the host, the port and the
    recipient are server-side facts, and the client can only retry later.
    They are logged once, at ``ERROR``, by the mailer that raised — which
    is why the exception handler logs it at ``WARNING`` without a traceback
    (``log_level``): a second ``ERROR`` record with a traceback reported
    the same outage twice.

    It is not :class:`IntegrationDisabledException`: that one means the
    deployment chose to run without e-mail, this one means e-mail is
    configured and failed.
    """

    status_code: int = 503
    message: str = "The e-mail server could not be reached; try again later"
    code: str = "EMAIL_UNAVAILABLE"
    log_level: ClassVar[int | None] = logging.WARNING


class ZapSendFailedException(AppException):
    """Raised when the zap-api ``202`` answers a key whose send already failed.

    :meth:`~tempest_fastapi_sdk.integrations.messaging.zap.ZapInbox.send_text`
    raises it when the ``202`` carries ``status="failed"``. A fresh send never
    answers that: the gateway enqueues every new row as ``queued``. It only
    comes back as the ``deduped=True`` answer to a key whose outbox row the
    gateway's worker already marked ``failed`` — read in the gateway's
    ``outbound-submit.ts`` and outbox repository, not measured against a
    running gateway. It is a ``502`` because the failure is the downstream
    gateway's, not the caller's request.

    ``details["idempotency_key"]`` names that send. Reusing the key returns
    the same failed row again, so a real retry needs a fresh key.

    Attributes:
        idempotency_key (str): The key the refused send went out with.
    """

    status_code: int = 502
    message: str = "The WhatsApp gateway refused the message"
    code: str = "ZAP_SEND_FAILED"
    details_example: ClassVar[dict[str, Any]] = {"idempotency_key": "reply-ABCD1234"}

    def __init__(self, idempotency_key: str) -> None:
        """Initialize the exception.

        Args:
            idempotency_key (str): The key the refused send went out with.
        """
        self.idempotency_key: str = idempotency_key
        super().__init__(details={"idempotency_key": idempotency_key})


class GoogleDriveException(AppException):
    """Raised when Google Drive, or Google's token endpoint, refuses a call.

    The Google Drive upload backend
    (:mod:`tempest_fastapi_sdk.integrations.storage.google_drive`) raises
    it for every non-success answer it does not retry, and once its retries
    for ``429``/``5xx`` are spent. ``details`` carries what the upstream
    said: the HTTP status in ``details["status"]`` (``None`` when the server
    was never reached) and the first ``error.errors[].reason`` of a Drive
    error body — or the OAuth ``error`` of the token endpoint, such as
    ``invalid_grant`` — in ``details["reason"]``.

    It is a ``502``: the request reached the service fine, the storage
    behind it did not take it. Neither the access token, the signed
    assertion nor the private key ever appears in the message.

    Attributes:
        upstream_status (int | None): Google's HTTP status, or ``None``.
        reason (str | None): Google's machine-readable reason.
    """

    status_code: int = 502
    message: str = "Google Drive refused the request"
    code: str = "GOOGLE_DRIVE_ERROR"
    details_example: ClassVar[dict[str, Any]] = {
        "status": 403,
        "reason": "insufficientFilePermissions",
    }

    def __init__(
        self,
        *,
        upstream_status: int | None,
        reason: str | None,
        message: str | None = None,
    ) -> None:
        """Initialize the exception.

        Args:
            upstream_status (int | None): Google's HTTP status, or ``None``
                when the request never got an answer.
            reason (str | None): Google's reason string, when the body had
                one.
            message (str | None): Override the message. Already redacted
                by the caller when it quotes an upstream body.
        """
        self.upstream_status: int | None = upstream_status
        self.reason: str | None = reason
        super().__init__(
            message=message,
            details={"status": upstream_status, "reason": reason},
        )


class GoogleDriveFolderNotFoundException(GoogleDriveException):
    """Raised when the configured Drive folder answers ``404``.

    Drive answers ``404 notFound`` both for a folder id that does not
    exist and for one the service account cannot see, so the message says
    what to do about the second, more common case: share the folder (or
    add the account as a member of the Shared Drive) with the service
    account's ``client_email``.

    Attributes:
        folder_id (str): The folder the backend writes into.
        principal_email (str | None): The account the folder must be
            shared with, when known.
    """

    message: str = "The configured Google Drive folder was not found"
    code: str = "GOOGLE_DRIVE_FOLDER_NOT_FOUND"
    details_example: ClassVar[dict[str, Any]] = {
        "status": 404,
        "reason": "notFound",
        "folder_id": "1AbC...",
    }

    def __init__(self, folder_id: str, *, principal_email: str | None) -> None:
        """Initialize the exception.

        Args:
            folder_id (str): The configured folder id.
            principal_email (str | None): The service account's
                ``client_email``, named in the message when given.
        """
        self.folder_id: str = folder_id
        self.principal_email: str | None = principal_email
        who = principal_email or "the service account"
        super().__init__(
            upstream_status=404,
            reason="notFound",
            message=(
                f"Google Drive folder {folder_id!r} was not found or is not "
                f"shared with {who}. Share the folder (or add the account as a "
                f"member of the Shared Drive) with {who}, and check "
                "GOOGLE_DRIVE_FOLDER_ID."
            ),
        )
        self.details["folder_id"] = folder_id


__all__: list[str] = [
    "EmailDeliveryException",
    "GoogleDriveException",
    "GoogleDriveFolderNotFoundException",
    "IntegrationDisabledException",
    "ZapSendFailedException",
]
