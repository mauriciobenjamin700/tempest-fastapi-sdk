"""Tests for tempest_fastapi_sdk.utils.LogUtils."""

import io
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk import (
    LogUtils,
    clear_request_id,
    reinitialize_logging,
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


def _root_stream_handler() -> logging.StreamHandler[Any]:
    """Return the stdout handler ``scope="root"`` installed on the root.

    Since v0.280.0 the handlers live on the **root** logger and each
    named logger propagates into them, so a test that wants to read what
    was emitted swaps the stream here rather than on the instance's own
    logger, which now has no handlers of its own.

    Returns:
        logging.StreamHandler[Any]: The root's stream handler.
    """
    handlers = [
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, logging.StreamHandler)
    ]
    assert handlers, "configure_root_once did not install a stream handler"
    return handlers[0]


class TestInstance:
    def _capture(self, level: str = "DEBUG") -> tuple[LogUtils, io.StringIO]:
        """Build a fresh root-scoped logger and capture its stdout.

        ``reinitialize_logging`` first because ``scope="root"``
        configures the root logger **once** per process: without the
        reset the second instance in a test session inherits the first
        one's level and formatter, and a case that asks for ``INFO``
        would silently run at whatever ran before it.

        Args:
            level (str): Minimum level for this instance.

        Returns:
            tuple[LogUtils, io.StringIO]: The facade and the buffer its
            records are written to.
        """
        reinitialize_logging()
        util = LogUtils(
            name=f"tempest.lu.inst.{level}",
            level=level,
            file_output=False,
        )
        buf = io.StringIO()
        _root_stream_handler().stream = buf
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
        reinitialize_logging()
        util = LogUtils(
            name="tempest.lu.text",
            level="INFO",
            json_output=False,
            file_output=False,
        )
        buf = io.StringIO()
        _root_stream_handler().stream = buf
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
        """Build a fresh root-scoped logger and capture its stdout.

        Returns:
            tuple[LogUtils, io.StringIO]: The facade and its buffer.
        """
        reinitialize_logging()
        util = LogUtils(name="tempest.lu.percent", level="DEBUG", file_output=False)
        buf = io.StringIO()
        _root_stream_handler().stream = buf
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


class TestRootScope:
    """``scope="root"`` is what makes ``LogUtils(__name__)`` per module safe."""

    def test_every_module_shares_one_handler_set(self, tmp_path: Path) -> None:
        """N modules must not mean N handler sets on the same files.

        The pre-0.280.0 default gave each named logger its own handlers
        with ``propagate = False``. A service with 27 modules therefore
        opened 27 stdout handlers and 162 file handlers across six
        paths, and that many ``RotatingFileHandler`` instances racing to
        roll one file over lose records.
        """
        reinitialize_logging()
        names = [f"tempest.scope.mod{index}" for index in range(5)]
        for name in names:
            LogUtils(name, log_dir=tmp_path)

        root_handlers = len(logging.getLogger().handlers)
        for name in names:
            module_logger = logging.getLogger(name)
            assert module_logger.handlers == []
            assert module_logger.propagate is True

        assert root_handlers == len(logging.getLogger().handlers)

    def test_records_from_every_module_reach_the_files(
        self,
        tmp_path: Path,
    ) -> None:
        """Sharing the root's handlers must not cost any record."""
        reinitialize_logging()
        LogUtils("tempest.scope.a", log_dir=tmp_path).info("from a")
        LogUtils("tempest.scope.b", log_dir=tmp_path).info("from b")

        messages = [
            json.loads(line)["message"]
            for line in (tmp_path / "info.log").read_text().splitlines()
        ]
        assert "from a" in messages
        assert "from b" in messages

    def test_logger_scope_keeps_the_old_isolation(self, tmp_path: Path) -> None:
        """``scope="logger"`` restores the pre-0.280.0 shape."""
        reinitialize_logging()
        util = LogUtils(
            "tempest.scope.isolated",
            log_dir=tmp_path,
            scope="logger",
        )

        assert util.logger.handlers
        assert util.logger.propagate is False


