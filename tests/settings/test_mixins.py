"""Tests for tempest_fastapi_sdk.settings.mixins."""

import os

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
