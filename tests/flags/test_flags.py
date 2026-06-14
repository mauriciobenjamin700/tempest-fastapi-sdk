"""Tests for the feature-flags module."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import (
    CompositeFeatureFlagBackend,
    EnvFeatureFlagBackend,
    FeatureFlags,
    MemoryFeatureFlagBackend,
    RedisFeatureFlagBackend,
    coerce_flag,
    make_flag_dependency,
    register_exception_handlers,
)

# --------------------------------------------------------------------------- #
# coerce_flag                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        (" yes ", True),
        ("on", True),
        ("y", True),
        ("0", False),
        ("false", False),
        ("", False),
        ("nope", False),
    ],
)
def test_coerce_flag(value: str, expected: bool) -> None:
    """String values map to the expected boolean."""
    assert coerce_flag(value) is expected


# --------------------------------------------------------------------------- #
# FeatureFlags over MemoryFeatureFlagBackend                                  #
# --------------------------------------------------------------------------- #


async def test_is_enabled_and_default() -> None:
    """Unset flags use the service default; set flags win."""
    flags = FeatureFlags(MemoryFeatureFlagBackend({"a": True}))
    assert await flags.is_enabled("a") is True
    assert await flags.is_enabled("missing") is False
    assert await flags.is_enabled("missing", default=True) is True


async def test_enable_disable_set() -> None:
    """Toggling persists through the backend."""
    flags = FeatureFlags(MemoryFeatureFlagBackend())
    await flags.enable("x")
    assert await flags.is_enabled("x") is True
    await flags.disable("x")
    assert await flags.is_enabled("x") is False
    await flags.set("x", True)
    assert await flags.is_enabled("x") is True


async def test_service_default_true() -> None:
    """A service default of True flips unknown flags on."""
    flags = FeatureFlags(MemoryFeatureFlagBackend(), default=True)
    assert await flags.is_enabled("unknown") is True


async def test_all_returns_mapping() -> None:
    """``all`` returns every known flag."""
    flags = FeatureFlags(MemoryFeatureFlagBackend({"a": True, "b": False}))
    assert await flags.all() == {"a": True, "b": False}


# --------------------------------------------------------------------------- #
# EnvFeatureFlagBackend                                                       #
# --------------------------------------------------------------------------- #


async def test_env_backend_reads_prefixed_vars() -> None:
    """Env flags map ``new_checkout`` -> ``FEATURE_NEW_CHECKOUT``."""
    env = {"FEATURE_NEW_CHECKOUT": "true", "FEATURE_OLD": "0", "OTHER": "1"}
    backend = EnvFeatureFlagBackend(environ=env)
    assert await backend.get("new_checkout") is True
    assert await backend.get("old") is False
    assert await backend.get("absent") is None
    assert await backend.all() == {"new_checkout": True, "old": False}


async def test_env_backend_is_read_only() -> None:
    """Setting an env flag raises (static config)."""
    backend = EnvFeatureFlagBackend(environ={})
    with pytest.raises(NotImplementedError):
        await backend.set("x", True)


# --------------------------------------------------------------------------- #
# CompositeFeatureFlagBackend                                                 #
# --------------------------------------------------------------------------- #


async def test_composite_first_non_none_wins() -> None:
    """The higher-priority backend overrides the lower one."""
    redis_like = MemoryFeatureFlagBackend({"x": True})
    env = EnvFeatureFlagBackend(environ={"FEATURE_X": "false", "FEATURE_Y": "true"})
    composite = CompositeFeatureFlagBackend([redis_like, env])
    assert await composite.get("x") is True  # redis override
    assert await composite.get("y") is True  # env default
    assert await composite.get("z") is None


async def test_composite_set_skips_read_only() -> None:
    """Writes fall through to the first writable backend."""
    env = EnvFeatureFlagBackend(environ={})
    mem = MemoryFeatureFlagBackend()
    composite = CompositeFeatureFlagBackend([env, mem])
    await composite.set("x", True)
    assert await mem.get("x") is True


async def test_composite_merges_all_with_priority() -> None:
    """``all`` merges layers with the higher-priority backend winning."""
    high = MemoryFeatureFlagBackend({"shared": True})
    low = MemoryFeatureFlagBackend({"shared": False, "only_low": True})
    composite = CompositeFeatureFlagBackend([high, low])
    assert await composite.all() == {"shared": True, "only_low": True}


# --------------------------------------------------------------------------- #
# RedisFeatureFlagBackend (against a fake hash client)                        #
# --------------------------------------------------------------------------- #


class _FakeHashRedis:
    """Minimal async Redis hash fake (hget / hset / hgetall)."""

    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, str]] = {}

    async def hget(self, name: str, key: str) -> str | None:
        return self._hashes.get(name, {}).get(key)

    async def hset(self, name: str, key: str, value: str) -> int:
        self._hashes.setdefault(name, {})[key] = value
        return 1

    async def hgetall(self, name: str) -> dict[str, str]:
        return dict(self._hashes.get(name, {}))


async def test_redis_backend_roundtrip() -> None:
    """The Redis backend stores and reads flags from a hash."""
    backend = RedisFeatureFlagBackend(_FakeHashRedis(), key="ff")
    assert await backend.get("x") is None
    await backend.set("x", True)
    await backend.set("y", False)
    assert await backend.get("x") is True
    assert await backend.get("y") is False
    assert await backend.all() == {"x": True, "y": False}


# --------------------------------------------------------------------------- #
# make_flag_dependency                                                        #
# --------------------------------------------------------------------------- #


def _make_app(flags: FeatureFlags) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/beta", dependencies=[Depends(make_flag_dependency(flags, "beta"))])
    async def beta() -> dict[str, bool]:
        return {"ok": True}

    @app.get(
        "/legacy",
        dependencies=[
            Depends(make_flag_dependency(flags, "legacy_off", enabled=False))
        ],
    )
    async def legacy() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_flag_dependency_allows_when_enabled() -> None:
    """The route is reachable only when the flag is on."""
    flags = FeatureFlags(MemoryFeatureFlagBackend({"beta": True}))
    client = TestClient(_make_app(flags))
    assert client.get("/beta").status_code == 200


def test_flag_dependency_blocks_when_disabled() -> None:
    """A disabled flag returns 404 with the SDK envelope code."""
    flags = FeatureFlags(MemoryFeatureFlagBackend({"beta": False}))
    client = TestClient(_make_app(flags))
    response = client.get("/beta")
    assert response.status_code == 404
    assert response.json()["code"] == "FEATURE_DISABLED"


def test_flag_dependency_inverted_gate() -> None:
    """``enabled=False`` allows the route only while the flag is off."""
    flags = FeatureFlags(MemoryFeatureFlagBackend())
    client = TestClient(_make_app(flags))
    assert client.get("/legacy").status_code == 200  # flag unset → off → allowed