class TestError500:
    """Writing to the SDK's own ``500.log`` without importing its marker."""

    def test_lands_in_the_isolated_file(self, tmp_path: Path) -> None:
        """The whole point: a service can reach ``500.log``."""
        reinitialize_logging()
        LogUtils("tempest.e5.a", log_dir=tmp_path).error_500("grave")

        payload = json.loads((tmp_path / "500.log").read_text().strip())
        assert payload["message"] == "grave"
        assert payload["http_500"] is True

    def test_also_lands_in_error_log(self, tmp_path: Path) -> None:
        """The isolated file exists so 500s are not buried, not to move them."""
        reinitialize_logging()
        LogUtils("tempest.e5.b", log_dir=tmp_path).error_500("grave")

        assert "grave" in (tmp_path / "error.log").read_text()

    def test_a_plain_error_is_not_flagged(self, tmp_path: Path) -> None:
        """Only ``error_500`` reaches the isolated stream."""
        reinitialize_logging()
        LogUtils("tempest.e5.c", log_dir=tmp_path).error("ordinary")

        assert (tmp_path / "500.log").read_text() == ""

    def test_attaches_the_traceback_being_handled(self, tmp_path: Path) -> None:
        """A 500 that reports no traceback is the case the file exists for."""
        reinitialize_logging()
        util = LogUtils("tempest.e5.d", log_dir=tmp_path)
        try:
            raise RuntimeError("kaboom")
        except RuntimeError:
            util.error_500("handler failed")

        payload = json.loads((tmp_path / "500.log").read_text().strip())
        assert "RuntimeError: kaboom" in payload["exception"]

    def test_carries_structured_fields(self, tmp_path: Path) -> None:
        """``**fields`` keep working alongside the marker."""
        reinitialize_logging()
        LogUtils("tempest.e5.e", log_dir=tmp_path).error_500(
            "failed",
            route="/pay",
        )

        payload = json.loads((tmp_path / "500.log").read_text().strip())
        assert payload["route"] == "/pay"


class TestErrorExcInfoAuto:
    """``exc_info="auto"`` serves a call site that is sometimes in an except."""

    def test_attaches_inside_an_except(self, tmp_path: Path) -> None:
        """Inside an ``except`` the traceback is worth having."""
        reinitialize_logging()
        util = LogUtils("tempest.auto.a", log_dir=tmp_path)
        try:
            raise ValueError("boom")
        except ValueError:
            util.error("failed: %s", "boom", exc_info="auto")

        payload = json.loads((tmp_path / "error.log").read_text().strip())
        assert "ValueError: boom" in payload["exception"]

    def test_omits_it_outside_an_except(self, tmp_path: Path) -> None:
        """``True`` here would write the useless ``NoneType: None``."""
        reinitialize_logging()
        LogUtils("tempest.auto.b", log_dir=tmp_path).error(
            "no exception here",
            exc_info="auto",
        )

        payload = json.loads((tmp_path / "error.log").read_text().strip())
        assert "exception" not in payload

    def test_default_stays_off(self, tmp_path: Path) -> None:
        """Adding the option must not change what existing callers get."""
        reinitialize_logging()
        util = LogUtils("tempest.auto.c", log_dir=tmp_path)
        try:
            raise ValueError("boom")
        except ValueError:
            util.error("failed")

        payload = json.loads((tmp_path / "error.log").read_text().strip())
        assert "exception" not in payload


class TestReinitializeLogging:
    """Undoing what ``fileConfig(disable_existing_loggers=True)`` does."""

    def test_re_enables_disabled_loggers(self, tmp_path: Path) -> None:
        """Alembic's ``env.py`` silences the application's own loggers.

        The records are dropped before any handler sees them, which
        looks like a logging configuration problem and is not one.
        """
        reinitialize_logging()
        util = LogUtils("tempest.reinit.a", log_dir=tmp_path)
        util.logger.disabled = True
        util.info("swallowed")
        assert (tmp_path / "info.log").read_text() == ""

        reinitialize_logging()
        LogUtils("tempest.reinit.a", log_dir=tmp_path).info("heard")

        assert "heard" in (tmp_path / "info.log").read_text()

    def test_clears_the_configure_once_latch(self, tmp_path: Path) -> None:
        """Without clearing it, the re-attached handlers never come back."""
        reinitialize_logging()
        LogUtils("tempest.reinit.b", log_dir=tmp_path)
        for handler in list(logging.getLogger().handlers):
            logging.getLogger().removeHandler(handler)

        reinitialize_logging()
        LogUtils("tempest.reinit.b", log_dir=tmp_path)

        assert logging.getLogger().handlers


