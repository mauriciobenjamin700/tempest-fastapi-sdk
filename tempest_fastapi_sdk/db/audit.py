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

* :class:`AuditAction` — the ``create`` / ``update`` / ``delete`` /
  ``event`` enum.
* :class:`BaseAuditLogModel` — the abstract audit table; the consuming
  project subclasses it and picks ``__tablename__`` (``audit_log`` by
  convention), like :class:`~tempest_fastapi_sdk.db.outbox.BaseOutboxModel`.
* :class:`AuditRequestMixin` — opt-in ``event`` / ``ip`` / ``user_agent``
  columns, so a domain event name and the request origin are queryable
  columns instead of keys buried in ``context``.
* :class:`AuditRequestContext` — the ``ip`` / ``user_agent`` pair, read
  from a request with :meth:`AuditRequestContext.from_request`.
* :func:`snapshot_model` / :func:`diff_snapshots` — turn a model into a
  JSON-able dict and diff two snapshots.
* :func:`audit_redacted_columns` / :func:`redact_snapshot` — what the
  entry factories apply before a snapshot is persisted, so a column the
  model lists in ``__audit_redact__`` is recorded as changed, never by
  value.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID, uuid4

from sqlalchemy import JSON, String
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db.model import BaseModel
from tempest_fastapi_sdk.utils.client_ip import get_client_ip

if TYPE_CHECKING:
    from starlette.requests import Request


class AuditAction(StrEnum):
    """The kind of change an audit entry records.

    ``EVENT`` marks a domain event recorded without a row mutation
    (``"consent.granted"``, ``"export.requested"``) — see
    :meth:`BaseAuditLogModel.for_event`.
    """

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    EVENT = "event"


AUDIT_EVENT_MAX_LENGTH: Final[int] = 128
"""Width of :attr:`AuditRequestMixin.event`; longer names are refused."""

AUDIT_IP_MAX_LENGTH: Final[int] = 64
"""Width of :attr:`AuditRequestMixin.ip`; longer values are refused."""

AUDIT_USER_AGENT_MAX_LENGTH: Final[int] = 512
"""Width of :attr:`AuditRequestMixin.user_agent`.

A longer ``User-Agent`` is truncated to this length rather than refused:
the header is client-controlled, and an oversized one must not turn the
audited business write into a database error.
"""


class AuditRequestMixin:
    """Opt-in ``event`` / ``ip`` / ``user_agent`` columns for an audit table.

    Mixed into a :class:`BaseAuditLogModel` subclass, it lets
    ``new_entry``, the ``for_*`` factories and the repository's
    ``*_audited`` / ``record_event`` methods store the domain event name
    and the request origin in their own columns. It is a mixin, not part
    of :class:`BaseAuditLogModel`, so an audit table created before these
    columns existed keeps working without a migration; adding the mixin to
    an existing table is a schema change that needs one.

    Attributes:
        event (str | None): Domain event name (``"consent.granted"``).
            Indexed, so "every consent grant" is an index lookup.
        ip (str | None): Client IP of the request that caused the change.
        user_agent (str | None): The request's ``User-Agent``, truncated
            to :data:`AUDIT_USER_AGENT_MAX_LENGTH`.
    """

    event: Mapped[str | None] = mapped_column(
        String(AUDIT_EVENT_MAX_LENGTH),
        nullable=True,
        default=None,
        index=True,
        doc="Domain event name (e.g. 'consent.granted'), or NULL.",
    )
    ip: Mapped[str | None] = mapped_column(
        String(AUDIT_IP_MAX_LENGTH),
        nullable=True,
        default=None,
        doc="Client IP of the request behind the change, or NULL.",
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(AUDIT_USER_AGENT_MAX_LENGTH),
        nullable=True,
        default=None,
        doc="User-Agent of the request behind the change, or NULL.",
    )


