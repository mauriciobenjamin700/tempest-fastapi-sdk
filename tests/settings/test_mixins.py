"""Tests for tempest_fastapi_sdk.settings.mixins."""

import os
from pathlib import Path

import pytest
from pydantic_settings import SettingsConfigDict

from tempest_fastapi_sdk import (
    BaseAppSettings,
    CORSSettings,
    DatabaseSettings,
    EmailSettings,
    JWTSettings,
    LogSettings,
    RabbitMQSettings,
    RedisSettings,
    ServerSettings,
    TaskIQSettings,
    TokenSettings,
    UploadSettings,
    WebPushSettings,
)


class Composed(
    ServerSettings,
    LogSettings,
    DatabaseSettings,
    RedisSettings,
    RabbitMQSettings,
    JWTSettings,
    CORSSettings,
    EmailSettings,
    UploadSettings,
    TokenSettings,
    WebPushSettings,
    TaskIQSettings,
    BaseAppSettings,
):
    """Aggregate every mixin to verify they compose cleanly."""

    model_config = SettingsConfigDict(env_file=None, frozen=True)


class TestMixinDefaults:
    def test_server_settings_defaults(self) -> None:
        settings = ServerSettings()
        assert settings.SERVER_HOST == "127.0.0.1"
        assert settings.SERVER_PORT == 8000
        assert settings.SERVER_RELOAD is False
        assert settings.SERVER_DEBUG is False

    def test_log_settings_defaults(self) -> None:
        settings = LogSettings()
        assert settings.LOG_LEVEL == "INFO"
        assert settings.LOG_JSON is True

    def test_database_settings_defaults(self) -> None:
        settings = DatabaseSettings()
        assert "sqlite" in settings.DATABASE_URL
        assert settings.DATABASE_POOL_SIZE == 10

    def test_redis_settings_defaults(self) -> None:
        settings = RedisSettings()
        assert settings.REDIS_URL.startswith("redis://")
        assert settings.REDIS_DECODE_RESPONSES is True

    def test_rabbitmq_settings_defaults(self) -> None:
        settings = RabbitMQSettings()
        assert settings.RABBITMQ_URL.startswith("amqp://")

    def test_jwt_settings_defaults(self) -> None:
        settings = JWTSettings()
        assert len(settings.JWT_SECRET) >= 32
        assert settings.JWT_ALGORITHM == "HS256"

    def test_cors_settings_defaults(self) -> None:
        settings = CORSSettings()
        assert settings.CORS_ORIGINS == ["*"]
        assert settings.CORS_EXPOSE_HEADERS == ["X-Request-ID"]

    def test_email_settings_defaults(self) -> None:
        settings = EmailSettings()
        assert settings.SMTP_HOST == "localhost"
        assert settings.SMTP_PORT == 587
        assert settings.SMTP_USE_TLS is True
        assert settings.SMTP_USE_SSL is False
        assert settings.SMTP_FROM_ADDR.endswith("@example.com")
        assert settings.SMTP_USERNAME is None
        assert settings.SMTP_PASSWORD is None

    def test_email_kwargs_maps_smtp_names_to_emailutils(self) -> None:
        settings = EmailSettings(
            SMTP_HOST="smtp.example.com",
            SMTP_PORT=465,
            SMTP_FROM_ADDR="ops@example.com",
            SMTP_USERNAME="apikey",
            SMTP_PASSWORD="secret",
            SMTP_USE_TLS=False,  # STARTTLS off
            SMTP_USE_SSL=True,  # implicit TLS on
            SMTP_TIMEOUT_SECONDS=10.0,
        )
        kwargs = settings.email_kwargs()
        assert kwargs == {
            "host": "smtp.example.com",
            "port": 465,
            "from_addr": "ops@example.com",
            "username": "apikey",
            "password": "secret",
            # SMTP_USE_TLS (STARTTLS) -> use_starttls; SMTP_USE_SSL -> use_tls.
            "use_tls": True,
            "use_starttls": False,
            "timeout": 10.0,
        }

    def test_email_kwargs_splats_into_emailutils(self) -> None:
        # Guards the documented `EmailUtils(**settings.email_kwargs())`
        # recipe: every key must be a real constructor parameter.
        from tempest_fastapi_sdk import EmailUtils

        mailer = EmailUtils(**EmailSettings().email_kwargs())
        assert mailer.host == "localhost"
        assert mailer.use_starttls is True  # mirrors SMTP_USE_TLS default

    def test_database_kwargs_splats_into_manager(self) -> None:
        from tempest_fastapi_sdk import AsyncDatabaseManager

        kwargs = DatabaseSettings().database_kwargs()
        assert set(kwargs) == {
            "db_url",
            "echo",
            "pool_size",
            "max_overflow",
            "pool_recycle",
        }
        manager = AsyncDatabaseManager(**kwargs)  # no connection opened
        assert manager is not None

    def test_redis_kwargs_splats_into_manager(self) -> None:
        from tempest_fastapi_sdk.cache import AsyncRedisManager

        kwargs = RedisSettings().redis_kwargs()
        assert kwargs == {"url": "redis://localhost:6379/0", "decode_responses": True}
        assert AsyncRedisManager(**kwargs) is not None

    def test_jwt_kwargs_splats_into_jwtutils(self) -> None:
        from datetime import timedelta

        from tempest_fastapi_sdk import JWTUtils

        kwargs = JWTSettings().jwt_kwargs()
        assert kwargs["default_ttl"] == timedelta(seconds=3600)
        assert set(kwargs) == {"secret", "algorithm", "default_ttl", "issuer"}
        assert JWTUtils(**kwargs) is not None

    def test_upload_kwargs_splats_into_uploadutils(self) -> None:
        from tempest_fastapi_sdk import UploadUtils

        kwargs = UploadSettings().upload_kwargs()
        assert set(kwargs) == {
            "source",
            "max_size_bytes",
            "allowed_extensions",
            "allowed_mimetypes",
        }
        assert UploadUtils(**kwargs) is not None

    def test_webpush_kwargs_splats_into_dispatcher(self) -> None:
        from tempest_fastapi_sdk import WebPushDispatcher

        kwargs = WebPushSettings().webpush_kwargs()
        assert set(kwargs) == {"vapid_private_key", "vapid_subject", "ttl_seconds"}
        assert WebPushDispatcher(**kwargs) is not None

    def test_enabled_reflects_private_key(self) -> None:
        assert WebPushSettings().enabled is False
        assert WebPushSettings(VAPID_PRIVATE_KEY="k").enabled is True

    def test_minio_kwargs_splats_into_client(self) -> None:
        from tempest_fastapi_sdk import AsyncMinIOClient, MinIOSettings

        kwargs = MinIOSettings().minio_kwargs()
        assert set(kwargs) == {
            "endpoint",
            "access_key",
            "secret_key",
            "default_bucket",
            "secure",
            "region",
            "public_endpoint",
            "public_secure",
        }
        assert AsyncMinIOClient(**kwargs) is not None

    def test_upload_settings_defaults(self) -> None:
        settings = UploadSettings()
        assert settings.UPLOAD_DIR == "./var/uploads"
        assert settings.UPLOAD_MAX_SIZE_BYTES == 10 * 1024 * 1024
        assert len(settings.UPLOAD_ALLOWED_EXTENSIONS) == 0
        assert len(settings.UPLOAD_ALLOWED_MIMETYPES) == 0

    def test_token_settings_defaults(self) -> None:
        settings = TokenSettings()
        assert settings.TOKEN_SECRET == ""

    def test_webpush_settings_defaults(self) -> None:
        settings = WebPushSettings()
        assert settings.VAPID_PUBLIC_KEY == ""
        assert settings.VAPID_PRIVATE_KEY == ""
        assert settings.VAPID_SUBJECT.startswith("mailto:")
        assert settings.WEBPUSH_DEFAULT_TTL_SECONDS == 86_400

    def test_taskiq_settings_defaults(self) -> None:
        settings = TaskIQSettings()
        assert settings.TASKIQ_BROKER_URL.startswith("amqp://")
        assert settings.TASKIQ_RESULT_BACKEND_URL is None


