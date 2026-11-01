"""``BaseSearchSchema`` mirrors the repository's ``searchable_columns``.

The repository already refuses a column outside its ``searchable_columns``;
the schema turns the same refusal into a validation error before any query
and publishes the accepted set in the OpenAPI schema, the way
``orderable_columns`` does for ``order_by``.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tempest_fastapi_sdk import BaseSearchSchema, register_exception_handlers
from tempest_fastapi_sdk.exceptions import ValidationException
from tempest_fastapi_sdk.schemas import DEFAULT_MAX_SEARCH_TERM


class _ProductSearch(BaseSearchSchema):
    """Search over two declared columns."""

    searchable_columns = frozenset({"name", "sku"})


class _OtherSearch(BaseSearchSchema):
    """A second subclass with its own set."""

    searchable_columns = frozenset({"title"})


class TestValidation:
    def test_listed_fields_pass(self) -> None:
        assert _ProductSearch(q="x", fields=["name", "sku"]).fields == ["name", "sku"]

    def test_empty_fields_default_to_a_list(self) -> None:
        assert _ProductSearch(q="x").fields == []

    def test_an_unlisted_field_is_refused_with_the_repository_envelope(self) -> None:
        with pytest.raises(ValidationException) as caught:
            _ProductSearch(q="x", fields=["hashed_password"])
        assert caught.value.details == {
            "field": "hashed_password",
            "allowed": ["name", "sku"],
        }

    def test_without_a_declared_set_any_name_passes(self) -> None:
        assert BaseSearchSchema(q="x", fields=["anything"]).fields == ["anything"]

    @pytest.mark.parametrize("term", ["", "x" * (DEFAULT_MAX_SEARCH_TERM + 1)])
    def test_term_length_is_bounded(self, term: str) -> None:
        with pytest.raises(ValidationError):
            BaseSearchSchema(q=term)


class TestOpenAPI:
    def test_declared_set_is_published_as_an_enum(self) -> None:
        items = _ProductSearch.model_json_schema()["properties"]["fields"]["items"]
        assert items == {"type": "string", "enum": ["name", "sku"]}

    def test_subclasses_do_not_share_the_enum(self) -> None:
        items = _OtherSearch.model_json_schema()["properties"]["fields"]["items"]
        assert items["enum"] == ["title"]
        base = BaseSearchSchema.model_json_schema()["properties"]["fields"]["items"]
        assert "enum" not in base


class TestInheritance:
    def test_a_grandchild_with_its_own_set_does_not_leak(self) -> None:
        class _Child(_ProductSearch):
            searchable_columns = frozenset({"title"})

        class _Grandchild(_Child):
            pass

        def enum(cls: type[BaseSearchSchema]) -> list[str]:
            items: list[str] = cls.model_json_schema()["properties"]["fields"]["items"][
                "enum"
            ]
            return items

        assert enum(_Child) == ["title"]
        assert enum(_Grandchild) == ["title"]
        assert enum(_ProductSearch) == ["name", "sku"]
        assert _Grandchild(q="x", fields=["title"]).fields == ["title"]
        with pytest.raises(ValidationException):
            _Grandchild(q="x", fields=["name"])


class TestEndpoint:
    def test_refusal_reaches_the_client_as_a_422(self) -> None:
        app = FastAPI()
        register_exception_handlers(app)

        @app.post("/search")
        async def search(body: _ProductSearch) -> dict[str, list[str]]:
            """Echo the validated fields.

            Args:
                body (_ProductSearch): The request.

            Returns:
                dict[str, list[str]]: The fields.
            """
            return {"fields": body.fields}

        response = TestClient(app).post(
            "/search",
            json={"q": "x", "fields": ["hashed_password"]},
        )
        assert response.status_code == 422
        assert response.json()["details"]["field"] == "hashed_password"
