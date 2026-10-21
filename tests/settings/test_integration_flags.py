"""Per-integration kill-switches and the e-mail production rule.

A deployment that ships in phases boots before some third-party
integrations are configured. ``<NAME>_ENABLED=false`` is the explicit,
auditable "this integration is off": the production guard skips it, the
boot succeeds, ``disabled_integrations()`` names it, and the client built
from the same settings refuses every call with ``503
INTEGRATION_DISABLED`` instead of talking to a development default.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    BaseAppSettings,
    EmailSettings,
    EmailUtils,
    EnvironmentSettings,
    FirebaseSettings,
    IntegrationDisabledException,
    PushSettings,
    RevenueCatSettings,
    StorageSettings,
)
from tempest_fastapi_sdk.auth import FirebaseAuth

REAL_SENDER: str = "noreply@mail.acme.com.br"
"""A sender domain the e-mail rule accepts."""


class Composed(
    EnvironmentSettings,
    StorageSettings,
    EmailSettings,
    FirebaseSettings,
    RevenueCatSettings,
    BaseAppSettings,
):
    """Every mixin that carries a ``<NAME>_ENABLED`` flag."""


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run each test away from any ``.env`` and from inherited variables.

    Args:
        tmp_path (Path): Empty working directory.
        monkeypatch (pytest.MonkeyPatch): Environment patcher.
    """
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if (
            name.startswith(
                ("MINIO_", "STORAGE_", "SMTP_", "EMAIL_", "FIREBASE_", "REVENUECAT_")
            )
            or name == "ENV"
        ):
            monkeypatch.delenv(name)


def production(**values: object) -> Composed:
    """Build the composed settings with ``ENV=production``.

    Args:
        **values (object): Field overrides.

    Returns:
        Composed: The validated settings.
    """
    return Composed(_env_file=None, ENV="production", **values)  # type: ignore[call-arg]


def violations_of(error: pytest.ExceptionInfo[ValidationError]) -> str:
    """Return the text of a refused production boot.

    Args:
        error (pytest.ExceptionInfo[ValidationError]): The captured error.

    Returns:
        str: The validation message.
    """
    return str(error.value)


class TestDefaults:
    """Nothing changes for a deployment that never touches the flags."""

    def test_every_flag_defaults_to_on(self) -> None:
        settings = Composed(_env_file=None)  # type: ignore[call-arg]
        assert settings.STORAGE_ENABLED is True
        assert settings.EMAIL_ENABLED is True
        assert settings.FIREBASE_ENABLED is True
        assert settings.REVENUECAT_ENABLED is True
        assert settings.disabled_integrations() == []

    def test_kwargs_carry_no_enabled_key_while_on(self) -> None:
        """The dictionaries a default deployment splats are unchanged."""
        assert "enabled" not in StorageSettings().storage_kwargs()
        assert "enabled" not in EmailSettings().email_kwargs()
        assert (
            "enabled"
            not in FirebaseSettings(
                FIREBASE_CREDENTIALS_PATH="sa.json"
            ).firebase_kwargs()
        )

    def test_base_settings_contribute_nothing(self) -> None:
        assert BaseAppSettings().disabled_integrations() == []

    def test_production_still_refuses_dev_storage_with_the_flag_on(self) -> None:
        with pytest.raises(ValidationError) as error:
            production(
                SMTP_FROM_ADDR=REAL_SENDER,
                REVENUECAT_WEBHOOK_SECRET="x",
            )
        assert "STORAGE_ACCESS_KEY" in violations_of(error)


