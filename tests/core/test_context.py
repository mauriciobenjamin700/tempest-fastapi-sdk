"""Tests for tempest_fastapi_sdk.core.context."""

from tempest_fastapi_sdk import (
    clear_request_id,
    get_request_id,
    request_id_ctx,
    set_request_id,
)


def test_default_request_id_is_none() -> None:
    assert get_request_id() is None


def test_set_and_clear_round_trip() -> None:
    token = set_request_id("abc-123")
    try:
        assert get_request_id() == "abc-123"
        assert request_id_ctx.get() == "abc-123"
    finally:
        clear_request_id(token)
    assert get_request_id() is None


def test_nested_sets_restore_outer_value() -> None:
    outer = set_request_id("outer")
    try:
        inner = set_request_id("inner")
        try:
            assert get_request_id() == "inner"
        finally:
            clear_request_id(inner)
        assert get_request_id() == "outer"
    finally:
        clear_request_id(outer)
    assert get_request_id() is None
