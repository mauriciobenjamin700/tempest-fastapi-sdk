"""``BaseModel.get_table_name()`` answers the mapped table's name (#304).

Consumers read ``Model.__tablename__`` because nothing else was there. That
is a declarative directive, absent on a model declared with ``__table__``,
and wrong under single-table inheritance only by accident of where the rows
live. The accessor reads the mapped table instead.
"""

from __future__ import annotations

from typing import Any, ClassVar, assert_type

import pytest
from sqlalchemy import Boolean, Column, DateTime, String, Table, Uuid
from sqlalchemy.exc import NoInspectionAvailable
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    BaseModel,
    BaseUserModel,
    BaseUserTokenModel,
)


class TableNameProbeItemModel(BaseModel):
    """Name derived from the class, ``Model`` suffix stripped."""


class NamedThing(BaseModel):
    __tablename__ = "table_name_named_things"


class ExplicitTable(BaseModel):
    __table__ = Table(
        "table_name_explicit",
        BaseModel.metadata,
        Column("id", Uuid, primary_key=True),
        Column("is_active", Boolean),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
    )


class Shape(BaseModel):
    __tablename__ = "table_name_shapes"
    __mapper_args__: ClassVar[dict[str, Any]] = {
        "polymorphic_on": "kind",
        "polymorphic_identity": "shape",
    }

    kind: Mapped[str] = mapped_column(String(16))


class Circle(Shape):
    __mapper_args__: ClassVar[dict[str, Any]] = {"polymorphic_identity": "circle"}


class AbstractThing(BaseModel):
    __abstract__ = True


class TableNameUser(BaseUserModel):
    __tablename__ = "table_name_users"


class TableNameToken(BaseUserTokenModel):
    __tablename__ = "table_name_user_tokens"


class TestGetTableName:
    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            (TableNameProbeItemModel, "table_name_probe_item"),
            (NamedThing, "table_name_named_things"),
            (ExplicitTable, "table_name_explicit"),
            (Shape, "table_name_shapes"),
            (Circle, "table_name_shapes"),
            (TableNameUser, "table_name_users"),
            (TableNameToken, "table_name_user_tokens"),
        ],
    )
    def test_answers_the_mapped_table(
        self,
        model: type[BaseModel],
        expected: str,
    ) -> None:
        assert model.get_table_name() == expected

    def test_matches_the_table_the_mapper_built(self) -> None:
        assert NamedThing.get_table_name() == NamedThing.__table__.name

    def test_return_type_is_str(self) -> None:
        assert_type(TableNameProbeItemModel.get_table_name(), str)

    def test_abstract_model_is_not_mapped(self) -> None:
        with pytest.raises(NoInspectionAvailable):
            AbstractThing.get_table_name()


class TestAdminSiteByModel:
    def test_lookups_accept_the_model_class(self) -> None:
        site = AdminSite(title="T")
        admin = site.register(AdminModel(model=NamedThing))

        assert site.get(NamedThing) is admin
        assert site.get("table_name_named_things") is admin
        assert site.require(NamedThing) is admin
        assert site.get(TableNameProbeItemModel) is None

    def test_require_names_the_slug(self) -> None:
        with pytest.raises(KeyError, match="table_name_probe_item"):
            AdminSite(title="T").require(TableNameProbeItemModel)

    def test_unregister_accepts_the_model_class(self) -> None:
        site = AdminSite(title="T")
        site.register(AdminModel(model=NamedThing))

        site.unregister(NamedThing)

        assert site.get(NamedThing) is None
