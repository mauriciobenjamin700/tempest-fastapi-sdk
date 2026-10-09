"""Redaction of personal data and credentials from log records.

A service that handles personal data has to keep e-mails, tokens and
verification codes out of every log line — including the ones it did not
write itself: a traceback that quotes the payload, an ``extra=`` dict
someone passed whole, a third-party library logging an ``Authorization``
header. :class:`RedactionPolicy` describes what to hide and
:class:`RedactionFilter` applies it to a :class:`logging.LogRecord` before
any formatter sees it. :func:`tempest_fastapi_sdk.configure_logging` hangs
the filter on every handler it installs when called with ``redact=True``.
"""

import logging
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

REDACTED: str = "[REDACTED]"
"""The default text that replaces every redacted value."""

DEFAULT_REDACT_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "email",
        "otp",
        "passwd",
        "password",
        "private_key",
        "secret",
        "token",
        "verification_code",
    }
)
"""Key fragments whose value is always redacted.

Matching is by **substring** on the key normalized to lower case with
``-`` turned into ``_``, so ``access_token``, ``X-API-Key``,
``Set-Cookie`` and ``client_secret`` all match. Substring matching errs on
the side of hiding: ``email_verified`` and ``token_type`` are redacted too,
which costs a field's readability and never leaks one.
"""

EMAIL_PATTERN: re.Pattern[str] = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
)
"""An e-mail address anywhere in a string."""

BEARER_PATTERN: re.Pattern[str] = re.compile(
    r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*",
    re.IGNORECASE,
)
"""A ``Bearer <credential>`` pair, the shape of an ``Authorization`` header."""

JWT_PATTERN: re.Pattern[str] = re.compile(
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"
)
"""A compact JWS — three base64url segments, the header starting ``eyJ``.

The signature segment may be empty, which is what an ``alg: none`` token
looks like.
"""

DEFAULT_REDACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    JWT_PATTERN,
    BEARER_PATTERN,
    EMAIL_PATTERN,
)
"""The patterns redacted from free text by default, in application order."""

_MAX_DEPTH: int = 8
"""How deep :meth:`RedactionPolicy.redact_value` walks nested containers.

Below this depth the remaining value is rendered with ``str`` and passed
through the text patterns, so a self-referencing structure terminates and
nothing past the limit escapes the patterns.
"""

_RECORD_FIELDS: frozenset[str] = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None))
) | {"message", "asctime", "taskName"}
"""Attribute names every ``LogRecord`` carries, i.e. not ``extra=`` keys."""

_PLAIN_TYPES: tuple[type, ...] = (bool, int, float, type(None))
"""Scalars that cannot carry text and are kept as they are."""

_EXCEPTION_FORMATTER: logging.Formatter = logging.Formatter()
"""Renders ``exc_info`` the way the stdlib formatter would."""

_KEY_VALUE_PATTERN: re.Pattern[str] = re.compile(
    r"(?<![\w-])(?P<key>[\w-]++)"
    r"(?P<sep>[\"']?\s*[=:]\s*[\"']?)"
    r"(?P<value>(?:(?:basic|bearer|digest|token)\s+)?[^\s\"',;&}\]]+)",
    re.IGNORECASE,
)
"""Any ``key=value`` / ``key: value`` pair in free text.

The key is checked against the policy in Python
(:meth:`RedactionPolicy.is_sensitive_key`, cached) rather than folded into
the pattern as an alternation: measured on a typical access-log line, the
alternation form cost about 6.6 us per call against about 3.2 us for this
one. The possessive key quantifier and the lookbehind keep the engine from
retrying every suffix of every word.
"""

_KEY_CACHE_LIMIT: int = 4096
"""How many distinct keys :meth:`RedactionPolicy.is_sensitive_key` remembers.

``extra=`` names repeat on every record and come from code, so a small
cache answers almost every lookup; the limit bounds memory when keys come
from data (the keys of a payload dict logged whole).
"""


