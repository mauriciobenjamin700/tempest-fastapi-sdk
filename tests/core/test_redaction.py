"""Tests for ``core.redaction`` and ``configure_logging(redact=...)``."""

import io
import json
import logging
import re
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from tempest_fastapi_sdk import (
    REDACTED,
    JSONFormatter,
    RedactionFilter,
    RedactionPolicy,
    configure_logging,
)

EMAIL: str = "ana.souza@example.com.br"
JWT: str = (
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ."
    "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
)


def _capture(
    name: str,
    *,
    redact: bool | RedactionPolicy = True,
) -> tuple[logging.Logger, io.StringIO]:
    """Configure ``name`` with redaction and swap stdout for a buffer.

    Args:
        name (str): Logger to configure.
        redact (bool | RedactionPolicy): Forwarded to ``configure_logging``.

    Returns:
        tuple[logging.Logger, io.StringIO]: The logger and the buffer its
        stdout handler writes to.
    """
    logger = configure_logging(logger_name=name, file_output=False, redact=redact)
    buffer = io.StringIO()
    handler = logger.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(buffer)
    return logger, buffer


def _payload(buffer: io.StringIO) -> dict[str, object]:
    """Parse the single JSON line written to ``buffer``.

    Args:
        buffer (io.StringIO): The captured stream.

    Returns:
        dict[str, object]: The decoded record.
    """
    lines = buffer.getvalue().splitlines()
    assert len(lines) == 1
    decoded: dict[str, object] = json.loads(lines[0])
    return decoded


@pytest.fixture(autouse=True)
def _reset_loggers() -> Iterator[None]:
    """Close the handlers each test installs on its ``tempest.redact.*`` logger."""
    yield
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith("tempest.redact"):
            logger = logging.getLogger(name)
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)


class TestAcceptance:
    """The four acceptance criteria of issue #445."""

    def test_exception_carrying_an_email_is_redacted(self) -> None:
        logger, buffer = _capture("tempest.redact.exc")
        try:
            raise ValueError(f"no user with e-mail {EMAIL}")
        except ValueError:
            logger.exception("lookup failed")

        payload = _payload(buffer)
        exception = str(payload["exception"])
        assert EMAIL not in exception
        assert REDACTED in exception
        assert "ValueError" in exception

    def test_sensitive_extra_keys_are_redacted(self) -> None:
        logger, buffer = _capture("tempest.redact.extra")
        logger.info(
            "signup",
            extra={"email": EMAIL, "access_token": "abc123", "plan": "free"},
        )

        payload = _payload(buffer)
        assert payload["email"] == REDACTED
        assert payload["access_token"] == REDACTED
        assert payload["plan"] == "free"

    def test_service_adds_domain_keys_without_rewriting_the_filter(self) -> None:
        policy = RedactionPolicy(extra_keys={"cpf", "guardian_phone"})
        logger, buffer = _capture("tempest.redact.domain", redact=policy)
        logger.info(
            "enrolled",
            extra={"cpf": "123.456.789-00", "guardian_phone": "+55 86 9", "token": "t"},
        )

        payload = _payload(buffer)
        assert payload["cpf"] == REDACTED
        assert payload["guardian_phone"] == REDACTED
        assert payload["token"] == REDACTED

    def test_formatter_prefers_exc_text_over_exc_info(self) -> None:
        """Guard: the formatter must not re-render ``exc_info`` over ``exc_text``.

        Before #445 ``JSONFormatter`` called ``formatException(exc_info)``
        unconditionally, so a handler filter that redacted ``exc_text`` was
        overwritten with the original traceback.
        """
        try:
            raise RuntimeError(f"secret {EMAIL}")
        except RuntimeError:
            record = logging.LogRecord(
                "tempest.redact.guard",
                logging.ERROR,
                __file__,
                1,
                "boom",
                None,
                sys.exc_info(),
            )
        record.exc_text = "already rendered and redacted"

        payload = json.loads(JSONFormatter().format(record))

        assert payload["exception"] == "already rendered and redacted"


