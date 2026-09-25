"""``orderable_columns`` and ``max_page_size`` on the pagination filters.

Issue #295: ``order_by`` was a free ``str`` and ``page_size`` had no ceiling,
so a public listing was a sort oracle and a single request could read the
whole table. These tests pin the schema half of the fix, at the model and
through FastAPI (both ``Depends()`` and ``Annotated[..., Query()]``), since
the ceiling only protects anything if it survives into the query parameter.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import Field, ValidationError

from tempest_fastapi_sdk import (
    DEFAULT_MAX_CURSOR_LIMIT,
    DEFAULT_MAX_PAGE_SIZE,
    BasePaginationFilterSchema,
    CompactPaginationFilterSchema,
    CursorPaginationFilterSchema,
    OrderByNotAllowedException,
    ValidationException,
    register_exception_handlers,
)


class _ProducerFilter(BasePaginationFilterSchema):
    orderable_columns = frozenset({"created_at", "name"})
    max_page_size = 200


class _Uncapped(BasePaginationFilterSchema):
    max_page_size = None


class _Child(_ProducerFilter):
    """Inherits both guards without redeclaring them."""


class _CompactCapped(CompactPaginationFilterSchema):
    max_page_size = 30


class _OwnBound(BasePaginationFilterSchema):
    """Redeclares ``page_size`` without ``max_page_size`` — keeps its bound."""

    page_size: int = Field(default=20, ge=1, le=250)


class _CursorFilter(CursorPaginationFilterSchema):
    orderable_columns = frozenset({"created_at", "score"})
    max_limit = 50


def _page_size_error(schema: type[Any], value: int, key: str = "page_size") -> Any:
    with pytest.raises(ValidationError) as exc:
        schema.model_validate({key: value})
    return exc.value.errors()[0]


class TestPageSizeCeiling:
    def test_default_ceiling_is_100(self) -> None:
        assert DEFAULT_MAX_PAGE_SIZE == 100
        assert BasePaginationFilterSchema(page_size=100).page_size == 100
        error = _page_size_error(BasePaginationFilterSchema, 101)
        assert error["type"] == "less_than_equal"
        assert error["ctx"] == {"le": 100}

    def test_subclass_raises_the_ceiling(self) -> None:
        assert _ProducerFilter(page_size=200).page_size == 200
        assert _page_size_error(_ProducerFilter, 201)["ctx"] == {"le": 200}

    def test_subclass_ceiling_does_not_leak_to_the_base(self) -> None:
        assert _page_size_error(BasePaginationFilterSchema, 150)["ctx"] == {"le": 100}

    def test_ceiling_is_inherited(self) -> None:
        assert _Child(page_size=200).page_size == 200
        assert _page_size_error(_Child, 201)["ctx"] == {"le": 200}

    def test_none_removes_the_ceiling(self) -> None:
        assert _Uncapped(page_size=10**6).page_size == 10**6

    def test_lower_bound_survives_the_rewrite(self) -> None:
        assert _page_size_error(_ProducerFilter, 0)["type"] == "greater_than_equal"

    def test_compact_schema_keeps_the_alias(self) -> None:
        assert _CompactCapped.model_validate({"size": 30}).page_size == 30
        error = _page_size_error(_CompactCapped, 31, key="size")
        assert error["ctx"] == {"le": 30}
        assert error["loc"] == ("size",)

    def test_compact_default_ceiling(self) -> None:
        error = _page_size_error(CompactPaginationFilterSchema, 101, key="size")
        assert error["ctx"] == {"le": 100}

    def test_redeclared_field_keeps_its_own_bound(self) -> None:
        assert _OwnBound(page_size=250).page_size == 250
        assert _page_size_error(_OwnBound, 251)["ctx"] == {"le": 250}

    def test_json_schema_publishes_the_maximum(self) -> None:
        schema = _ProducerFilter.model_json_schema()
        assert schema["properties"]["page_size"]["maximum"] == 200


class TestOrderableColumns:
    def test_undeclared_accepts_any_value(self) -> None:
        schema = BasePaginationFilterSchema(order_by="hashed_password")
        assert schema.order_by == "hashed_password"

    def test_declared_column_passes(self) -> None:
        assert _ProducerFilter(order_by="name").order_by == "name"

    def test_unlisted_column_is_refused(self) -> None:
        with pytest.raises(OrderByNotAllowedException) as exc:
            _ProducerFilter(order_by="wallet")
        assert isinstance(exc.value, ValidationException)
        assert exc.value.status_code == 422
        assert exc.value.code == "ORDER_BY_NOT_ALLOWED"
        assert exc.value.field == "order_by"
        assert exc.value.details == {
            "order_by": "wallet",
            "allowed": ["created_at", "name"],
        }

    @pytest.mark.parametrize(
        "column",
        ["hashed_password", "wallet", "cpf_cnpj", "email", "id", "Name"],
    )
    def test_refusal_never_lists_a_column_outside_the_set(self, column: str) -> None:
        with pytest.raises(OrderByNotAllowedException) as exc:
            _ProducerFilter(order_by=column)
        assert set(exc.value.details["allowed"]) <= _ProducerFilter.orderable_columns

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_empty_means_absent(self, value: str | None) -> None:
        assert _ProducerFilter(order_by=value).order_by is None
        assert BasePaginationFilterSchema(order_by=value).order_by is None

    def test_pagination_conditions_carry_the_validated_value(self) -> None:
        conditions = _ProducerFilter(order_by="name").get_pagination_conditions()
        assert conditions["order_by"] == "name"


class TestCursorFilter:
    def test_default_limit_ceiling_is_unchanged(self) -> None:
        assert DEFAULT_MAX_CURSOR_LIMIT == 500
        error = _page_size_error(CursorPaginationFilterSchema, 501, key="limit")
        assert error["ctx"] == {"le": 500}

    def test_max_limit_rewrites_the_bound(self) -> None:
        assert _CursorFilter(limit=50).limit == 50
        assert _page_size_error(_CursorFilter, 51, key="limit")["ctx"] == {"le": 50}

    def test_unlisted_order_by_is_refused(self) -> None:
        with pytest.raises(OrderByNotAllowedException) as exc:
            _CursorFilter(order_by="hashed_password")
        assert exc.value.details["allowed"] == ["created_at", "score"]

    def test_undeclared_cursor_filter_accepts_any(self) -> None:
        assert CursorPaginationFilterSchema(order_by="x").order_by == "x"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/depends")
    def by_depends(f: _ProducerFilter = Depends()) -> dict[str, Any]:
        return f.get_pagination_conditions()

    @app.get("/query")
    def by_query(f: Annotated[_ProducerFilter, Query()]) -> dict[str, Any]:
        return f.get_pagination_conditions()

    return TestClient(app, raise_server_exceptions=False)


class TestThroughFastAPI:
    @pytest.mark.parametrize("path", ["/depends", "/query"])
    def test_unlisted_order_by_answers_422_with_only_the_set(
        self, client: TestClient, path: str
    ) -> None:
        response = client.get(path, params={"order_by": "hashed_password"})
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "ORDER_BY_NOT_ALLOWED"
        assert body["field"] == "order_by"
        assert body["details"] == {
            "order_by": "hashed_password",
            "allowed": ["created_at", "name"],
        }

    @pytest.mark.parametrize("path", ["/depends", "/query"])
    def test_page_size_above_the_ceiling_is_422(
        self, client: TestClient, path: str
    ) -> None:
        response = client.get(path, params={"page_size": 201})
        assert response.status_code == 422
        assert response.json()["detail"][0]["type"] == "less_than_equal"
        assert client.get(path, params={"page_size": 200}).status_code == 200

    @pytest.mark.parametrize("path", ["/depends", "/query"])
    def test_openapi_publishes_the_maximum(self, client: TestClient, path: str) -> None:
        parameters = client.app.openapi()["paths"][path]["get"]["parameters"]  # type: ignore[attr-defined]
        page_size = next(p for p in parameters if p["name"] == "page_size")
        assert page_size["schema"]["maximum"] == 200
