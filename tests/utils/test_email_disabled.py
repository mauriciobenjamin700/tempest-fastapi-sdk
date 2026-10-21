"""``EmailUtils(enabled=False)`` — the e-mail half of the kill-switch."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk import EmailUtils, IntegrationDisabledException
from tempest_fastapi_sdk.utils import email as email_module


def disabled_mailer() -> EmailUtils:
    """Build the mailer ``email_kwargs()`` yields under ``EMAIL_ENABLED=false``.

    Returns:
        EmailUtils: A mailer built with ``enabled=False``.
    """
    return EmailUtils("localhost", 1025, from_addr="noreply@localhost", enabled=False)


async def test_send_refuses_before_connecting() -> None:
    with pytest.raises(IntegrationDisabledException) as error:
        await disabled_mailer().send("a@acme.com", "subject", "body")
    assert error.value.status_code == 503
    assert error.value.details == {"integration": "email"}


async def test_send_many_refuses_even_an_empty_batch() -> None:
    """An empty recipient list must not hide that nothing could be sent."""
    with pytest.raises(IntegrationDisabledException):
        await disabled_mailer().send_many([], "subject", "body")


def test_require_enabled_is_the_public_check() -> None:
    with pytest.raises(IntegrationDisabledException):
        disabled_mailer().require_enabled()
    EmailUtils("h", 25, from_addr="a@acme.com").require_enabled()


def test_builds_without_the_email_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_module, "_aiosmtplib", None)
    assert disabled_mailer().enabled is False
    with pytest.raises(ImportError):
        EmailUtils("h", 25, from_addr="a@acme.com")