class TestReservedFieldNames:
    """A structured field that shadows a ``LogRecord`` slot is refused.

    The defect this covers shipped as an asymmetry: ``error`` took an
    ``exc_info`` parameter and the other four levels did not, so
    ``warning(exc_info="auto")`` fell into ``**fields``, became
    ``extra=``, and ``logging`` raised ``KeyError`` — inside
    ``makeRecord``, which runs *after* the level check. Measured on
    0.280.0: at level ``ERROR`` the same call did not raise; at
    ``DEBUG`` it raised ``KeyError: "Attempt to overwrite 'exc_info'
    in LogRecord"``. A service running at INFO carried a dormant
    crash that woke up the moment someone raised the verbosity.
    """

    def _util(self, level: str) -> LogUtils:
        """Build a facade at ``level`` with no file handlers.

        Args:
            level (str): Minimum level for this instance.

        Returns:
            LogUtils: The facade under test.
        """
        reinitialize_logging()
        return LogUtils(
            name=f"tempest.lu.reserved.{level}",
            level=level,
            file_output=False,
        )

    @pytest.mark.parametrize(
        "method",
        ["debug", "info", "warning", "error", "critical", "exception"],
    )
    @pytest.mark.parametrize("key", ["stack_info", "msg", "args", "levelname"])
    def test_every_level_refuses_a_reserved_field(
        self,
        method: str,
        key: str,
    ) -> None:
        util = self._util("DEBUG")
        with pytest.raises(TypeError, match=key):
            getattr(util, method)("x", **{key: 1})

    def test_error_500_refuses_it_too(self) -> None:
        util = self._util("DEBUG")
        with pytest.raises(TypeError, match="levelname"):
            util.error_500("x", levelname="FAKE")

    def test_refusal_does_not_depend_on_the_level(self) -> None:
        """The whole point: a filtered-out call fails just the same.

        ``logging`` returns before ``makeRecord`` when the level is
        disabled, so the ``KeyError`` never fired for a DEBUG line in a
        service running at ERROR. Validating at the call makes the
        failure deterministic on the first run instead of during the
        incident that raised the verbosity.
        """
        util = self._util("CRITICAL")
        assert not util.logger.isEnabledFor(logging.DEBUG)
        with pytest.raises(TypeError, match="stack_info"):
            util.debug("never emitted", stack_info=True)

    def test_message_names_every_clashing_key(self) -> None:
        util = self._util("DEBUG")
        with pytest.raises(TypeError) as caught:
            util.info("x", args=1, levelname="FAKE")
        text = str(caught.value)
        assert "'args'" in text
        assert "'levelname'" in text

    def test_a_field_that_is_not_reserved_still_lands(self) -> None:
        reinitialize_logging()
        buf = io.StringIO()
        util = LogUtils(
            name="tempest.lu.reserved.ok",
            level="DEBUG",
            file_output=False,
        )
        for handler in logging.getLogger().handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.stream = buf
        util.info("hello", exc_info_note="not the reserved name", user_id="42")
        payload = json.loads(buf.getvalue().strip())
        assert payload["exc_info_note"] == "not the reserved name"
        assert payload["user_id"] == "42"


class TestExcInfoOnEveryLevel:
    """``exc_info`` is a named parameter of every level method."""

    def _grab(self, level: str) -> tuple[LogUtils, list[logging.LogRecord]]:
        """Build a facade whose records are collected in a list.

        Args:
            level (str): Minimum level for this instance.

        Returns:
            tuple[LogUtils, list[logging.LogRecord]]: The facade and the
            records it emitted.
        """
        captured: list[logging.LogRecord] = []

        class Grab(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record)

        reinitialize_logging()
        util = LogUtils(
            name=f"tempest.lu.excinfo.{level}",
            level=level,
            file_output=False,
        )
        util.logger.handlers = [Grab()]
        util.logger.propagate = False
        return util, captured

    @pytest.mark.parametrize(
        "method",
        ["debug", "info", "warning", "error", "critical"],
    )
    def test_auto_attaches_the_traceback_inside_an_except(
        self,
        method: str,
    ) -> None:
        util, captured = self._grab("DEBUG")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            getattr(util, method)("failed", exc_info="auto")
        attached = captured[0].exc_info
        assert attached is not None
        assert attached[0] is RuntimeError

    @pytest.mark.parametrize(
        "method",
        ["debug", "info", "warning", "error", "critical"],
    )
    def test_auto_attaches_nothing_outside_an_except(self, method: str) -> None:
        """``exc_info=True`` out here would write ``NoneType: None``."""
        util, captured = self._grab("DEBUG")
        getattr(util, method)("nothing wrong", exc_info="auto")
        assert not captured[0].exc_info

    @pytest.mark.parametrize(
        "method",
        ["debug", "info", "warning", "error", "critical"],
    )
    def test_default_attaches_nothing_even_inside_an_except(
        self,
        method: str,
    ) -> None:
        util, captured = self._grab("DEBUG")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            getattr(util, method)("handled, and not worth a traceback")
        assert not captured[0].exc_info

    @pytest.mark.parametrize(
        "method",
        ["debug", "info", "warning", "error", "critical"],
    )
    def test_the_funnel_keeps_the_record_on_the_call_site(
        self,
        method: str,
    ) -> None:
        """Every level routes through ``_emit``, one extra frame.

        The default ``stacklevel=2`` still has to name the caller, not
        the facade — a funnel that forgot to account for itself would
        blame ``utils/log.py`` on every record.
        """
        util, captured = self._grab("DEBUG")

        def service_call() -> None:
            getattr(util, method)("work done")

        service_call()
        assert captured[0].funcName == "service_call"
        assert Path(captured[0].pathname).name != "log.py"
