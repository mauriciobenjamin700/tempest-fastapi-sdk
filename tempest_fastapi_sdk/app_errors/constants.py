"""Column limits and the truncation marker for app error reports.

Each value is a decision about what a crashed client can still deliver,
not an arbitrary size.
"""

from typing import Final

APP_ERROR_CODE_MAX_LENGTH: Final[int] = 120
"""Maximum persisted length of an error code.

Codes are short, stable identifiers (``AUTH_TOKEN_EXPIRED``,
``PLAN_ACTIVATION_FAILED``): 120 characters cover any namespace an app may
use and still leave the column indexable.
"""

APP_ERROR_MESSAGE_MAX_LENGTH: Final[int] = 4000
"""Maximum persisted length of an error message.

Generous enough to hold a mobile stack trace, which is what makes the
report useful, and small enough that a client stuck in an error loop
cannot fill the table with a single row.
"""

APP_ERROR_TEXT_FIELD_MAX_LENGTH: Final[int] = 200
"""Maximum length of the descriptive device fields.

Covers ``os_version``, ``app_version``, ``device_model`` and ``device_id``.
None of them is genuinely free text — they are identifiers emitted by the
operating system or by the build.
"""

APP_ERROR_DEVICE_TEXT_FIELDS: Final[tuple[str, ...]] = (
    "os_version",
    "app_version",
    "device_model",
    "device_id",
)
"""Device fields subject to ``APP_ERROR_TEXT_FIELD_MAX_LENGTH``.

Listed here rather than derived from the schema because the set encodes a
sanitization rule: not every textual field of a report shares that limit —
``code`` and ``message`` have limits of their own.
"""

APP_ERROR_TRUNCATION_SUFFIX: Final[str] = "…[truncado]"
"""Mark appended to a value cut for exceeding its column limit.

The rule is: **a truncated report beats a lost report**. A payload above
the limit does not become a 422 — the sender is an app that has just
crashed and cannot handle the refusal, so the error would simply vanish.
The suffix exists so whoever reads the listing knows content is missing.

The marker itself stays in pt-BR: it is stored data the team reads in the
listing, not documentation.
"""

__all__: list[str] = [
    "APP_ERROR_CODE_MAX_LENGTH",
    "APP_ERROR_DEVICE_TEXT_FIELDS",
    "APP_ERROR_MESSAGE_MAX_LENGTH",
    "APP_ERROR_TEXT_FIELD_MAX_LENGTH",
    "APP_ERROR_TRUNCATION_SUFFIX",
]