class TestDisabledBoots:
    """Off skips the integration's checks and names it, never silently."""

    def test_all_off_boots_with_every_dev_default(self) -> None:
        settings = production(
            STORAGE_ENABLED=False,
            EMAIL_ENABLED=False,
            FIREBASE_ENABLED=False,
            REVENUECAT_ENABLED=False,
        )
        assert settings.production_violations() == []
        assert sorted(settings.disabled_integrations()) == [
            "email",
            "firebase",
            "revenuecat",
            "storage",
        ]

    def test_only_the_switched_off_integration_is_skipped(self) -> None:
        with pytest.raises(ValidationError) as error:
            production(STORAGE_ENABLED=False, REVENUECAT_ENABLED=False)
        text = violations_of(error)
        assert "SMTP_FROM_ADDR" in text
        assert "STORAGE_" not in text
        assert "REVENUECAT_" not in text

    def test_flags_read_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in (
            "STORAGE_ENABLED",
            "EMAIL_ENABLED",
            "FIREBASE_ENABLED",
            "REVENUECAT_ENABLED",
        ):
            monkeypatch.setenv(name, "false")
        monkeypatch.setenv("ENV", "production")
        settings = Composed(_env_file=None)  # type: ignore[call-arg]
        assert len(settings.disabled_integrations()) == 4

    def test_revenuecat_off_skips_the_empty_secret_check(self) -> None:
        settings = RevenueCatSettings(REVENUECAT_ENABLED=False)
        assert settings.production_violations() == []
        assert RevenueCatSettings().production_violations() != []


class TestEmailProductionRule:
    """Only unmistakable development values are refused."""

    @pytest.mark.parametrize(
        ("host", "port"),
        [
            ("mailpit", 1025),
            ("mailpit", 25),
            ("MailHog", 1025),
            ("mailpit.internal", 1025),
            ("localhost", 1025),
            ("127.0.0.1", 1025),
            ("::1", 1025),
        ],
    )
    def test_dev_mail_catcher_is_refused(self, host: str, port: int) -> None:
        settings = EmailSettings(
            SMTP_HOST=host, SMTP_PORT=port, SMTP_FROM_ADDR=REAL_SENDER
        )
        violations = settings.production_violations()
        assert len(violations) == 1
        assert violations[0].startswith(("SMTP_HOST", "SMTP_PORT"))

    @pytest.mark.parametrize(
        ("host", "port"),
        [
            ("127.0.0.1", 25),
            ("127.0.0.1", 587),
            ("smtp.sendgrid.net", 1025),
            ("email-smtp.us-east-1.amazonaws.com", 587),
            ("mailpitstop.example.net", 587),
        ],
    )
    def test_real_relays_are_accepted(self, host: str, port: int) -> None:
        """``localhost`` alone is a legitimate local relay."""
        settings = EmailSettings(
            SMTP_HOST=host, SMTP_PORT=port, SMTP_FROM_ADDR=REAL_SENDER
        )
        assert settings.production_violations() == []

    @pytest.mark.parametrize(
        "sender",
        [
            "noreply@example.com",
            "noreply@example.org",
            "noreply@mg.example.com",
            "noreply@EXAMPLE.net",
            "noreply@localhost",
            "noreply@app.localhost",
            "noreply@acme.test",
            "noreply@acme.example",
            "noreply@acme.invalid",
            "noreply",
            "",
        ],
    )
    def test_placeholder_sender_is_refused(self, sender: str) -> None:
        settings = EmailSettings(SMTP_HOST="smtp.acme.com", SMTP_FROM_ADDR=sender)
        assert [v.split(":")[0] for v in settings.production_violations()] == [
            "SMTP_FROM_ADDR"
        ]

    def test_the_declared_default_is_refused(self) -> None:
        """``noreply@example.com`` cannot send real mail."""
        assert any(
            v.startswith("SMTP_FROM_ADDR")
            for v in EmailSettings().production_violations()
        )

    def test_tempest_new_env_block_is_refused(self) -> None:
        """The ``.env`` ``tempest new`` writes for MailHog never boots in prod."""
        settings = EmailSettings(
            SMTP_HOST="localhost", SMTP_PORT=1025, SMTP_FROM_ADDR="noreply@localhost"
        )
        assert len(settings.production_violations()) == 2

    def test_disabled_email_skips_the_rule(self) -> None:
        settings = EmailSettings(EMAIL_ENABLED=False, SMTP_HOST="mailpit")
        assert settings.production_violations() == []

    def test_the_message_never_carries_the_value(self) -> None:
        settings = EmailSettings(SMTP_HOST="mailpit", SMTP_FROM_ADDR="me@acme.test")
        joined = " ".join(settings.production_violations())
        assert "acme.test" not in joined
        assert "mailpit" not in joined.lower().replace("mailpit/mailhog", "")


