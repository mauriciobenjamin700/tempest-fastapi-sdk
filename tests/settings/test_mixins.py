"""Tests for tempest_fastapi_sdk.settings.mixins."""

from pydantic_settings import SettingsConfigDict

from tempest_fastapi_sdk import (
    BaseAppSettings,
    CORSSettings,
    DatabaseSettings,
    JWTSettings,
    RabbitMQSettings,
    RedisSettings,
    ServerSettings,
)


class Composed(
    ServerSettings,
    DatabaseSettings,
    RedisSettings,
    RabbitMQSettings,
    JWTSettings,
    CORSSettings,
    BaseAppSettings,
):
    model_config = SettingsConfigDict(env_file=None, frozen=True)


class TestMixinDefaults:
    def test_server_settings_defaults(self) -> None:
        settings = ServerSettings()
        assert settings.HOST == "127.0.0.1"
        assert settings.PORT == 8000
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


class TestComposition:
    def test_composed_settings_loads_all_fields(self) -> None:
        s = Composed()
        assert s.HOST == "127.0.0.1"
        assert s.DATABASE_URL.endswith("app.db")
        assert s.REDIS_URL.startswith("redis://")
        assert s.RABBITMQ_URL.startswith("amqp://")
        assert s.JWT_SECRET
        assert s.CORS_ORIGINS

    def test_env_override(self, monkeypatch: object) -> None:
        import os

        os.environ["HOST"] = "0.0.0.0"
        os.environ["DATABASE_URL"] = "postgresql+asyncpg://x/y"
        try:
            s = Composed()
            assert s.HOST == "0.0.0.0"
            assert s.DATABASE_URL == "postgresql+asyncpg://x/y"
        finally:
            del os.environ["HOST"]
            del os.environ["DATABASE_URL"]