class TestMessageAndArgs:
    def test_email_in_args_is_redacted(self) -> None:
        logger, buffer = _capture("tempest.redact.args")
        logger.info("sent code to %s", EMAIL)

        assert _payload(buffer)["message"] == f"sent code to {REDACTED}"

    def test_mapping_args_have_sensitive_keys_redacted(self) -> None:
        logger, buffer = _capture("tempest.redact.mapping")
        logger.info("login %(user)s pw=%(password)s", {"user": "ana", "password": "x"})

        assert _payload(buffer)["message"] == f"login ana pw={REDACTED}"

    def test_bearer_and_jwt_in_message_are_redacted(self) -> None:
        logger, buffer = _capture("tempest.redact.bearer")
        logger.info("header Authorization: Bearer %s and raw %s", "opaque.tok", JWT)

        message = str(_payload(buffer)["message"])
        assert "opaque.tok" not in message
        assert JWT not in message
        assert "eyJ" not in message

    def test_key_value_pairs_in_free_text_are_redacted(self) -> None:
        logger, buffer = _capture("tempest.redact.kv")
        logger.info("GET /verify?token=s3cr3t&page=2 body={'password': 'hunter2'}")

        message = str(_payload(buffer)["message"])
        assert "s3cr3t" not in message
        assert "hunter2" not in message
        assert "page=2" in message

    def test_mismatched_args_do_not_raise_from_the_filter(self) -> None:
        """A broken format string is the handler's error, reported redacted."""
        record = logging.LogRecord(
            "tempest.redact.bad", logging.INFO, __file__, 1, "%s %s", (EMAIL,), None
        )

        assert RedactionFilter().filter(record) is True
        assert record.args == (REDACTED,)


class TestTracebackAndStack:
    def test_text_formatter_also_gets_the_redacted_traceback(self) -> None:
        logger = configure_logging(
            logger_name="tempest.redact.text",
            file_output=False,
            json_output=False,
            redact=True,
        )
        buffer = io.StringIO()
        handler = logger.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        handler.setStream(buffer)
        try:
            raise KeyError(EMAIL)
        except KeyError:
            logger.exception("missing")

        assert EMAIL not in buffer.getvalue()
        assert "KeyError" in buffer.getvalue()

    def test_stack_info_is_redacted(self) -> None:
        record = logging.LogRecord(
            "tempest.redact.stack", logging.INFO, __file__, 1, "m", None, None
        )
        record.stack_info = f"Stack (most recent call last):\n  user={EMAIL}"

        RedactionFilter().filter(record)

        assert record.stack_info is not None
        assert EMAIL not in record.stack_info


class TestExtraValues:
    def test_nested_containers_are_walked(self) -> None:
        logger, buffer = _capture("tempest.redact.nested")
        logger.info(
            "payload",
            extra={
                "body": {
                    "user": {"name": "Ana", "password": "x"},
                    "contacts": [EMAIL, {"api-key": "k"}],
                },
            },
        )

        body = _payload(buffer)["body"]
        assert body == {
            "user": {"name": "Ana", "password": REDACTED},
            "contacts": [REDACTED, {"api-key": REDACTED}],
        }

    def test_object_repr_goes_through_the_patterns(self) -> None:
        class User:
            def __repr__(self) -> str:
                return f"User(email={EMAIL!r})"

            __str__ = __repr__

        logger, buffer = _capture("tempest.redact.obj")
        logger.info("loaded", extra={"user": User()})

        assert EMAIL not in str(_payload(buffer)["user"])

    def test_self_reference_terminates(self) -> None:
        loop: list[object] = []
        loop.append(loop)

        redacted = RedactionPolicy().redact_value({"loop": loop})

        assert isinstance(redacted, dict)

    def test_scalars_and_500_marker_survive(self) -> None:
        logger, buffer = _capture("tempest.redact.scalars")
        logger.error("x", extra={"http_500": True, "duration_ms": 1.5, "count": 3})

        payload = _payload(buffer)
        assert payload["http_500"] is True
        assert payload["duration_ms"] == 1.5
        assert payload["count"] == 3