@dataclass(frozen=True, slots=True)
class AuditRequestContext:
    """The request origin an audit entry records: client IP and user agent.

    Built once per request with :meth:`from_request` and handed to the
    audited repository methods as ``request_context=``.

    Attributes:
        ip (str | None): The client IP, or ``None`` when unknown.
        user_agent (str | None): The ``User-Agent`` header, or ``None``.
    """

    ip: str | None = None
    user_agent: str | None = None

    @classmethod
    def from_request(
        cls,
        request: Request,
        *,
        trusted_ip_header: str | None,
    ) -> AuditRequestContext:
        """Read the client IP and ``User-Agent`` from a request.

        ``trusted_ip_header`` has no default on purpose: whether a proxy
        header is trustworthy is a deployment fact the caller has to state
        (see :func:`~tempest_fastapi_sdk.utils.get_client_ip`). Pass the
        single header your edge overwrites (``"x-real-ip"``), or ``None``
        to use the transport peer.

        A resolved value that is not an IP address (a misconfigured header,
        or no peer at all) is stored as ``None`` instead of as the raw
        string, so the column only ever holds addresses.

        Args:
            request (Request): The inbound Starlette/FastAPI request.
            trusted_ip_header (str | None): The edge-set header to trust,
                or ``None`` for the transport peer only.

        Returns:
            AuditRequestContext: The resolved origin.
        """
        raw_ip = get_client_ip(request, trusted_header=trusted_ip_header)
        try:
            ip: str | None = str(ipaddress.ip_address(raw_ip))
        except ValueError:
            ip = None
        user_agent = request.headers.get("user-agent") or None
        return cls(ip=ip, user_agent=user_agent)


