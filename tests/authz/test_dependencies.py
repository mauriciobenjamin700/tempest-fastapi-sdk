"""Tests for the object-level permission FastAPI guard."""

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.authz import default_registry, make_permission_checker


class User:
    def __init__(self, *, is_admin: bool = False) -> None:
        self.id: UUID = uuid4()
        self.is_admin: bool = is_admin
        self.permissions: set[str] = set()


class Order:
    def __init__(self, owner_id: UUID) -> None:
        self.owner_id: UUID = owner_id


CURRENT = User()
ORDER = Order(owner_id=CURRENT.id)


def get_user() -> User:
    return CURRENT


def get_order() -> Order:
    return ORDER


@pytest.fixture(autouse=True)
def _clear_default() -> Iterator[None]:
    default_registry.clear()
    yield
    default_registry.clear()


def _make_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    checker = make_permission_checker(
        "order.delete",
        get_user=get_user,
        get_object=get_order,
    )

    @app.delete("/orders/{order_id}", dependencies=[Depends(checker)])
    async def delete_order(order_id: str) -> dict[str, bool]:
        return {"deleted": True}

    return app


def test_guard_allows_when_rule_grants() -> None:
    default_registry.register("order.delete", lambda user, obj: obj.owner_id == user.id)
    client = TestClient(_make_app())
    assert client.delete(f"/orders/{uuid4()}").status_code == 200


def test_guard_forbids_when_rule_denies() -> None:
    default_registry.register("order.delete", lambda _user, _obj: False)
    client = TestClient(_make_app())
    assert client.delete(f"/orders/{uuid4()}").status_code == 403


def test_model_level_guard_without_object() -> None:
    app = FastAPI()
    register_exception_handlers(app)
    default_registry.register("order.create", lambda _u, _o: True)

    checker = make_permission_checker("order.create", get_user=get_user)

    @app.post("/orders", dependencies=[Depends(checker)])
    async def create_order() -> dict[str, bool]:
        return {"created": True}

    client = TestClient(app)
    assert client.post("/orders").status_code == 200
