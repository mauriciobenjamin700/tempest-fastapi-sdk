"""Tests for tempest_fastapi_sdk.db.model.BaseModel."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db import NAMING_CONVENTION, BaseModel


class Widget(BaseModel):
    __tablename__ = "widget_for_model_test"

    name: Mapped[str] = mapped_column(String(64), nullable=False)


class TestBaseModelColumns:
    async def test_default_id_is_uuid(self, session: AsyncSession) -> None:
        widget = Widget(name="foo")
        session.add(widget)
        await session.flush()
        assert isinstance(widget.id, UUID)

    async def test_default_is_active_true(self, session: AsyncSession) -> None:
        widget = Widget(name="foo")
        session.add(widget)
        await session.flush()
        assert widget.is_active is True

    async def test_timestamps_populated_on_flush_without_refresh(
        self, session: AsyncSession
    ) -> None:
        widget = Widget(name="foo")
        session.add(widget)
        await session.flush()
        # Hybrid default=utcnow + server_default=NOW() means the
        # instance attributes are set by Python at flush time — no
        # refresh required to read them.
        assert isinstance(widget.created_at, datetime)
        assert isinstance(widget.updated_at, datetime)
        assert widget.created_at.tzinfo is not None
        assert widget.updated_at.tzinfo is not None

    async def test_updated_at_advances_on_update(self, session: AsyncSession) -> None:
        import asyncio

        widget = Widget(name="foo")
        session.add(widget)
        await session.flush()
        original_updated_at = widget.updated_at
        await asyncio.sleep(0.01)
        widget.name = "bar"
        await session.flush()
        assert widget.updated_at > original_updated_at


class TestRepr:
    def test_repr_contains_class_and_columns(self) -> None:
        widget_id = uuid4()
        widget = Widget(name="foo", id=widget_id)
        result = repr(widget)
        assert "Widget(" in result
        assert "name='foo'" in result
        assert str(widget_id) in result


class TestToDict:
    def test_includes_inherited_columns(self) -> None:
        now = datetime(2024, 1, 1, tzinfo=UTC)
        widget = Widget(
            name="foo",
            id=uuid4(),
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        data = widget.to_dict()
        assert set(data.keys()) == {
            "id",
            "is_active",
            "created_at",
            "updated_at",
            "name",
        }

    def test_exclude_and_include(self) -> None:
        widget = Widget(name="foo", is_active=True)
        data = widget.to_dict(exclude=["is_active"], include={"role": "x"})
        assert "is_active" not in data
        assert data["role"] == "x"

    def test_remove_none_drops_nulls(self) -> None:
        widget = Widget(name="foo")
        widget.created_at = None  # type: ignore[assignment]
        data = widget.to_dict(remove_none=True)
        assert "created_at" not in data


class TestNamingConvention:
    def test_metadata_uses_naming_convention(self) -> None:
        assert BaseModel.metadata.naming_convention == NAMING_CONVENTION

    def test_widget_table_has_pk_constraint_named(self) -> None:
        # PK constraint name should follow the "pk_<tablename>" template.
        pk = Widget.__table__.primary_key
        assert pk.name == "pk_widget_for_model_test"


class TestAutoTablename:
    def test_strips_model_suffix_and_snake_cases(self) -> None:
        class UserModel(BaseModel):
            name: Mapped[str] = mapped_column(String(64), nullable=False)

        try:
            assert UserModel.__tablename__ == "user"
        finally:
            BaseModel.metadata.remove(UserModel.__table__)

    def test_multiword_camelcase_snake_cases(self) -> None:
        class OrderItemModel(BaseModel):
            name: Mapped[str] = mapped_column(String(64), nullable=False)

        try:
            assert OrderItemModel.__tablename__ == "order_item"
        finally:
            BaseModel.metadata.remove(OrderItemModel.__table__)

    def test_no_model_suffix_falls_back_to_class_name(self) -> None:
        class Account(BaseModel):
            name: Mapped[str] = mapped_column(String(64), nullable=False)

        try:
            assert Account.__tablename__ == "account"
        finally:
            BaseModel.metadata.remove(Account.__table__)

    def test_explicit_tablename_overrides_default(self) -> None:
        # Widget explicitly sets __tablename__; check it wins.
        assert Widget.__tablename__ == "widget_for_model_test"


class TestEqAndHash:
    def test_same_id_compare_equal(self) -> None:
        shared_id = uuid4()
        a = Widget(name="foo", id=shared_id)
        b = Widget(name="bar", id=shared_id)
        assert a == b
        assert hash(a) == hash(b)

    def test_different_id_compare_not_equal(self) -> None:
        a = Widget(name="foo", id=uuid4())
        b = Widget(name="foo", id=uuid4())
        assert a != b

    def test_unflushed_falls_back_to_identity(self) -> None:
        a = Widget(name="foo")
        b = Widget(name="foo")
        # Same content but distinct in-memory objects → not equal.
        assert a != b
        assert hash(a) != hash(b)

    def test_different_classes_return_not_implemented(self) -> None:
        widget = Widget(name="foo", id=uuid4())
        assert (widget == "not a widget") is False

    def test_set_with_same_id_collapses(self) -> None:
        shared_id = uuid4()
        bucket = {Widget(name="a", id=shared_id), Widget(name="b", id=shared_id)}
        assert len(bucket) == 1


class TestUpdateFromDict:
    def test_assigns_known_columns(self) -> None:
        widget = Widget(name="foo")
        widget.update_from_dict({"name": "bar"})
        assert widget.name == "bar"

    def test_ignores_unknown_keys(self) -> None:
        widget = Widget(name="foo")
        widget.update_from_dict({"name": "bar", "unknown": "noise"})
        assert widget.name == "bar"
        assert not hasattr(widget, "unknown")

    def test_allowed_fields_whitelists(self) -> None:
        widget = Widget(name="foo", is_active=True)
        widget.update_from_dict(
            {"name": "bar", "is_active": False},
            allowed_fields={"name"},
        )
        assert widget.name == "bar"
        assert widget.is_active is True

    def test_empty_allowed_fields_blocks_everything(self) -> None:
        widget = Widget(name="foo")
        widget.update_from_dict({"name": "bar"}, allowed_fields=set())
        assert widget.name == "foo"