class TestComposition:
    def test_composed_settings_loads_all_fields(self) -> None:
        s = Composed()
        assert s.SERVER_HOST == "127.0.0.1"
        assert s.LOG_LEVEL == "INFO"
        assert s.DATABASE_URL.endswith("app.db")
        assert s.REDIS_URL.startswith("redis://")
        assert s.RABBITMQ_URL.startswith("amqp://")
        assert s.JWT_SECRET
        assert s.CORS_ORIGINS
        assert s.SMTP_HOST == "localhost"
        assert s.UPLOAD_DIR == "./var/uploads"
        assert s.TOKEN_SECRET == ""
        assert s.VAPID_SUBJECT.startswith("mailto:")
        assert s.TASKIQ_BROKER_URL.startswith("amqp://")

    def test_env_override(self) -> None:
        os.environ["SERVER_HOST"] = "0.0.0.0"
        os.environ["DATABASE_URL"] = "postgresql+asyncpg://x/y"
        os.environ["SMTP_HOST"] = "smtp.gmail.com"
        os.environ["TOKEN_SECRET"] = "super-secret"
        try:
            s = Composed()
            assert s.SERVER_HOST == "0.0.0.0"
            assert s.DATABASE_URL == "postgresql+asyncpg://x/y"
            assert s.SMTP_HOST == "smtp.gmail.com"
            assert s.TOKEN_SECRET == "super-secret"
        finally:
            for key in ("SERVER_HOST", "DATABASE_URL", "SMTP_HOST", "TOKEN_SECRET"):
                os.environ.pop(key, None)


class TestEnvFilePriority:
    """Regression: composing mixins must keep ``env_file=".env"``.

    Each mixin inherits :class:`BaseAppSettings`, so the canonical
    ``model_config`` (``env_file=".env"``, ``extra="ignore"``,
    ``case_sensitive=True``) survives on the composed class no matter
    where the mixins sit relative to ``BaseAppSettings`` in the bases.

    Before the fix the mixins inherited raw
    ``pydantic_settings.BaseSettings``; pydantic materialized a complete
    ``model_config`` (``env_file=None``) onto each of them, and a mixin
    listed before ``BaseAppSettings`` overwrote the whole config — so
    ``.env`` was silently ignored and ``DATABASE_URL`` fell back to the
    SQLite default.
    """

    def test_env_file_survives_mixins_before_base(self) -> None:
        """``model_config`` keeps the ``.env`` defaults with no re-declaration."""

        class Settings(ServerSettings, DatabaseSettings, BaseAppSettings):
            """Mixins ahead of ``BaseAppSettings`` — the historical trap order."""

        assert Settings.model_config.get("env_file") == ".env"
        assert Settings.model_config.get("extra") == "ignore"
        assert Settings.model_config.get("case_sensitive") is True

    def test_dotenv_loaded_when_mixins_precede_base(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A value set only in ``.env`` reaches a composed ``Settings``."""

        env = tmp_path / ".env"
        env.write_text("DATABASE_URL=postgresql+asyncpg://app:app@localhost:5432/app\n")
        monkeypatch.chdir(tmp_path)

        class Settings(ServerSettings, DatabaseSettings, BaseAppSettings):
            """Composed exactly as a real service would, ``.env`` present."""

        assert (
            Settings().DATABASE_URL == "postgresql+asyncpg://app:app@localhost:5432/app"
        )
