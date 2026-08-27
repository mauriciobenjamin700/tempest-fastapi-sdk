"""Tests for tempest_fastapi_sdk.utils.LogUtils."""

import io
import json
import logging
from pathlib import Path

from tempest_fastapi_sdk import (
    LogUtils,
    clear_request_id,
    set_request_id,
)


class TestStaticHelpers:
    def test_configure_named_logger(self) -> None:
        logger = LogUtils.configure(
            level="DEBUG",
            logger_name="tempest.lu.cfg",
            file_output=False,
        )
        assert logger.level == logging.DEBUG
        assert len(logger.handlers) == 1

    def test_get_logger_returns_stdlib_logger(self) -> None:
        logger = LogUtils.get_logger("tempest.lu.get")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "tempest.lu.get"

    def test_current_request_id_reflects_contextvar(self) -> None:
        assert LogUtils.current_request_id() is None
        token = set_request_id("trace-x")
        try:
            assert LogUtils.current_request_id() == "trace-x"
        finally:
            clear_request_id(token)


class TestInstance:
    def _capture(self, level: str = "DEBUG") -> tuple[LogUtils, io.StringIO]:
        util = LogUtils(
            name=f"tempest.lu.inst.{level}",
            level=level,
            file_output=False,
        )
        buf = io.StringIO()
        util.logger.handlers[0].stream = buf  # type: ignore[attr-defined]
        return util, buf

    def test_info_emits_json_with_fields(self) -> None:
        util, buf = self._capture()
        util.info("hello", user_id="42", op="login")
        payload = json.loads(buf.getvalue().strip())
        assert payload["message"] == "hello"
        assert payload["level"] == "INFO"
        assert payload["user_id"] == "42"
        assert payload["op"] == "login"

    def test_warning_emits_record(self) -> None:
        util, buf = self._capture()
        util.warning("careful")
        payload = json.loads(buf.getvalue().strip())
        assert payload["level"] == "WARNING"

    def test_error_emits_record(self) -> None:
        util, buf = self._capture()
        util.error("boom", trace_id="abc")
        payload = json.loads(buf.getvalue().strip())
        assert payload["level"] == "ERROR"
        assert payload["trace_id"] == "abc"

    def test_critical_emits_record(self) -> None:
        util, buf = self._capture()
        util.critical("down")
        payload = json.loads(buf.getvalue().strip())
        assert payload["level"] == "CRITICAL"

    def test_debug_respects_level(self) -> None:
        util, buf = self._capture(level="INFO")
        util.debug("invisible")
        assert buf.getvalue() == ""

    def test_exception_serializes_traceback(self) -> None:
        util, buf = self._capture()
        try:
            raise RuntimeError("kaboom")
        except RuntimeError:
            util.exception("caught")
        payload = json.loads(buf.getvalue().strip())
        assert "exception" in payload
        assert "RuntimeError: kaboom" in payload["exception"]

    def test_text_mode(self) -> None:
        util = LogUtils(
            name="tempest.lu.text",
            level="INFO",
            json_output=False,
            file_output=False,
        )
        buf = io.StringIO()
        util.logger.handlers[0].stream = buf  # type: ignore[attr-defined]
        util.info("plain")
        line = buf.getvalue().strip()
        assert "INFO" in line
        assert "plain" in line
        assert not line.startswith("{")

    def test_includes_request_id_when_set(self) -> None:
        util, buf = self._capture()
        token = set_request_id("trace-7")
        try:
            util.info("with-rid")
        finally:
            clear_request_id(token)
        payload = json.loads(buf.getvalue().strip())
        assert payload["request_id"] == "trace-7"


class TestPercentStyleAndStacklevel:
    """The two things that kept an existing service on its own facade."""

    def _capture(self) -> tuple[LogUtils, io.StringIO]:
        util = LogUtils(name="tempest.lu.percent", level="DEBUG", file_output=False)
        buf = io.StringIO()
        util.logger.handlers[0].stream = buf  # type: ignore[attr-defined]
        return util, buf

    def test_percent_style_arguments_are_interpolated(self) -> None:
        """`logger.info("... %s", value)` is how existing code logs.

        Before this, the second positional was a `TypeError`, so adopting the
        facade meant rewriting every call site into f-strings (which formats
        eagerly and loses the stable template) or into `**fields` (which
        changes the message).
        """
        util, buf = self._capture()
        util.info("Email sent to %s", "ana@example.com")
        payload = json.loads(buf.getvalue().strip())
        assert payload["message"] == "Email sent to ana@example.com"

    def test_percent_style_coexists_with_structured_fields(self) -> None:
        util, buf = self._capture()
        util.error("Send to %s failed: %s", "b@x.com", "timeout", op="send_email")
        payload = json.loads(buf.getvalue().strip())
        assert payload["message"] == "Send to b@x.com failed: timeout"
        assert payload["op"] == "send_email"

    def test_record_points_at_the_call_site_not_the_facade(self) -> None:
        """`stacklevel=2` by default, or every record blames `utils/log.py`."""
        captured: list[logging.LogRecord] = []

        class Grab(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record)

        util = LogUtils(name="tempest.lu.stack", level="DEBUG", file_output=False)
        util.logger.handlers = [Grab()]

        def service_call() -> None:
            util.info("work done")

        service_call()
        assert captured[0].funcName == "service_call"
        assert Path(captured[0].pathname).name != "log.py"

    def test_stacklevel_is_tunable_for_a_wrapper_of_our_own(self) -> None:
        captured: list[logging.LogRecord] = []

        class Grab(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record)

        util = LogUtils(name="tempest.lu.stack2", level="DEBUG", file_output=False)
        util.logger.handlers = [Grab()]

        def inner_wrapper(message: str) -> None:
            util.info(message, stacklevel=3)

        def outer_call() -> None:
            inner_wrapper("through two frames")

        outer_call()
        assert captured[0].funcName == "outer_call"

    def test_exception_keeps_both_the_template_and_the_traceback(self) -> None:
        util, buf = self._capture()
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            util.exception("reconcile of %s failed", "order-1042", op="reconcile")
        payload = json.loads(buf.getvalue().strip())
        assert payload["message"] == "reconcile of order-1042 failed"
        assert "RuntimeError: boom" in payload["exception"]