def _origin_columns(
    *,
    event: str | None,
    ip: str | None,
    user_agent: str | None,
    actor_id: UUID | None,
    request_context: AuditRequestContext | None,
) -> dict[str, Any]:
    """Return the optional audit columns that carry a value.

    Args:
        event (str | None): The domain event name.
        ip (str | None): The client IP.
        user_agent (str | None): The user agent.
        actor_id (UUID | None): The author's id, for an ``actor_id`` FK.
        request_context (AuditRequestContext | None): ``ip`` and
            ``user_agent`` bundled; mutually exclusive with both.

    Returns:
        dict[str, Any]: ``{column: value}`` for every non-``None`` value,
        with ``user_agent`` truncated to
        :data:`AUDIT_USER_AGENT_MAX_LENGTH`.

    Raises:
        ValueError: When ``request_context`` is combined with ``ip`` or
            ``user_agent``, or when ``event`` / ``ip`` exceed their
            column width.
    """
    if request_context is not None:
        if ip is not None or user_agent is not None:
            raise ValueError(
                "pass either request_context= or ip=/user_agent=, not both",
            )
        ip = request_context.ip
        user_agent = request_context.user_agent
    if event is not None and len(event) > AUDIT_EVENT_MAX_LENGTH:
        raise ValueError(
            f"audit event is {len(event)} characters long; the column holds "
            f"{AUDIT_EVENT_MAX_LENGTH}",
        )
    if ip is not None and len(ip) > AUDIT_IP_MAX_LENGTH:
        raise ValueError(
            f"audit ip is {len(ip)} characters long; the column holds "
            f"{AUDIT_IP_MAX_LENGTH}",
        )
    if user_agent is not None:
        user_agent = user_agent[:AUDIT_USER_AGENT_MAX_LENGTH]
    values: dict[str, Any] = {
        "event": event,
        "ip": ip,
        "user_agent": user_agent,
        "actor_id": actor_id,
    }
    return {key: value for key, value in values.items() if value is not None}


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
            (request id, reason, ...). The event name, client IP and user
            agent get their own columns through :class:`AuditRequestMixin`.
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
        event: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        request_context: AuditRequestContext | None = None,
        actor_id: UUID | None = None,
    ) -> BaseAuditLogModel:
        """Build an audit row (not yet added to a session).

        The origin values (``event``, ``ip``, ``user_agent``, ``actor_id``)
        go to columns of the same name. Each is optional and, when ``None``,
        is not written at all — so an audit table without those columns
        keeps working. A value for a column the table lacks is refused
        rather than dropped, because silently losing an audit fact is
        worse than failing the call.

        **Author FK extension point.** To link entries to a users table
        (and anonymize with ``ON DELETE SET NULL`` when an account is
        erased), declare the column on the subclass —
        ``actor_id: Mapped[UUID | None] = mapped_column(ForeignKey(
        "users.id", ondelete="SET NULL"), nullable=True, index=True)`` —
        and pass ``actor_id=``. No override of this method is needed.

        Args:
            entity (str): The changed model name.
            entity_id (str): The changed row id (as text).
            action (AuditAction): The mutation kind.
            changes (dict[str, Any]): The before/after diff.
            actor (str | None): Who performed the change.
            context (dict[str, Any] | None): Extra metadata.
            event (str | None): Domain event name; needs
                :class:`AuditRequestMixin` on the audit table.
            ip (str | None): Client IP; needs :class:`AuditRequestMixin`.
            user_agent (str | None): User agent; needs
                :class:`AuditRequestMixin`.
            request_context (AuditRequestContext | None): ``ip`` and
                ``user_agent`` together; mutually exclusive with both.
            actor_id (UUID | None): The author's id; needs an ``actor_id``
                column on the audit table.

        Returns:
            BaseAuditLogModel: A new instance ready to add to a session.

        Raises:
            ValueError: When a value is passed for a column the audit table
                does not have, when ``request_context`` is combined with
                ``ip`` / ``user_agent``, or when ``event`` / ``ip`` exceed
                their column width.
        """
        origin = _origin_columns(
            event=event,
            ip=ip,
            user_agent=user_agent,
            actor_id=actor_id,
            request_context=request_context,
        )
        columns = {column.key for column in inspect(cls).columns}
        missing = sorted(origin.keys() - columns)
        if missing:
            raise ValueError(
                f"{cls.__name__} has no column for: {', '.join(missing)}. "
                "Mix AuditRequestMixin in for event/ip/user_agent, or declare "
                "an actor_id column for actor_id.",
            )
        return cls(
            id=uuid4(),
            entity=entity,
            entity_id=entity_id,
            action=action.value,
            changes=changes,
            actor=actor,
            context=context,
            **origin,
        )

    @classmethod
    def for_create(
        cls,
        instance: BaseModel,
        *,
        actor: str | None = None,
        context: dict[str, Any] | None = None,
        event: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        request_context: AuditRequestContext | None = None,
        actor_id: UUID | None = None,
    ) -> BaseAuditLogModel:
        """Build a ``create`` entry snapshotting the new row.

        Args:
            instance (BaseModel): The created instance.
            actor (str | None): Who created it.
            context (dict[str, Any] | None): Extra metadata.
            event (str | None): Domain event name (see :meth:`new_entry`).
            ip (str | None): Client IP.
            user_agent (str | None): User agent.
            request_context (AuditRequestContext | None): ``ip`` and
                ``user_agent`` together.
            actor_id (UUID | None): The author's id.

        Returns:
            BaseAuditLogModel: The audit row with ``{"after": snapshot}``.

        Raises:
            ValueError: See :meth:`new_entry`.
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
            event=event,
            ip=ip,
            user_agent=user_agent,
            request_context=request_context,
            actor_id=actor_id,
        )

    @classmethod
    def for_update(
        cls,
        instance: BaseModel,
        before: dict[str, Any],
        *,
        actor: str | None = None,
        context: dict[str, Any] | None = None,
        event: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        request_context: AuditRequestContext | None = None,
        actor_id: UUID | None = None,
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
            event (str | None): Domain event name (see :meth:`new_entry`).
            ip (str | None): Client IP.
            user_agent (str | None): User agent.
            request_context (AuditRequestContext | None): ``ip`` and
                ``user_agent`` together.
            actor_id (UUID | None): The author's id.

        Returns:
            BaseAuditLogModel: The audit row with the changed-field diff.

        Raises:
            ValueError: See :meth:`new_entry`.
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
            event=event,
            ip=ip,
            user_agent=user_agent,
            request_context=request_context,
            actor_id=actor_id,
        )

    @classmethod
    def for_delete(
        cls,
        instance: BaseModel,
        *,
        actor: str | None = None,
        context: dict[str, Any] | None = None,
        event: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        request_context: AuditRequestContext | None = None,
        actor_id: UUID | None = None,
    ) -> BaseAuditLogModel:
        """Build a ``delete`` entry snapshotting the row being removed.

        Args:
            instance (BaseModel): The instance about to be deleted.
            actor (str | None): Who deleted it.
            context (dict[str, Any] | None): Extra metadata.
            event (str | None): Domain event name (see :meth:`new_entry`).
            ip (str | None): Client IP.
            user_agent (str | None): User agent.
            request_context (AuditRequestContext | None): ``ip`` and
                ``user_agent`` together.
            actor_id (UUID | None): The author's id.

        Returns:
            BaseAuditLogModel: The audit row with ``{"before": snapshot}``.

        Raises:
            ValueError: See :meth:`new_entry`.
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
            event=event,
            ip=ip,
            user_agent=user_agent,
            request_context=request_context,
            actor_id=actor_id,
        )

    @classmethod
    def for_event(
        cls,
        event: str,
        *,
        entity: str,
        entity_id: str = "",
        changes: dict[str, Any] | None = None,
        actor: str | None = None,
        context: dict[str, Any] | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        request_context: AuditRequestContext | None = None,
        actor_id: UUID | None = None,
    ) -> BaseAuditLogModel:
        """Build an ``event`` entry — a domain event with no row mutation.

        Requires :class:`AuditRequestMixin` on the audit table, since the
        event name is what the entry records. ``entity_id`` defaults to
        ``""`` (the column is ``NOT NULL``) for an event about no single
        row.

        Args:
            event (str): Domain event name (``"export.requested"``).
            entity (str): The model the event concerns.
            entity_id (str): The concerned row id, or ``""`` for none.
            changes (dict[str, Any] | None): Optional payload; stored as
                ``{}`` when ``None``.
            actor (str | None): Who triggered the event.
            context (dict[str, Any] | None): Extra metadata.
            ip (str | None): Client IP.
            user_agent (str | None): User agent.
            request_context (AuditRequestContext | None): ``ip`` and
                ``user_agent`` together.
            actor_id (UUID | None): The author's id.

        Returns:
            BaseAuditLogModel: The audit row with ``action="event"``.

        Raises:
            ValueError: See :meth:`new_entry` — including when the audit
                table lacks :class:`AuditRequestMixin`.
        """
        return cls.new_entry(
            entity=entity,
            entity_id=entity_id,
            action=AuditAction.EVENT,
            changes=changes if changes is not None else {},
            actor=actor,
            context=context,
            event=event,
            ip=ip,
            user_agent=user_agent,
            request_context=request_context,
            actor_id=actor_id,
        )


__all__: list[str] = [
    "AUDIT_EVENT_MAX_LENGTH",
    "AUDIT_IP_MAX_LENGTH",
    "AUDIT_REDACTED",
    "AUDIT_USER_AGENT_MAX_LENGTH",
    "DEFAULT_AUDIT_REDACT",
    "AuditAction",
    "AuditRequestContext",
    "AuditRequestMixin",
    "BaseAuditLogModel",
    "audit_redacted_columns",
    "diff_snapshots",
    "redact_snapshot",
    "snapshot_model",
]
