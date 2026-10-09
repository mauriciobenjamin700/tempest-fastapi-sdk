"""Tests for the per-entity audit trail."""

from collections.abc import AsyncGenerator
from typing import Any, ClassVar
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import ForeignKey, String, delete, select
from sqlalchemy.orm import Mapped, mapped_column
from starlette.requests import Request

from tempest_fastapi_sdk.db import (
    AUDIT_EVENT_MAX_LENGTH,
    AUDIT_REDACTED,
    AUDIT_USER_AGENT_MAX_LENGTH,
    AsyncDatabaseManager,
    AuditAction,
    AuditRequestContext,
    AuditRequestMixin,
    BaseAuditLogModel,
    BaseModel,
    BaseRepository,
    audit_redacted_columns,
    diff_snapshots,
    redact_snapshot,
    snapshot_model,
)


class _GadgetModel(BaseModel):
    """Business row used by the audit tests."""

    __tablename__ = "gadget"

    name: Mapped[str] = mapped_column(String(50), nullable=False)


class _AuditLogModel(BaseAuditLogModel):
    """Concrete audit-log table for the tests."""

    __tablename__ = "audit_log"


class _GadgetRepository(BaseRepository[_GadgetModel]):
    def __init__(self, session: Any) -> None:
        super().__init__(session, model=_GadgetModel, audit_model=_AuditLogModel)


class _UnauditedRepository(BaseRepository[_GadgetModel]):
    def __init__(self, session: Any) -> None:
        super().__init__(session, model=_GadgetModel)


@pytest_asyncio.fixture
async def audit_db() -> AsyncGenerator[AsyncDatabaseManager]:
    """In-memory database with the gadget + audit_log tables created."""
    manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await manager.connect()
    await manager.create_tables()
    try:
        yield manager
    finally:
        await manager.drop_tables()
        await manager.disconnect()


# --------------------------------------------------------------------------- #
# snapshot / diff helpers                                                     #
# --------------------------------------------------------------------------- #


def test_snapshot_model_is_jsonable() -> None:
    """A snapshot turns UUIDs into strings and includes every column."""
    gadget = _GadgetModel(name="a")
    snap = snapshot_model(gadget)
    assert snap["name"] == "a"
    assert "id" in snap and "is_active" in snap


def test_diff_snapshots_reports_changes() -> None:
    """The diff lists only changed fields, union of both key sets."""
    diff = diff_snapshots({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4})
    assert diff == {
        "b": {"before": 2, "after": 3},
        "c": {"before": None, "after": 4},
    }


# --------------------------------------------------------------------------- #
# Entry classmethods                                                          #
# --------------------------------------------------------------------------- #


def test_for_create_snapshots_after() -> None:
    """A create entry stores the new row under ``after``."""
    gadget = _GadgetModel(name="new")
    entry = _AuditLogModel.for_create(gadget, actor="alice")
    assert entry.action == AuditAction.CREATE.value
    assert entry.actor == "alice"
    assert entry.changes["after"]["name"] == "new"
    assert entry.entity == "_GadgetModel"


def test_for_delete_snapshots_before() -> None:
    """A delete entry stores the removed row under ``before``."""
    gadget = _GadgetModel(name="gone")
    entry = _AuditLogModel.for_delete(gadget)
    assert entry.action == AuditAction.DELETE.value
    assert entry.changes["before"]["name"] == "gone"


# --------------------------------------------------------------------------- #
# Repository hook                                                             #
# --------------------------------------------------------------------------- #


async def test_add_audited_writes_both_rows(
    audit_db: AsyncDatabaseManager,
) -> None:
    """add_audited persists the business row and a create audit entry."""
    async with audit_db.get_session_context() as session:
        repo = _GadgetRepository(session)
        gadget = await repo.add_audited(_GadgetModel(name="widget-1"), actor="bob")
        assert gadget.id is not None

    async with audit_db.get_session_context() as session:
        rows = (await session.execute(select(_AuditLogModel))).scalars().all()
        assert len(rows) == 1
        entry = rows[0]
        assert entry.action == AuditAction.CREATE.value
        assert entry.actor == "bob"
        assert entry.entity_id == str(gadget.id)
        assert entry.changes["after"]["name"] == "widget-1"