class TestFirebaseEnabled:
    """``enabled`` follows the flag, whatever the credentials say."""

    def test_flag_off_wins_over_credentials(self) -> None:
        settings = FirebaseSettings(
            FIREBASE_CREDENTIALS_JSON="{}", FIREBASE_ENABLED=False
        )
        assert settings.enabled is False
        assert settings.disabled_integrations() == ["firebase"]

    def test_flag_on_still_needs_credentials(self) -> None:
        assert FirebaseSettings().enabled is False
        assert FirebaseSettings(FIREBASE_CREDENTIALS_PATH="sa.json").enabled is True

    def test_push_settings_mobile_half_follows_the_flag(self) -> None:
        settings = PushSettings(
            FIREBASE_CREDENTIALS_PATH="sa.json", FIREBASE_ENABLED=False
        )
        assert settings.mobile_enabled is False
        assert settings.enabled is False
        web = PushSettings(
            VAPID_PRIVATE_KEY="k",
            FIREBASE_CREDENTIALS_PATH="sa.json",
            FIREBASE_ENABLED=False,
        )
        assert web.enabled is True

    def test_kwargs_forward_nothing_but_the_switch(self) -> None:
        settings = FirebaseSettings(
            FIREBASE_CREDENTIALS_JSON="{}", FIREBASE_ENABLED=False
        )
        assert settings.firebase_kwargs() == {"enabled": False}


class TestSameConstructionLine:
    """The documented splat builds a client that refuses with the same name."""

    async def test_storage_client_refuses_with_its_name(self) -> None:
        settings = StorageSettings(STORAGE_ENABLED=False)
        assert settings.storage_kwargs()["enabled"] is False
        client = AsyncMinIOClient(**settings.storage_kwargs())
        with pytest.raises(IntegrationDisabledException) as error:
            await client.put_object("a.txt", b"x")
        assert error.value.details == {"integration": "storage"}
        assert error.value.integration in settings.disabled_integrations()

    async def test_mailer_refuses_with_its_name(self) -> None:
        settings = EmailSettings(EMAIL_ENABLED=False)
        mailer = EmailUtils(**settings.email_kwargs())
        with pytest.raises(IntegrationDisabledException) as error:
            await mailer.send("a@acme.com", "s", "b")
        assert error.value.integration in settings.disabled_integrations()

    async def test_firebase_auth_refuses_with_its_name(self) -> None:
        settings = FirebaseSettings(FIREBASE_ENABLED=False)
        auth = FirebaseAuth(**settings.firebase_kwargs())
        with pytest.raises(IntegrationDisabledException) as error:
            await auth.verify("token")
        assert error.value.integration in settings.disabled_integrations()


class TestEmailDefaultsAndGmail:
    """The declared defaults are dev markers; Gmail is production."""

    def test_unauthenticated_default_host_is_refused(self) -> None:
        settings = EmailSettings(SMTP_FROM_ADDR=REAL_SENDER)
        assert [v.split(":")[0] for v in settings.production_violations()] == [
            "SMTP_HOST"
        ]

    def test_authenticated_localhost_relay_is_accepted(self) -> None:
        settings = EmailSettings(
            SMTP_HOST="localhost",
            SMTP_PORT=25,
            SMTP_USERNAME="relay",
            SMTP_FROM_ADDR=REAL_SENDER,
        )
        assert settings.production_violations() == []

    def test_declared_default_sender_is_refused_by_identity(self) -> None:
        default = EmailSettings.model_fields["SMTP_FROM_ADDR"].default
        settings = EmailSettings(
            SMTP_HOST="smtp.acme.com", SMTP_USERNAME="u", SMTP_FROM_ADDR=default
        )
        assert [v.split(":")[0] for v in settings.production_violations()] == [
            "SMTP_FROM_ADDR"
        ]

    def test_gmail_boots_in_production(self) -> None:
        """smtp.gmail.com:587, STARTTLS, app password, real sender."""
        settings = production(
            STORAGE_ENABLED=False,
            FIREBASE_ENABLED=False,
            REVENUECAT_ENABLED=False,
            SMTP_HOST="smtp.gmail.com",
            SMTP_PORT=587,
            SMTP_USE_TLS=True,
            SMTP_USE_SSL=False,
            SMTP_USERNAME="homework.app@gmail.com",
            SMTP_PASSWORD="abcd efgh ijkl mnop",
            SMTP_FROM_ADDR="homework.app@gmail.com",
        )
        assert settings.production_violations() == []
        assert "email" not in settings.disabled_integrations()