class TestPolicy:
    def test_key_matching_is_substring_and_normalized(self) -> None:
        policy = RedactionPolicy()

        assert policy.is_sensitive_key("X-API-Key")
        assert policy.is_sensitive_key("Set-Cookie")
        assert policy.is_sensitive_key("client_secret")
        assert policy.is_sensitive_key("refresh_token")
        assert not policy.is_sensitive_key("http_path")

    def test_keys_replace_the_defaults(self) -> None:
        policy = RedactionPolicy(keys={"cpf"})

        assert policy.is_sensitive_key("cpf")
        assert not policy.is_sensitive_key("password")

    def test_string_patterns_are_compiled_and_custom_replacement_used(self) -> None:
        policy = RedactionPolicy(
            extra_patterns=[r"\d{3}\.\d{3}\.\d{3}-\d{2}"],
            replacement="***",
        )

        assert policy.redact_text("cpf 123.456.789-00 ok") == "cpf *** ok"

    def test_empty_policy_redacts_nothing(self) -> None:
        policy = RedactionPolicy(keys=(), patterns=())

        assert policy.redact_text(f"token={EMAIL}") == f"token={EMAIL}"

    def test_redact_value_does_not_mutate_the_input(self) -> None:
        original = {"password": "x", "nested": [EMAIL]}

        RedactionPolicy().redact_value(original)

        assert original == {"password": "x", "nested": [EMAIL]}

    def test_patterns_accept_precompiled(self) -> None:
        policy = RedactionPolicy(keys=(), patterns=[re.compile("foo")])

        assert policy.redact_text("a foo b") == f"a {REDACTED} b"


class TestWiring:
    def test_default_installs_no_filter(self) -> None:
        logger = configure_logging(logger_name="tempest.redact.off", file_output=False)

        assert not [
            f for f in logger.handlers[0].filters if isinstance(f, RedactionFilter)
        ]

    def test_default_leaves_records_untouched(self) -> None:
        logger, buffer = _capture("tempest.redact.default", redact=False)
        logger.info("mail %s", EMAIL, extra={"password": "x"})

        payload = _payload(buffer)
        assert payload["message"] == f"mail {EMAIL}"
        assert payload["password"] == "x"

    def test_every_file_handler_gets_the_filter(self, tmp_path: Path) -> None:
        logger = configure_logging(
            logger_name="tempest.redact.files",
            log_dir=tmp_path,
            redact=True,
        )
        logger.error("boom for %s", EMAIL, extra={"http_500": True})
        for handler in logger.handlers:
            handler.flush()

        assert len(logger.handlers) == 7
        for handler in logger.handlers:
            assert any(isinstance(f, RedactionFilter) for f in handler.filters)
        for name in ("error.log", "500.log"):
            text = (tmp_path / name).read_text(encoding="utf-8")
            assert text
            assert EMAIL not in text

    def test_child_logger_records_are_redacted(self) -> None:
        """Propagated records skip logger filters but not handler filters."""
        logger, buffer = _capture("tempest.redact.parent")
        logger.propagate = False
        child = logging.getLogger("tempest.redact.parent.child")

        child.warning("child saw %s", EMAIL)

        assert EMAIL not in str(_payload(buffer)["message"])

    def test_record_is_redacted_once_per_policy(self) -> None:
        """A second handler with the same policy skips the work."""
        policy = RedactionPolicy()
        record = logging.LogRecord(
            "tempest.redact.once", logging.INFO, __file__, 1, "%s", (EMAIL,), None
        )
        RedactionFilter(policy).filter(record)
        record.msg = f"planted {EMAIL}"

        RedactionFilter(policy).filter(record)
        assert record.msg == f"planted {EMAIL}"

        RedactionFilter(RedactionPolicy()).filter(record)
        assert record.msg == f"planted {REDACTED}"