async def test_update_audited_records_diff(
    audit_db: AsyncDatabaseManager,
) -> None:
    """update_audited stores only the changed fields."""
    async with audit_db.get_session_context() as session:
        repo = _GadgetRepository(session)
        gadget = await repo.add_audited(_GadgetModel(name="before"))

    async with audit_db.get_session_context() as session:
        repo = _GadgetRepository(session)
        gadget = await session.get(_GadgetModel, gadget.id)
        assert gadget is not None
        before = repo.snapshot(gadget)
        gadget.name = "after"
        await repo.update_audited(gadget, before, actor="carol")

    async with audit_db.get_session_context() as session:
        rows = (
            (
                await session.execute(
                    select(_AuditLogModel).where(
                        _AuditLogModel.action == AuditAction.UPDATE.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].changes["name"] == {"before": "before", "after": "after"}
        assert rows[0].actor == "carol"


async def test_delete_audited_removes_row_and_logs(
    audit_db: AsyncDatabaseManager,
) -> None:
    """delete_audited deletes the row and logs the before-snapshot."""
    async with audit_db.get_session_context() as session:
        repo = _GadgetRepository(session)
        gadget = await repo.add_audited(_GadgetModel(name="doomed"))

    async with audit_db.get_session_context() as session:
        repo = _GadgetRepository(session)
        gadget = await session.get(_GadgetModel, gadget.id)
        assert gadget is not None
        await repo.delete_audited(gadget, actor="dave")

    async with audit_db.get_session_context() as session:
        assert await session.get(_GadgetModel, gadget.id) is None
        rows = (
            (
                await session.execute(
                    select(_AuditLogModel).where(
                        _AuditLogModel.action == AuditAction.DELETE.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].changes["before"]["name"] == "doomed"
        assert rows[0].actor == "dave"


async def test_audit_without_model_raises(
    audit_db: AsyncDatabaseManager,
) -> None:
    """A repository built without audit_model refuses to record."""
    async with audit_db.get_session_context() as session:
        repo = _UnauditedRepository(session)
        with pytest.raises(RuntimeError, match="without an audit_model"):
            await repo.add_audited(_GadgetModel(name="x"))


class _CredentialModel(BaseModel):
    __tablename__ = "_test_audit_credential"
    __audit_redact__: ClassVar[frozenset[str]] = frozenset({"totp_secret"})

    hashed_password: Mapped[str] = mapped_column(String(128), default="")
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    label: Mapped[str] = mapped_column(String(32), default="")


class _TypoModel(BaseModel):
    __tablename__ = "_test_audit_typo"
    __audit_redact__: ClassVar[frozenset[str]] = frozenset({"totp_secert"})

    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)


class TestAuditRedaction:
    """``__audit_redact__`` keeps a credential's value out of the audit table."""

    def test_declared_and_default_columns(self) -> None:
        assert audit_redacted_columns(_CredentialModel) == {
            "totp_secret",
            "hashed_password",
        }

    def test_default_applies_only_where_the_column_exists(self) -> None:
        assert audit_redacted_columns(_GadgetModel) == frozenset()

    def test_typo_raises(self) -> None:
        with pytest.raises(ValueError, match="totp_secert"):
            audit_redacted_columns(_TypoModel)

    def test_create_and_delete_store_the_marker(self) -> None:
        row = _CredentialModel(hashed_password="$2b$digest", totp_secret="JBSWY3DP")

        created = _AuditLogModel.for_create(row).changes["after"]
        deleted = _AuditLogModel.for_delete(row).changes["before"]

        for snapshot in (created, deleted):
            assert snapshot["totp_secret"] == AUDIT_REDACTED
            assert snapshot["hashed_password"] == AUDIT_REDACTED

    def test_none_stays_none(self) -> None:
        row = _CredentialModel(hashed_password="$2b$digest", totp_secret=None)

        assert _AuditLogModel.for_create(row).changes["after"]["totp_secret"] is None

    def test_update_records_the_rotation_without_values(self) -> None:
        row = _CredentialModel(hashed_password="h", totp_secret="OLD", label="a")
        before = snapshot_model(row)
        row.totp_secret = "NEW"
        row.label = "b"

        changes = _AuditLogModel.for_update(row, before).changes

        assert changes["totp_secret"] == {
            "before": AUDIT_REDACTED,
            "after": AUDIT_REDACTED,
        }
        assert changes["label"] == {"before": "a", "after": "b"}

    def test_redact_snapshot_returns_a_new_dict(self) -> None:
        raw = {"totp_secret": "X", "label": "a"}

        assert redact_snapshot(_CredentialModel, raw) == {
            "totp_secret": AUDIT_REDACTED,
            "label": "a",
        }
        assert raw["totp_secret"] == "X"


# --------------------------------------------------------------------------- #
# Event, request origin and author FK (#458)                                  #
# --------------------------------------------------------------------------- #


class _OriginUserModel(BaseModel):
    """Stand-in users table the author FK points at."""

    __tablename__ = "_test_audit_origin_user"

    email: Mapped[str] = mapped_column(String(64), nullable=False)


class _OriginAuditLogModel(AuditRequestMixin, BaseAuditLogModel):
    """Audit table with the request columns and an author FK."""

    __tablename__ = "_test_audit_origin_log"

    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("_test_audit_origin_user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class _OriginGadgetRepository(BaseRepository[_GadgetModel]):
    def __init__(self, session: Any) -> None:
        super().__init__(session, model=_GadgetModel, audit_model=_OriginAuditLogModel)


def _request(headers: dict[str, str], client: tuple[str, int] | None) -> Request:
    """Build a bare Starlette request with ``headers`` and peer ``client``."""
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in headers.items()
        ],
        "client": client,
    }
    return Request(scope)


async def _origin_rows(
    audit_db: AsyncDatabaseManager,
) -> list[_OriginAuditLogModel]:
    """Return every row of the origin audit table, oldest first."""
    async with audit_db.get_session_context() as session:
        result = await session.execute(
            select(_OriginAuditLogModel).order_by(_OriginAuditLogModel.created_at)
        )
        return list(result.scalars().all())


class TestAuditRequestColumns:
    """``event`` / ``ip`` / ``user_agent`` land in their own columns."""

    async def test_add_audited_writes_each_value_to_its_column(
        self,
        audit_db: AsyncDatabaseManager,
    ) -> None:
        async with audit_db.get_session_context() as session:
            await _OriginGadgetRepository(session).add_audited(
                _GadgetModel(name="g"),
                event="consent.granted",
                ip="203.0.113.7",
                user_agent="pytest/1.0",
            )

        [entry] = await _origin_rows(audit_db)
        assert entry.action == AuditAction.CREATE.value
        assert entry.event == "consent.granted"
        assert entry.ip == "203.0.113.7"
        assert entry.user_agent == "pytest/1.0"
        assert entry.context is None

    async def test_update_and_delete_accept_a_request_context(
        self,
        audit_db: AsyncDatabaseManager,
    ) -> None:
        origin = AuditRequestContext(ip="2001:db8::1", user_agent="ua")
        async with audit_db.get_session_context() as session:
            repo = _OriginGadgetRepository(session)
            gadget = await repo.add_audited(_GadgetModel(name="a"))
            before = repo.snapshot(gadget)
            gadget.name = "b"
            await repo.update_audited(
                gadget, before, event="gadget.renamed", request_context=origin
            )
            await repo.delete_audited(gadget, request_context=origin)

        rows = await _origin_rows(audit_db)
        by_action = {row.action: row for row in rows}
        create = by_action["create"]
        update = by_action["update"]
        removed = by_action["delete"]
        assert (create.ip, create.event) == (None, None)
        assert (update.event, update.ip, update.user_agent) == (
            "gadget.renamed",
            "2001:db8::1",
            "ua",
        )
        assert (removed.ip, removed.user_agent) == ("2001:db8::1", "ua")

    def test_request_context_and_explicit_ip_are_exclusive(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            _OriginAuditLogModel.for_create(
                _GadgetModel(name="x"),
                ip="203.0.113.7",
                request_context=AuditRequestContext(ip="198.51.100.1"),
            )

    def test_user_agent_is_truncated_to_the_column(self) -> None:
        entry = _OriginAuditLogModel.for_create(
            _GadgetModel(name="x"), user_agent="a" * 2000
        )
        assert entry.user_agent == "a" * AUDIT_USER_AGENT_MAX_LENGTH

    def test_oversized_event_is_refused(self) -> None:
        with pytest.raises(ValueError, match="event"):
            _OriginAuditLogModel.for_create(
                _GadgetModel(name="x"), event="e" * (AUDIT_EVENT_MAX_LENGTH + 1)
            )


class TestLegacyAuditTable:
    """A table without the mixin keeps working and never drops a value."""

    def test_base_model_has_no_new_columns(self) -> None:
        columns = {column.name for column in _AuditLogModel.__table__.columns}
        assert {"event", "ip", "user_agent", "actor_id"}.isdisjoint(columns)

    async def test_value_for_a_missing_column_is_refused_and_rolled_back(
        self,
        audit_db: AsyncDatabaseManager,
    ) -> None:
        async with audit_db.get_session_context() as session:
            with pytest.raises(ValueError, match="AuditRequestMixin"):
                await _GadgetRepository(session).add_audited(
                    _GadgetModel(name="lost"), ip="203.0.113.7"
                )

        async with audit_db.get_session_context() as session:
            assert (await session.execute(select(_GadgetModel))).first() is None
            assert (await session.execute(select(_AuditLogModel))).first() is None

    async def test_record_event_needs_the_mixin(
        self,
        audit_db: AsyncDatabaseManager,
    ) -> None:
        async with audit_db.get_session_context() as session:
            with pytest.raises(ValueError, match="event"):
                await _GadgetRepository(session).record_event("export.requested")


class TestRecordEvent:
    """``record_event`` writes a row without a row mutation."""

    async def test_event_without_subject(
        self,
        audit_db: AsyncDatabaseManager,
    ) -> None:
        async with audit_db.get_session_context() as session:
            entry = await _OriginGadgetRepository(session).record_event(
                "export.requested",
                actor="alice",
                ip="203.0.113.7",
            )
            assert entry.action == AuditAction.EVENT.value

        [row] = await _origin_rows(audit_db)
        assert row.action == "event"
        assert row.event == "export.requested"
        assert row.entity == "_GadgetModel"
        assert row.entity_id == ""
        assert row.changes == {}
        assert (row.actor, row.ip) == ("alice", "203.0.113.7")

    async def test_event_about_a_row(
        self,
        audit_db: AsyncDatabaseManager,
    ) -> None:
        async with audit_db.get_session_context() as session:
            repo = _OriginGadgetRepository(session)
            gadget = await repo.add(_GadgetModel(name="g"))
            await repo.record_event(
                "gadget.viewed", subject=gadget, changes={"via": "api"}
            )

        [row] = await _origin_rows(audit_db)
        assert row.entity_id == str(gadget.id)
        assert row.changes == {"via": "api"}


class TestAuthorForeignKey:
    """A subclass with an ``actor_id`` FK works without overriding anything."""

    async def test_actor_id_is_stored_and_set_null_on_user_delete(
        self,
        audit_db: AsyncDatabaseManager,
    ) -> None:
        async with audit_db.get_session_context() as session:
            user = _OriginUserModel(email="u@example.com")
            session.add(user)
            await session.commit()
            await _OriginGadgetRepository(session).add_audited(
                _GadgetModel(name="g"), actor=str(user.id), actor_id=user.id
            )

        [entry] = await _origin_rows(audit_db)
        assert entry.actor_id == user.id

        async with audit_db.get_session_context() as session:
            await session.execute(delete(_OriginUserModel))
            await session.commit()

        [entry] = await _origin_rows(audit_db)
        assert entry.actor_id is None

    def test_actor_id_on_a_table_without_the_column_is_refused(self) -> None:
        with pytest.raises(ValueError, match="actor_id"):
            _AuditLogModel.for_create(_GadgetModel(name="x"), actor_id=uuid4())


class TestAuditRequestContextFromRequest:
    """``from_request`` reads the IP the caller trusts, plus the user agent."""

    def test_trusted_header_wins_over_the_peer(self) -> None:
        request = _request(
            {"X-Real-IP": "203.0.113.9", "User-Agent": "curl/8"},
            ("10.0.0.1", 5000),
        )
        origin = AuditRequestContext.from_request(
            request, trusted_ip_header="x-real-ip"
        )
        assert origin == AuditRequestContext(ip="203.0.113.9", user_agent="curl/8")

    def test_untrusted_header_is_ignored(self) -> None:
        request = _request({"X-Real-IP": "203.0.113.9"}, ("10.0.0.1", 5000))
        origin = AuditRequestContext.from_request(request, trusted_ip_header=None)
        assert origin == AuditRequestContext(ip="10.0.0.1", user_agent=None)

    def test_non_address_is_stored_as_none(self) -> None:
        request = _request({"X-Real-IP": "not-an-ip"}, None)
        origin = AuditRequestContext.from_request(
            request, trusted_ip_header="x-real-ip"
        )
        assert origin.ip is None

    def test_trusted_ip_header_has_no_default(self) -> None:
        request = _request({}, ("10.0.0.1", 5000))
        with pytest.raises(TypeError, match="trusted_ip_header"):
            AuditRequestContext.from_request(request)  # type: ignore[call-arg]
