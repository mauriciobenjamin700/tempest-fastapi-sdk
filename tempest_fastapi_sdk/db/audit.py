"""Per-entity audit trail: who changed what, when, with a before/after diff.

:class:`~tempest_fastapi_sdk.db.mixins.AuditMixin` records *who* last
touched a row (``created_by`` / ``updated_by``) and ``BaseModel`` records
*when* (``created_at`` / ``updated_at``). Neither keeps the **history**
of changes. This module adds an append-only audit log: one row per
create / update / delete, capturing the actor, the action and a
before/after diff of the changed columns.

The log row is written in the **same transaction** as the change (reuse
the outbox machinery — :meth:`BaseRepository.add_audited` /
:meth:`update_audited` add the audit row and the business row and commit
them together), so an audit entry can never reference a change that was
rolled back.

Pieces:

* :class:`AuditAction` — the ``create`` / ``update`` / ``delete`` enum.
* :class:`BaseAuditLogModel` — the abstract audit table; the consuming
  project subclasses it and picks ``__tablename__`` (``audit_log`` by
  convention), like :class:`~tempest_fastapi_sdk.db.outbox.BaseOutboxModel`.
* :func:`snapshot_model` / :func:`diff_snapshots` — turn a model into a
  JSON-able dict and diff two snapshots.
* :func:`audit_redacted_columns` / :func:`redact_snapshot` — what the
  entry factories apply before a snapshot is persisted, so a column the
  model lists in ``__audit_redact__`` is recorded as changed, never by
  value.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any, Final
from uuid import UUID, uuid4

from sqlalchemy import JSON, String
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db.model import BaseModel


class AuditAction(StrEnum):
    """The kind of mutation an audit entry records."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


def _jsonable(value: Any) -> Any:
    """Return a JSON-serializable representation of a column value.

    Args:
        value (Any): A column value (UUID, datetime, Decimal, ...).

    Returns:
        Any: ``value`` coerced to something ``json``/``JSON`` accepts —
        ``UUID``/``Decimal`` become ``str``, ``datetime``/``date`` use
        ``isoformat()``, everything else is returned unchanged.
    """
    if isinstance(value, UUID | Decimal):
        return str(value)
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return value


def snapshot_model(instance: BaseModel) -> dict[str, Any]:
    """Capture a model's column values as a JSON-able dict.

    Only mapped columns are read (no relationships), and the instance is
    not refreshed — pass an instance whose attributes are already loaded.

    Args:
        instance (BaseModel): The mapped instance to snapshot.

    Returns:
        dict[str, Any]: ``{column_name: jsonable_value}`` for every
        mapped column.
    """
    mapper = inspect(type(instance))
    return {
        column.key: _jsonable(getattr(instance, column.key))
        for column in mapper.columns
    }


AUDIT_REDACTED: Final[str] = "[redacted]"
"""What an audit entry stores in place of a redacted column's value.

A constant rather than a digest on purpose: a short hash of a
low-entropy secret is brute-forced offline, and a full one still says
whether two rows share a value. ``None`` stays ``None``, so the entry
still tells "not set" from "set".
"""

DEFAULT_AUDIT_REDACT: Final[frozenset[str]] = frozenset({"hashed_password"})
"""Columns redacted on every model that has them, declared or not."""


def audit_redacted_columns(model: type[BaseModel]) -> frozenset[str]:
    """Return the columns of ``model`` whose values the audit never stores.

    ``model.__audit_redact__`` plus :data:`DEFAULT_AUDIT_REDACT`, the
    latter only where the model has the column.

    Args:
        model (type[BaseModel]): The audited model class.

    Returns:
        frozenset[str]: The redacted column keys.

    Raises:
        ValueError: When ``__audit_redact__`` names a column the model
            does not map — a typo there would store the secret silently.
    """
    columns = {column.key for column in inspect(model).columns}
    declared = frozenset(model.__audit_redact__)
    unknown = sorted(declared - columns)
    if unknown:
        raise ValueError(
            f"{model.__name__}.__audit_redact__ names columns the model does "
            f"not have: {', '.join(unknown)}",
        )
    return declared | (DEFAULT_AUDIT_REDACT & columns)