def _normalize_key(key: str) -> str:
    """Normalize a key for sensitive-fragment matching.

    Args:
        key (str): The raw key (``extra=`` name, dict key, header name).

    Returns:
        str: The key in lower case with ``-`` replaced by ``_``.
    """
    return key.lower().replace("-", "_")


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    """What :class:`RedactionFilter` hides from a log record.

    Two mechanisms run together:

    * **Sensitive keys** — any mapping key (an ``extra=`` field, a key of a
      dict passed as a log argument or nested in one) whose normalized name
      contains one of :attr:`keys` / :attr:`extra_keys` has its whole value
      replaced. The same keys also drive a ``key=value`` / ``key: value``
      pattern over free text, so ``?token=abc`` in a query string and
      ``{'password': 'x'}`` quoted in an exception message are caught.
    * **Text patterns** — every string (message, traceback, stack, string
      values) has each of :attr:`patterns` / :attr:`extra_patterns`
      replaced. The defaults cover e-mail addresses, ``Bearer`` credentials
      and JWTs.

    Use :attr:`extra_keys` / :attr:`extra_patterns` to add domain terms
    without restating the defaults; set :attr:`keys` / :attr:`patterns`
    only to replace them.

    Attributes:
        keys (Collection[str]): Base sensitive key fragments. Defaults to
            :data:`DEFAULT_REDACT_KEYS`.
        extra_keys (Collection[str]): Additional key fragments, added to
            :attr:`keys`.
        patterns (Sequence[re.Pattern[str] | str]): Base text patterns.
            Defaults to :data:`DEFAULT_REDACT_PATTERNS`. Strings are
            compiled.
        extra_patterns (Sequence[re.Pattern[str] | str]): Additional text
            patterns, applied after :attr:`patterns`.
        replacement (str): The text written in place of each redacted
            value. Defaults to :data:`REDACTED`.
    """

    keys: Collection[str] = DEFAULT_REDACT_KEYS
    extra_keys: Collection[str] = ()
    patterns: Sequence[re.Pattern[str] | str] = DEFAULT_REDACT_PATTERNS
    extra_patterns: Sequence[re.Pattern[str] | str] = ()
    replacement: str = REDACTED
    _fragments: tuple[str, ...] = field(init=False, repr=False, compare=False)
    _compiled: tuple[re.Pattern[str], ...] = field(
        init=False, repr=False, compare=False
    )
    _key_cache: dict[str, bool] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Normalize the key fragments and compile every pattern once.

        Compiling here keeps the per-record cost to matching only; the
        filter runs on every record a handler admits.
        """
        fragments = tuple(
            sorted({_normalize_key(key) for key in (*self.keys, *self.extra_keys)})
        )
        compiled = tuple(
            pattern if isinstance(pattern, re.Pattern) else re.compile(pattern)
            for pattern in (*self.patterns, *self.extra_patterns)
        )
        object.__setattr__(self, "_fragments", fragments)
        object.__setattr__(self, "_compiled", compiled)
        object.__setattr__(self, "_key_cache", {})

    def is_sensitive_key(self, key: str) -> bool:
        """Return whether a key's value must be redacted whole.

        Args:
            key (str): The key to test. Matched by substring after
                normalizing to lower case with ``-`` turned into ``_``.

        Returns:
            bool: ``True`` when the key contains a sensitive fragment.
        """
        cached = self._key_cache.get(key)
        if cached is not None:
            return cached
        normalized = _normalize_key(key)
        sensitive = any(fragment in normalized for fragment in self._fragments)
        if len(self._key_cache) < _KEY_CACHE_LIMIT:
            self._key_cache[key] = sensitive
        return sensitive

    def _replace_pair(self, match: re.Match[str]) -> str:
        """Redact the value of a ``key=value`` match when the key is sensitive.

        Args:
            match (re.Match[str]): A match of :data:`_KEY_VALUE_PATTERN`.

        Returns:
            str: The pair with its value replaced, or the match unchanged
            when the key is not sensitive.
        """
        if self.is_sensitive_key(match["key"]):
            return match["key"] + match["sep"] + self.replacement
        return match[0]

    def redact_text(self, text: str) -> str:
        """Redact sensitive ``key=value`` pairs and pattern matches.

        Args:
            text (str): Any free text — a message, a traceback, a stack.

        Returns:
            str: The text with every match replaced by
            :attr:`replacement`. The key and separator of a ``key=value``
            pair are kept, so the line still says *what* was hidden.
        """
        if self._fragments and ("=" in text or ":" in text):
            text = _KEY_VALUE_PATTERN.sub(self._replace_pair, text)
        for pattern in self._compiled:
            text = pattern.sub(self.replacement, text)
        return text

    def redact_value(self, value: Any) -> Any:
        """Redact a value of any shape, recursing into containers.

        Mappings have sensitive keys replaced and the remaining values
        redacted; lists, tuples and sets are redacted item by item (and
        come back as lists); strings go through :meth:`redact_text`;
        ``bool``/``int``/``float``/``None`` are kept. Anything else is
        rendered with ``str`` first — which is what
        :class:`~tempest_fastapi_sdk.JSONFormatter` would write for it
        anyway — so an object whose ``repr`` quotes an e-mail does not slip
        past.

        Args:
            value (Any): The value to redact.

        Returns:
            Any: A redacted copy. The input is never mutated.
        """
        return self._redact(value, 0)

    def _redact(self, value: Any, depth: int) -> Any:
        """Redact ``value`` at a given nesting depth.

        Args:
            value (Any): The value to redact.
            depth (int): How many containers deep ``value`` sits; past
                :data:`_MAX_DEPTH` the value is redacted as text.

        Returns:
            Any: A redacted copy of ``value``.
        """
        if isinstance(value, _PLAIN_TYPES):
            return value
        if isinstance(value, str):
            return self.redact_text(value)
        if depth >= _MAX_DEPTH:
            return self.redact_text(str(value))
        if isinstance(value, Mapping):
            return {
                key: (
                    self.replacement
                    if self.is_sensitive_key(str(key))
                    else self._redact(item, depth + 1)
                )
                for key, item in value.items()
            }
        if isinstance(value, list | tuple | set | frozenset):
            return [self._redact(item, depth + 1) for item in value]
        return self.redact_text(str(value))


class RedactionFilter(logging.Filter):
    """Redact a log record in place before any formatter renders it.

    Attach it to a **handler**, not a logger: a logger's filters do not run
    for records propagated from its children, a handler's do.
    :func:`tempest_fastapi_sdk.configure_logging` does that for every
    handler it installs when ``redact`` is set; attach it yourself to any
    handler you add on top (Sentry, a syslog sink).

    What it rewrites:

    * ``msg`` / ``args`` — the message is rendered, redacted, and stored
      back as ``msg`` with ``args`` cleared, so the original arguments do
      not reach the formatter. A mapping argument has its sensitive keys
      replaced before rendering.
    * ``exc_text`` — the traceback is rendered from ``exc_info`` (unless a
      previous step already did) and redacted. Both the stdlib formatter
      and :class:`~tempest_fastapi_sdk.JSONFormatter` prefer ``exc_text``
      over re-rendering ``exc_info``.
    * ``stack_info`` — redacted as text.
    * every ``extra=`` field — sensitive keys replaced whole, other values
      through :meth:`RedactionPolicy.redact_value`.

    The record is marked once redacted by a given policy, so the several
    handlers :func:`~tempest_fastapi_sdk.configure_logging` installs pay for
    it once per record. The filter never drops a record.

    Attributes:
        policy (RedactionPolicy): What to redact.
    """

    _MARKER: str = "_tempest_redaction_policy"

    def __init__(self, policy: RedactionPolicy | None = None) -> None:
        """Initialize the filter.

        Args:
            policy (RedactionPolicy | None): What to redact. ``None`` uses
                ``RedactionPolicy()`` — the default keys and patterns.
        """
        super().__init__()
        self.policy: RedactionPolicy = policy or RedactionPolicy()

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact ``record`` in place.

        A message whose arguments do not match its format string is not
        rendered here (that error belongs to the handler, which reports it
        through ``handleError``); its template and arguments are redacted
        separately instead, so the error report does not leak them either.

        Args:
            record (logging.LogRecord): The record to redact.

        Returns:
            bool: Always ``True`` — redaction never suppresses a record.
        """
        policy = self.policy
        if record.__dict__.get(self._MARKER) is policy:
            return True

        args: Any = record.args
        if isinstance(args, Mapping):
            record.args = policy.redact_value(args)
        try:
            message = record.getMessage()
        except Exception:
            record.msg = policy.redact_text(str(record.msg))
            if record.args:
                redacted_args = policy.redact_value(record.args)
                record.args = (
                    redacted_args
                    if isinstance(redacted_args, dict)
                    else tuple(redacted_args)
                )
        else:
            record.msg = policy.redact_text(message)
            record.args = None

        if record.exc_info and not record.exc_text:
            record.exc_text = _EXCEPTION_FORMATTER.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = policy.redact_text(record.exc_text)
        if record.stack_info:
            record.stack_info = policy.redact_text(record.stack_info)

        for key, value in list(record.__dict__.items()):
            if key in _RECORD_FIELDS or key.startswith("_"):
                continue
            record.__dict__[key] = (
                policy.replacement
                if policy.is_sensitive_key(key)
                else policy.redact_value(value)
            )

        record.__dict__[self._MARKER] = policy
        return True


__all__: list[str] = [
    "BEARER_PATTERN",
    "DEFAULT_REDACT_KEYS",
    "DEFAULT_REDACT_PATTERNS",
    "EMAIL_PATTERN",
    "JWT_PATTERN",
    "REDACTED",
    "RedactionFilter",
    "RedactionPolicy",
]
