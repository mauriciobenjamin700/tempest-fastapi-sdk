"""Tests for tempest_fastapi_sdk.core.logging."""

import io
import json
import logging

from tempest_fastapi_sdk import (
    JSONFormatter,
    clear_request_id,
    configure_logging,
    set_request_id,
)


def _make_record(
    *,
    msg: str = "hello",
    level: int = logging.INFO,
    extra: dict[str, object] | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="tempest.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    if extra:
        for k, v in extra.items():
            setattr(record, k, v)
    return record


class TestJSONFormatter:
    def test_minimal_record(self) -> None:
        fmt = JSONFormatter()
        payload = json.loads(fmt.format(_make_record()))
        assert payload["level"] == "INFO"
        assert payload["logger"] == "tempest.test"
        assert payload["message"] == "hello"
        assert "timestamp" in payload
        assert payload["timestamp"].endswith("Z")
        assert "request_id" not in payload

    def test_request_id_attached_when_set(self) -> None:
        fmt = JSONFormatter()
        token = set_request_id("trace-1")
        try:
            payload = json.loads(fmt.format(_make_record()))
        finally:
            clear_request_id(token)
        assert payload["request_id"] == "trace-1"

    def test_extra_fields_propagate(self) -> None:
        fmt = JSONFormatter()
        record = _make_record(extra={"user_id": "u1", "tenant": "acme"})
        payload = json.loads(fmt.format(record))
        assert payload["user_id"] == "u1"
        assert payload["tenant"] == "acme"

    def test_exception_serialized(self) -> None:
        fmt = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="tempest.test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="failed",
                args=(),
                exc_info=sys.exc_info(),
            )
        payload = json.loads(fmt.format(record))
        assert "exception" in payload
        assert "ValueError: boom" in payload["exception"]


class TestConfigureLogging:
    def test_named_logger_emits_json(self) -> None:
        logger = configure_logging(level="DEBUG", logger_name="tempest.cfg.test")
        buf = io.StringIO()
        logger.handlers[0].stream = buf  # type: ignore[attr-defined]
        logger.info("emit", extra={"foo": "bar"})
        payload = json.loads(buf.getvalue().strip())
        assert payload["message"] == "emit"
        assert payload["foo"] == "bar"

    def test_replaces_existing_handlers(self) -> None:
        first = configure_logging(logger_name="tempest.cfg.replace")
        first_id = id(first.handlers[0])
        second = configure_logging(logger_name="tempest.cfg.replace")
        assert len(second.handlers) == 1
        assert id(second.handlers[0]) != first_id

    def test_text_mode_uses_plain_formatter(self) -> None:
        logger = configure_logging(
            logger_name="tempest.cfg.text",
            json_output=False,
        )
        buf = io.StringIO()
        logger.handlers[0].stream = buf  # type: ignore[attr-defined]
        logger.warning("plain")
        line = buf.getvalue().strip()
        assert "WARNING" in line
        assert "plain" in line
        assert not line.startswith("{")