def redact_snapshot(
    model: type[BaseModel],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Return ``snapshot`` with every redacted column's value replaced.

    Args:
        model (type[BaseModel]): The model the snapshot was taken from.
        snapshot (dict[str, Any]): A :func:`snapshot_model` result.

    Returns:
        dict[str, Any]: A new dict; a redacted column holding a value
        reads :data:`AUDIT_REDACTED`, one holding ``None`` stays ``None``.
    """
    redacted = audit_redacted_columns(model)
    return {
        key: AUDIT_REDACTED if key in redacted and value is not None else value
        for key, value in snapshot.items()
    }


def diff_snapshots(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return the changed fields between two snapshots.

    Args:
        before (dict[str, Any]): The pre-change snapshot.
        after (dict[str, Any]): The post-change snapshot.

    Returns:
        dict[str, dict[str, Any]]: ``{field: {"before": x, "after": y}}``
        for every field whose value differs (the union of both key
        sets; a field absent on one side reads as ``None``).
    """
    changed: dict[str, dict[str, Any]] = {}
    for key in before.keys() | after.keys():
        old = before.get(key)
        new = after.get(key)
        if old != new:
            changed[key] = {"before": old, "after": new}
    return changed


class BaseAuditLogModel(BaseModel):
    """Abstract append-only audit-log table — one row per mutation.

    The consuming project subclasses this and picks a ``__tablename__``
    (``audit_log`` by convention), mirroring
    :class:`~tempest_fastapi_sdk.db.outbox.BaseOutboxModel`. Inherits the
    canonical four columns from
    :class:`~tempest_fastapi_sdk.db.model.BaseModel`.

    Attributes:
        entity (str): The changed model's name (``self.model.__name__``).
            Indexed for per-entity history queries.
        entity_id (str): The changed row's id, stored as text so any key
            type fits. Indexed.
        action (str): One of :class:`AuditAction`.
        actor (str | None): Who performed the change (user id, e-mail,
            ``"system"``, ...). Indexed; ``None`` for anonymous/system.
        changes (dict[str, Any]): The diff. For ``create`` it is
            ``{"after": {...}}``; for ``delete`` ``{"before": {...}}``;
            for ``update`` ``{field: {"before": x, "after": y}}``.
        context (dict[str, Any] | None): Optional extra metadata
            (request id, ip, reason, ...).
    """

    __abstract__ = True

    entity: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        doc="Changed model name (e.g. 'UserModel').",
    )
    entity_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        doc="Changed row id, stored as text.",
    )
    action: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        index=True,
        doc="Mutation kind (AuditAction value).",
    )
    actor: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default=None,
        index=True,
        doc="Who performed the change, or NULL for system/anonymous.",
    )
    changes: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        doc="Before/after diff of the change, serialized as JSON.",
    )
    context: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        default=None,
        doc="Optional extra metadata (request id, ip, reason, ...).",
    )

    @classmethod
    def new_entry(
        cls,
        *,
        entity: str,
        entity_id: str,
        action: AuditAction,
        changes: dict[str, Any],
        actor: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> BaseAuditLogModel:
        """Build an audit row (not yet added to a session).

        Args:
            entity (str): The changed model name.
            entity_id (str): The changed row id (as text).
            action (AuditAction): The mutation kind.
            changes (dict[str, Any]): The before/after diff.
            actor (str | None): Who performed the change.
            context (dict[str, Any] | None): Extra metadata.

        Returns:
            BaseAuditLogModel: A new instance ready to add to a session.
        """
        return cls(
            id=uuid4(),
            entity=entity,
            entity_id=entity_id,
            action=action.value,
            changes=changes,
            actor=actor,
            context=context,
        )

    @classmethod
    def for_create(
        cls,
        instance: BaseModel,
        *,
        actor: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> BaseAuditLogModel:
        """Build a ``create`` entry snapshotting the new row.

        Args:
            instance (BaseModel): The created instance.
            actor (str | None): Who created it.
            context (dict[str, Any] | None): Extra metadata.

        Returns:
            BaseAuditLogModel: The audit row with ``{"after": snapshot}``.
        """
        return cls.new_entry(
            entity=type(instance).__name__,
            entity_id=str(getattr(instance, "id", "")),
            action=AuditAction.CREATE,
            changes={
                "after": redact_snapshot(type(instance), snapshot_model(instance))
            },
            actor=actor,
            context=context,
        )

    @classmethod
    def for_update(
        cls,
        instance: BaseModel,
        before: dict[str, Any],
        *,
        actor: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> BaseAuditLogModel:
        """Build an ``update`` entry diffing ``before`` against the row now.

        The diff is computed on the raw snapshots and redacted afterwards,
        so rotating a secret listed in ``__audit_redact__`` still yields
        ``{"before": "[redacted]", "after": "[redacted]"}`` — the change
        is recorded, the values are not.

        Args:
            instance (BaseModel): The instance after mutation.
            before (dict[str, Any]): A snapshot taken *before* the change
                (via :func:`snapshot_model`).
            actor (str | None): Who updated it.
            context (dict[str, Any] | None): Extra metadata.

        Returns:
            BaseAuditLogModel: The audit row with the changed-field diff.
        """
        diff = diff_snapshots(before, snapshot_model(instance))
        redacted = audit_redacted_columns(type(instance))
        changes = {
            key: (
                {
                    side: AUDIT_REDACTED if value is not None else None
                    for side, value in delta.items()
                }
                if key in redacted
                else delta
            )
            for key, delta in diff.items()
        }
        return cls.new_entry(
            entity=type(instance).__name__,
            entity_id=str(getattr(instance, "id", "")),
            action=AuditAction.UPDATE,
            changes=changes,
            actor=actor,
            context=context,
        )

    @classmethod
    def for_delete(
        cls,
        instance: BaseModel,
        *,
        actor: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> BaseAuditLogModel:
        """Build a ``delete`` entry snapshotting the row being removed.

        Args:
            instance (BaseModel): The instance about to be deleted.
            actor (str | None): Who deleted it.
            context (dict[str, Any] | None): Extra metadata.

        Returns:
            BaseAuditLogModel: The audit row with ``{"before": snapshot}``.
        """
        return cls.new_entry(
            entity=type(instance).__name__,
            entity_id=str(getattr(instance, "id", "")),
            action=AuditAction.DELETE,
            changes={
                "before": redact_snapshot(type(instance), snapshot_model(instance))
            },
            actor=actor,
            context=context,
        )


__all__: list[str] = [
    "AUDIT_REDACTED",
    "DEFAULT_AUDIT_REDACT",
    "AuditAction",
    "BaseAuditLogModel",
    "audit_redacted_columns",
    "diff_snapshots",
    "redact_snapshot",
    "snapshot_model",
]
