"""Two-step CSV import of the admin: a preview that writes nothing, then a commit.

``POST {prefix}/m/{slug}/import`` reads the uploaded CSV, validates every
row and rehearses the inserts inside a transaction that is always rolled
back, then renders what would happen: the rows that enter and, per row, the
field errors. The CSV travels to the confirmation inside a hidden field
signed with the router's ``secret_key`` and bound to the model and the
signed-in principal, so what is committed is byte for byte what was
previewed. ``POST {prefix}/m/{slug}/import/confirm`` re-runs the same rows
for real in one transaction and commits only if every row went in.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy.exc import IntegrityError

from tempest_fastapi_sdk.admin.forms import parse_submission
from tempest_fastapi_sdk.admin.i18n import admin_text
from tempest_fastapi_sdk.db.transaction import savepoint, transaction
from tempest_fastapi_sdk.exceptions import AppException

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.admin.config import AdminModel

ADMIN_IMPORT_MAX_BYTES: Final[int] = 512 * 1024
"""Largest CSV, in bytes, the import page accepts.

The preview carries the whole file to the confirmation as one signed hidden
field, so the cap also bounds that request. Measured over six files of
512 KiB (random hex, random printable ASCII, random Latin-1, random CJK,
random emoji, one-character lines), the signed field came to at most
571 099 bytes — random printable ASCII, which compresses least — under the
1 MiB Starlette allows one multipart field (``max_part_size``).
"""

ADMIN_IMPORT_PREVIEW_MAX_AGE_SECONDS: Final[int] = 3600
"""How long a preview can be confirmed before the file must be sent again."""

ADMIN_IMPORT_PREVIEW_ROWS: Final[int] = 20
"""Rows of the file shown in the preview table; the counts cover all of them."""

_IMPORT_SALT: Final[str] = "tempest-admin-import"


class ImportPayloadError(Exception):
    """The signed import payload is forged, expired or for another import."""


@dataclass
class ImportOutcome:
    """What one pass over the CSV found.

    Attributes:
        total (int): Data rows read (the header excluded).
        accepted (int): Rows that validated and inserted.
        sample (list[dict[str, str]]): The first accepted rows, as read,
            for the preview table.
        errors (list[dict[str, Any]]): ``{"row": n, "errors": {field:
            message}}`` per refused row; ``n`` counts the header as row 1,
            like a spreadsheet, and the field is ``""`` for a row-level
            error.
        committed (bool): Whether the rows were committed.
    """

    total: int = 0
    accepted: int = 0
    sample: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    committed: bool = False


class _RehearsalError(Exception):
    """Raised at the end of a pass that must not commit, to roll it back."""


def sign_import_payload(
    secret_key: str, *, slug: str, principal_id: str, text: str
) -> str:
    """Sign the CSV text for the confirmation step.

    Args:
        secret_key (str): The router's ``secret_key``.
        slug (str): The admin the import targets.
        principal_id (str): The operator who previewed it.
        text (str): The decoded CSV.

    Returns:
        str: A URL-safe token, compressed when that makes it shorter.
    """
    from tempest_fastapi_sdk.admin.session import _require_itsdangerous

    itsdangerous = _require_itsdangerous()
    serializer = itsdangerous.URLSafeTimedSerializer(secret_key, salt=_IMPORT_SALT)
    token: str = serializer.dumps(
        {"slug": slug, "principal": principal_id, "csv": text}
    )
    return token


def read_import_payload(
    secret_key: str, token: str, *, slug: str, principal_id: str
) -> str:
    """Verify a confirmation token and return the CSV it carries.

    Args:
        secret_key (str): The router's ``secret_key``.
        token (str): The posted hidden field.
        slug (str): The admin the confirmation was posted to.
        principal_id (str): The operator confirming.

    Returns:
        str: The CSV text exactly as previewed.

    Raises:
        ImportPayloadError: When the signature does not verify, the token
            is older than :data:`ADMIN_IMPORT_PREVIEW_MAX_AGE_SECONDS`, or it
            was issued for another model or another operator.
    """
    from tempest_fastapi_sdk.admin.session import _require_itsdangerous

    itsdangerous = _require_itsdangerous()
    serializer = itsdangerous.URLSafeTimedSerializer(secret_key, salt=_IMPORT_SALT)
    try:
        data = serializer.loads(token, max_age=ADMIN_IMPORT_PREVIEW_MAX_AGE_SECONDS)
    except itsdangerous.BadData as exc:
        raise ImportPayloadError from exc
    if (
        not isinstance(data, dict)
        or data.get("slug") != slug
        or data.get("principal") != principal_id
        or not isinstance(data.get("csv"), str)
    ):
        raise ImportPayloadError
    text: str = data["csv"]
    return text


async def run_import(
    admin: AdminModel[Any],
    db_session: AsyncSession,
    text: str,
    *,
    scope: ColumnElement[bool] | None,
    prepare: Callable[[Any], None],
    commit: bool,
    locale: str,
) -> ImportOutcome:
    """Validate and insert every CSV row inside one transaction.

    Each row is parsed with the create form's rules, then inserted inside
    its own savepoint so a refused row leaves the others usable; a row the
    ``row_scope`` does not reach is refused like a validation error. The
    transaction is rolled back at the end unless ``commit`` is set **and**
    no row was refused, so a commit is all or nothing.

    The rehearsal (``commit=False``) flushes the row through the session
    after the repository's ``_prepare_insert`` hook, without the
    repository's ``add``: no ``PRE_SAVE`` / ``POST_SAVE`` signal fires for a
    preview. The commit goes through ``repository.add``, signals included.

    Args:
        admin (AdminModel[Any]): The admin being imported into.
        db_session (AsyncSession): The request session.
        text (str): The decoded CSV, header first.
        scope (ColumnElement[bool] | None): The principal's ``row_scope``.
        prepare (Callable[[Any], None]): Stamps a new instance before it is
            inserted (audit columns).
        commit (bool): Whether to keep the rows when every one went in.
        locale (str): Language of the row errors.

    Returns:
        ImportOutcome: Counts, the preview sample and the row errors.

    Raises:
        csv.Error: When the file is not parseable CSV.
    """
    outcome = ImportOutcome()
    columns = admin.editable_field_names(creating=True)
    repository = admin.build_repository(db_session)
    reader = csv.DictReader(io.StringIO(text))
    try:
        async with transaction(db_session):
            for line_no, row in enumerate(reader, start=2):
                outcome.total += 1
                data, errors = parse_submission(
                    admin, row, creating=True, locale=locale
                )
                if errors:
                    outcome.errors.append({"row": line_no, "errors": errors})
                    continue
                instance = admin.model(**data)
                prepare(instance)
                message = await _insert(
                    repository,
                    db_session,
                    instance,
                    scope,
                    commit=commit,
                    locale=locale,
                )
                if message is not None:
                    outcome.errors.append({"row": line_no, "errors": {"": message}})
                    continue
                outcome.accepted += 1
                if len(outcome.sample) < ADMIN_IMPORT_PREVIEW_ROWS:
                    outcome.sample.append(
                        {name: str(row.get(name) or "") for name in columns}
                    )
            if not commit or outcome.errors:
                raise _RehearsalError
    except _RehearsalError:
        return outcome
    outcome.committed = True
    return outcome


async def _insert(
    repository: Any,
    db_session: AsyncSession,
    instance: Any,
    scope: ColumnElement[bool] | None,
    *,
    commit: bool,
    locale: str,
) -> str | None:
    """Insert one row inside a savepoint and check it against the scope.

    Args:
        repository (Any): The admin's repository.
        db_session (AsyncSession): The request session.
        instance (Any): The new row.
        scope (ColumnElement[bool] | None): The principal's ``row_scope``.
        commit (bool): ``True`` inserts through ``repository.add``;
            ``False`` flushes through the session (no signals).
        locale (str): Language of the returned message.

    Returns:
        str | None: Why the row was refused, or ``None`` when it went in.
    """
    try:
        async with savepoint(db_session):
            if commit:
                await repository.add(instance)
            else:
                repository._prepare_insert(instance)
                db_session.add(instance)
                await db_session.flush()
            if scope is not None and not await repository.count(
                {"id": instance.id}, where=scope
            ):
                raise _OutsideScopeError
    except _OutsideScopeError:
        return admin_text(locale, "error.out_of_scope")
    except AppException as exc:
        return exc.message
    except IntegrityError:
        return admin_text(locale, "import.error.conflict")
    return None


class _OutsideScopeError(Exception):
    """The rehearsed row falls outside the principal's scope."""


__all__: list[str] = [
    "ADMIN_IMPORT_MAX_BYTES",
    "ADMIN_IMPORT_PREVIEW_MAX_AGE_SECONDS",
    "ADMIN_IMPORT_PREVIEW_ROWS",
    "ImportOutcome",
    "ImportPayloadError",
    "read_import_payload",
    "run_import",
    "sign_import_payload",
]
