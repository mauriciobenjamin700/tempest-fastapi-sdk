"""Social-login refusals, one identifiable ``code`` each.

The bundled OAuth flow refuses for ten distinct reasons, and a client has
to tell them apart: "sign in with your password and link the provider"
and "this provider never verified your address" are the same 409 to an
HTTP status code, but opposite instructions to a person — one has a next
step, the other has none, on purpose.

Branching on the message is not a substitute. It is English, it is
prose, and the SDK localizes the rest of the auth flow (see
:mod:`tempest_fastapi_sdk.auth.locale`), so the string a client matched
on today changes the day someone translates it.

Every class here subclasses the exception that used to be raised at that
site, so ``except ConflictException`` (or ``ForbiddenException``, or
``UnauthorizedException``) keeps catching exactly what it caught before.
What is new is the ``code`` in the serialized envelope, which is the
field :func:`~tempest_fastapi_sdk.register_exception_handlers` puts on
the wire for the client to switch on — and the field
:class:`~tempest_fastapi_sdk.AppException` warns about when a subclass
does not declare one.

Not to be confused with
:class:`~tempest_fastapi_sdk.OAuthError`, which is the **transport**
failure — the provider rejected the token exchange or the userinfo
call — and answers 502. The classes here are the flow's own decisions
about an exchange that succeeded.
"""

from tempest_fastapi_sdk.exceptions.conflict import ConflictException
from tempest_fastapi_sdk.exceptions.forbidden import ForbiddenException
from tempest_fastapi_sdk.exceptions.not_found import NotFoundException
from tempest_fastapi_sdk.exceptions.unauthorized import UnauthorizedException
from tempest_fastapi_sdk.exceptions.validation import ValidationException


class OAuthProviderNotConfiguredException(NotFoundException):
    """Raised when ``{provider}`` names no registered client.

    The provider key is part of the path, so an unregistered one is an
    unknown route rather than a bad request.
    """

    message: str = "Unknown OAuth provider"
    code: str = "OAUTH_PROVIDER_NOT_CONFIGURED"


class OAuthProviderDeniedException(UnauthorizedException):
    """Raised when the provider returned ``error=`` instead of a code.

    Almost always the user declining the consent screen, which is a
    normal outcome and deserves a different message from a failure.
    """

    message: str = "The provider did not authorize the login"
    code: str = "OAUTH_PROVIDER_DENIED"


class OAuthStateMismatchException(UnauthorizedException):
    """Raised when the callback's ``state`` does not match the cookie.

    Either the login was not started by this browser — a forged
    callback — or the state cookie expired mid-consent. The client
    should restart the flow, not retry the callback.
    """

    message: str = "OAuth state mismatch — the callback was not started by this browser"
    code: str = "OAUTH_STATE_MISMATCH"


class OAuthCodeMissingException(ValidationException):
    """Raised when the callback carried neither a ``code`` nor an ``error``."""

    message: str = "The callback carried no authorization code"
    code: str = "OAUTH_CODE_MISSING"


class OAuthEmailMissingException(ValidationException):
    """Raised when the provider returned no email address.

    The user column is ``NOT NULL UNIQUE``, so there is nothing to
    store, and inventing an address would create an account nobody can
    recover. The client's next step is to ask the provider for the email
    scope, not to retry.
    """

    message: str = "The identity provider returned no email address"
    code: str = "OAUTH_EMAIL_MISSING"


class OAuthEmailTakenException(ConflictException):
    """Raised when the email already belongs to another local account.

    There **is** a next step for the user: sign in with the password
    they already have and link the provider from their account
    settings.
    """

    message: str = (
        "Email already registered — sign in and link the provider "
        "from your account settings"
    )
    code: str = "OAUTH_EMAIL_TAKEN"


class OAuthEmailUnverifiedException(ConflictException):
    """Raised when linking by email was allowed but the provider did not verify it.

    Distinct from :class:`OAuthEmailTakenException` on purpose: this is
    the barrier that stops someone who registered a provider identity
    carrying the victim's address from taking the account over, and
    there is **no** action the user can take that clears it. A client
    that shows "sign in and link it" here is telling the person to do
    something that cannot work.
    """

    message: str = "The identity provider did not verify this email"
    code: str = "OAUTH_EMAIL_UNVERIFIED"


class OAuthRegistrationDisabledException(ForbiddenException):
    """Raised when the identity is new and account creation is off.

    The deployment decided who may have an account
    (``AUTH_OAUTH_ALLOW_ACCOUNT_CREATION``, inheriting
    ``AUTH_SIGNUP_ENABLED``), so this is a policy answer, not a
    failure.
    """

    message: str = (
        "This account does not exist and self-service registration is disabled"
    )
    code: str = "OAUTH_REGISTRATION_DISABLED"


class OAuthAccountInactiveException(UnauthorizedException):
    """Raised when the identity resolves to a deactivated account.

    A deactivated account must not be revived by arriving through the
    provider instead of the login form.
    """

    message: str = "Account is not active"
    code: str = "OAUTH_ACCOUNT_INACTIVE"


class OAuthAccountNotLinkedException(NotFoundException):
    """Raised when unlinking a provider the caller never linked.

    The lookup is scoped to the caller, so a provider linked by somebody
    else answers exactly like one that was never linked — the endpoint
    is not an oracle for other people's accounts.
    """

    message: str = "Provider is not linked to this account"
    code: str = "OAUTH_ACCOUNT_NOT_LINKED"


__all__: list[str] = [
    "OAuthAccountInactiveException",
    "OAuthAccountNotLinkedException",
    "OAuthCodeMissingException",
    "OAuthEmailMissingException",
    "OAuthEmailTakenException",
    "OAuthEmailUnverifiedException",
    "OAuthProviderDeniedException",
    "OAuthProviderNotConfiguredException",
    "OAuthRegistrationDisabledException",
    "OAuthStateMismatchException",
]