def storage_violations(**values: object) -> list[str]:
    """Return the storage production violations for the given fields.

    The keys are real ones so only the transport rules can fire.

    Args:
        **values (object): ``STORAGE_*`` overrides.

    Returns:
        list[str]: The field names that were refused.
    """
    settings = StorageSettings(
        STORAGE_ACCESS_KEY="AKIAREAL",
        STORAGE_SECRET_KEY="real-secret",
        **values,  # type: ignore[arg-type]
    )
    return [v.split(":")[0] for v in settings.production_violations()]


class TestStorageSplitEndpoint:
    """Plain HTTP to a private bucket behind an HTTPS public endpoint."""

    @pytest.mark.parametrize(
        "endpoint",
        [
            "bucket:9000",
            "bucket",
            "localhost:9000",
            "10.0.3.7:9000",
            "172.16.0.2:9000",
            "172.31.255.1:9000",
            "192.168.1.10:9000",
            "127.0.0.1:9000",
            "[fd00::5]:9000",
        ],
    )
    def test_private_endpoint_behind_https_is_accepted(self, endpoint: str) -> None:
        assert (
            storage_violations(
                STORAGE_ENDPOINT=endpoint,
                STORAGE_SECURE=False,
                STORAGE_PUBLIC_ENDPOINT="storage.acme.com.br",
                STORAGE_PUBLIC_SECURE=True,
            )
            == []
        )

    def test_https_scheme_on_the_public_endpoint_counts(self) -> None:
        assert (
            storage_violations(
                STORAGE_ENDPOINT="bucket:9000",
                STORAGE_PUBLIC_ENDPOINT="https://storage.acme.com.br",
            )
            == []
        )

    @pytest.mark.parametrize(
        "endpoint",
        ["s3.amazonaws.com", "storage.acme.com.br:9000", "172.32.0.1:9000", "8.8.8.8"],
    )
    def test_public_endpoint_is_refused(self, endpoint: str) -> None:
        assert storage_violations(
            STORAGE_ENDPOINT=endpoint,
            STORAGE_PUBLIC_ENDPOINT="storage.acme.com.br",
            STORAGE_PUBLIC_SECURE=True,
        ) == ["STORAGE_SECURE"]

    def test_missing_public_endpoint_is_refused(self) -> None:
        assert storage_violations(STORAGE_ENDPOINT="bucket:9000") == ["STORAGE_SECURE"]

    def test_public_endpoint_over_http_is_refused(self) -> None:
        assert storage_violations(
            STORAGE_ENDPOINT="bucket:9000",
            STORAGE_PUBLIC_ENDPOINT="storage.acme.com.br",
            STORAGE_PUBLIC_SECURE=False,
        ) == ["STORAGE_SECURE", "STORAGE_PUBLIC_SECURE"]

    def test_public_endpoint_without_scheme_or_flag_is_refused(self) -> None:
        """``None`` falls back to ``STORAGE_SECURE=False``: plain HTTP."""
        assert storage_violations(
            STORAGE_ENDPOINT="bucket:9000",
            STORAGE_PUBLIC_ENDPOINT="storage.acme.com.br",
        ) == ["STORAGE_SECURE"]

    def test_the_message_names_the_escape_hatch(self) -> None:
        settings = StorageSettings(
            STORAGE_ACCESS_KEY="a", STORAGE_SECRET_KEY="b", STORAGE_SECURE=False
        )
        (message,) = settings.production_violations()
        assert "STORAGE_PUBLIC_ENDPOINT" in message
