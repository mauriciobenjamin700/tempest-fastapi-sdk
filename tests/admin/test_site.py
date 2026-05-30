"""Tests for AdminSite + AdminModel registry."""

import pytest
from sqlalchemy import String, desc
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AdminModel, AdminSite, BaseModel


class ItemModel(BaseModel):
    __tablename__ = "admin_site_test_item"
    title: Mapped[str] = mapped_column(String(64), nullable=False)


class OrderModel(BaseModel):
    __tablename__ = "admin_site_test_order"
    label: Mapped[str] = mapped_column(String(64), nullable=False)


item_admin = AdminModel(model=ItemModel)
order_admin = AdminModel(
    model=OrderModel,
    verbose_name="Pedido",
    verbose_name_plural="Pedidos",
)


class TestAdminModelValidation:
    def test_missing_model_raises(self) -> None:
        with pytest.raises(TypeError):
            AdminModel()  # type: ignore[call-arg]

    def test_wrong_model_type_raises(self) -> None:
        with pytest.raises(TypeError):
            AdminModel(model=object)  # type: ignore[type-var]


class TestAdminModelFieldRefs:
    def test_column_refs_normalized_to_keys(self) -> None:
        admin = AdminModel(
            model=ItemModel,
            list_display=[ItemModel.id, ItemModel.title],
            search_fields=[ItemModel.title],
        )
        assert admin.list_display == ["id", "title"]
        assert admin.search_fields == ["title"]

    def test_strings_still_accepted(self) -> None:
        admin = AdminModel(model=ItemModel, list_display=["id", "title"])
        assert admin.list_display == ["id", "title"]

    def test_ordering_desc_column(self) -> None:
        admin = AdminModel(model=ItemModel, ordering=desc(ItemModel.created_at))
        assert admin.order_key == "created_at"
        assert admin.order_ascending is False

    def test_ordering_plain_column_is_ascending(self) -> None:
        admin = AdminModel(model=ItemModel, ordering=ItemModel.title)
        assert admin.order_key == "title"
        assert admin.order_ascending is True

    def test_ordering_string_with_minus(self) -> None:
        admin = AdminModel(model=ItemModel, ordering="-created_at")
        assert admin.order_key == "created_at"
        assert admin.order_ascending is False


class TestAdminModelDefaults:
    def test_verbose_name_auto_humanized(self) -> None:
        assert item_admin.get_verbose_name() == "Item"
        assert item_admin.get_verbose_name_plural() == "Items"

    def test_verbose_name_override(self) -> None:
        assert order_admin.get_verbose_name() == "Pedido"
        assert order_admin.get_verbose_name_plural() == "Pedidos"

    def test_slug_from_tablename(self) -> None:
        assert item_admin.get_slug() == "admin_site_test_item"

    def test_resolved_list_display_drops_password(self) -> None:
        columns = item_admin.resolved_list_display()
        assert "title" in columns
        assert "id" in columns


class TestAdminSiteRegistry:
    def test_register_returns_instance(self) -> None:
        site = AdminSite()
        returned = site.register(item_admin)
        assert returned is item_admin
        assert site.get(item_admin.get_slug()) is item_admin

    def test_duplicate_registration_rejected(self) -> None:
        site = AdminSite()
        site.register(item_admin)
        with pytest.raises(ValueError):
            site.register(AdminModel(model=ItemModel))

    def test_require_missing_raises(self) -> None:
        site = AdminSite()
        with pytest.raises(KeyError):
            site.require("ghost")

    def test_unregister(self) -> None:
        site = AdminSite()
        site.register(item_admin)
        site.unregister(item_admin.get_slug())
        assert site.get(item_admin.get_slug()) is None

    def test_iter_models_ordered_by_plural(self) -> None:
        site = AdminSite()
        site.register(order_admin)
        site.register(item_admin)
        order = [admin.get_verbose_name_plural() for admin in site.iter_models()]
        assert order == sorted(order, key=str.lower)
