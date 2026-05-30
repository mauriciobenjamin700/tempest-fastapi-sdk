"""Tests for AdminSite + AdminModel registry."""

import pytest
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AdminModel, AdminSite, BaseModel


class ItemModel(BaseModel):
    __tablename__ = "admin_site_test_item"
    title: Mapped[str] = mapped_column(String(64), nullable=False)


class OrderModel(BaseModel):
    __tablename__ = "admin_site_test_order"
    label: Mapped[str] = mapped_column(String(64), nullable=False)


class ItemAdmin(AdminModel[ItemModel]):
    model = ItemModel


class OrderAdmin(AdminModel[OrderModel]):
    model = OrderModel
    verbose_name = "Pedido"
    verbose_name_plural = "Pedidos"


class TestAdminModelValidation:
    def test_missing_model_raises(self) -> None:
        with pytest.raises(TypeError):

            class Bad(AdminModel):  # type: ignore[type-arg]
                pass

    def test_wrong_model_type_raises(self) -> None:
        with pytest.raises(TypeError):

            class Bad(AdminModel):  # type: ignore[type-arg]
                model = object  # type: ignore[assignment]


class TestAdminModelDefaults:
    def test_verbose_name_auto_humanized(self) -> None:
        assert ItemAdmin.get_verbose_name() == "Item"
        assert ItemAdmin.get_verbose_name_plural() == "Items"

    def test_verbose_name_override(self) -> None:
        assert OrderAdmin.get_verbose_name() == "Pedido"
        assert OrderAdmin.get_verbose_name_plural() == "Pedidos"

    def test_slug_from_tablename(self) -> None:
        assert ItemAdmin.get_slug() == "admin_site_test_item"

    def test_resolved_list_display_drops_password(self) -> None:
        columns = ItemAdmin.resolved_list_display()
        assert "title" in columns
        assert "id" in columns


class TestAdminSiteRegistry:
    def test_register_decorator_style(self) -> None:
        site = AdminSite()
        returned = site.register(ItemAdmin)
        assert returned is ItemAdmin
        assert site.get(ItemAdmin.get_slug()) is ItemAdmin

    def test_duplicate_registration_rejected(self) -> None:
        site = AdminSite()
        site.register(ItemAdmin)
        with pytest.raises(ValueError):
            site.register(ItemAdmin)

    def test_require_missing_raises(self) -> None:
        site = AdminSite()
        with pytest.raises(KeyError):
            site.require("ghost")

    def test_unregister(self) -> None:
        site = AdminSite()
        site.register(ItemAdmin)
        site.unregister(ItemAdmin.get_slug())
        assert site.get(ItemAdmin.get_slug()) is None

    def test_iter_models_ordered_by_plural(self) -> None:
        site = AdminSite()
        site.register(OrderAdmin)
        site.register(ItemAdmin)
        order = [cls.get_verbose_name_plural() for cls in site.iter_models()]
        assert order == sorted(order, key=str.lower)
