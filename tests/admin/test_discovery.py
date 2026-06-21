"""Tests for admin model auto-discovery + ``AdminSite.automap`` + brand."""

from __future__ import annotations

import sys

import pytest
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AdminSite, BaseModel, discover_models


class WidgetModel(BaseModel):
    __tablename__ = "admin_discovery_widget"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class GadgetModel(BaseModel):
    __tablename__ = "admin_discovery_gadget"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class _AbstractThing(BaseModel):
    """Abstract base — no __tablename__; discovery must skip it."""

    __abstract__ = True


_THIS_MODULE = sys.modules[__name__]


class TestDiscoverModels:
    def test_finds_concrete_models(self) -> None:
        found = discover_models(_THIS_MODULE)
        assert WidgetModel in found
        assert GadgetModel in found

    def test_skips_abstract_and_base(self) -> None:
        found = discover_models(_THIS_MODULE)
        assert _AbstractThing not in found
        assert BaseModel not in found

    def test_ordered_by_table_name(self) -> None:
        found = discover_models(_THIS_MODULE)
        tablenames = [m.__tablename__ for m in found]
        assert tablenames == sorted(tablenames)

    def test_exclude_by_class(self) -> None:
        found = discover_models(_THIS_MODULE, exclude=[WidgetModel])
        assert WidgetModel not in found
        assert GadgetModel in found

    def test_exclude_by_class_name(self) -> None:
        found = discover_models(_THIS_MODULE, exclude=["GadgetModel"])
        assert GadgetModel not in found

    def test_exclude_by_table_name(self) -> None:
        found = discover_models(_THIS_MODULE, exclude=["admin_discovery_widget"])
        assert WidgetModel not in found

    def test_accepts_dotted_path(self) -> None:
        found = discover_models(__name__)
        assert WidgetModel in found


class TestAutomap:
    def test_registers_all_discovered(self) -> None:
        site = AdminSite()
        registered = site.automap(_THIS_MODULE)
        slugs = {admin.get_slug() for admin in registered}
        assert "admin_discovery_widget" in slugs
        assert "admin_discovery_gadget" in slugs
        assert site.get("admin_discovery_widget") is not None

    def test_skip_registered_leaves_manual_config(self) -> None:
        from tempest_fastapi_sdk import AdminModel

        site = AdminSite()
        manual = AdminModel(model=WidgetModel, page_size=99)
        site.register(manual)
        site.automap(_THIS_MODULE)  # skip_registered=True by default
        # The hand-tuned admin is untouched.
        assert site.get("admin_discovery_widget") is manual
        assert site.get("admin_discovery_widget").page_size == 99

    def test_exclude_skips_model(self) -> None:
        site = AdminSite()
        site.automap(_THIS_MODULE, exclude=[GadgetModel])
        assert site.get("admin_discovery_gadget") is None

    def test_admin_kwargs_applied_uniformly(self) -> None:
        site = AdminSite()
        registered = site.automap(_THIS_MODULE, page_size=50, can_delete=False)
        assert registered
        for admin in registered:
            assert admin.page_size == 50
            assert admin.can_delete is False

    def test_skip_registered_false_raises_on_collision(self) -> None:
        from tempest_fastapi_sdk import AdminModel

        site = AdminSite()
        site.register(AdminModel(model=WidgetModel))
        with pytest.raises(ValueError):
            site.automap(_THIS_MODULE, skip_registered=False)


class TestBrandText:
    def test_brand_falls_back_to_title(self) -> None:
        site = AdminSite(title="Servus")
        assert site.brand_text == "Servus"

    def test_brand_override(self) -> None:
        site = AdminSite(title="Servus", brand="servus-backend-admin")
        assert site.brand_text == "servus-backend-admin"
