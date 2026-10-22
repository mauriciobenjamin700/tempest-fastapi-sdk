"""503s raised when an integration is switched off or cannot be reached."""

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

    Attributes:
        integration (str): The integration that refused the call.
    """

    status_code: int = 503
    message: str = "This integration is disabled in this environment"
    code: str = "INTEGRATION_DISABLED"
    details_example: ClassVar[dict[str, Any]] = {"integration": "email"}

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
    They are logged once, at ``ERROR``, by the mailer that raised.

    It is not :class:`IntegrationDisabledException`: that one means the
    deployment chose to run without e-mail, this one means e-mail is
    configured and failed.
    """

    status_code: int = 503
    message: str = "The e-mail server could not be reached; try again later"
    code: str = "EMAIL_UNAVAILABLE"


__all__: list[str] = [
    "EmailDeliveryException",
    "IntegrationDisabledException",
]
