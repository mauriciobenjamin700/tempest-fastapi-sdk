"""Tests for tempest_fastapi_sdk.api.error_docs."""

from typing import Any, ClassVar
from uuid import UUID

import pytest
from fastapi import APIRouter, FastAPI

from tempest_fastapi_sdk import (
    BaseSchema,
    ConflictException,
    ErrorResponseSchema,
    ForbiddenException,
    MessageCatalog,
    NotFoundException,
    TempestAPIRouter,
    ValidationException,
    declared_raises,
    error_responses,
    raises,
)
from tempest_fastapi_sdk.api.error_docs import RAISES_ATTRIBUTE, RaisesSpec


class ServiceNotFoundException(NotFoundException):
    """Service does not exist."""

    code: str = "SERVICE_NOT_FOUND"
    message: str = "Service not found"
    details_example: ClassVar[dict[str, Any]] = {
        "service_id": "8f2c1e40-0000-4000-8000-000000000000"
    }


class CategoryNotFoundException(NotFoundException):
    """Category does not exist."""

    code: str = "CATEGORY_NOT_FOUND"
    message: str = "Category not found"


class ServiceOwnerCannotApplyException(ForbiddenException):
    """The service owner cannot apply to their own job."""

    code: str = "SERVICE_OWNER_CANNOT_APPLY"
    message: str = "The service owner cannot apply"


class ServiceFullException(ConflictException):
    """The service reached its candidate limit."""

    code: str = "SERVICE_FULL"
    message: str = "Service is full"


class CandidateAlreadyExistsException(ConflictException):
    """The user already applied to this service."""

    code: str = "CANDIDATE_ALREADY_EXISTS"
    message: str = "Candidate already exists"


class CandidateDoesNotHaveCoinsException(ValidationException):
    """The user does not have enough coins to apply."""

    code: str = "CANDIDATE_DOES_NOT_HAVE_COINS"
    status_code: int = 400
    message: str = "Not enough coins"


ALL_EXCEPTIONS: tuple[type[NotFoundException] | type[ConflictException], ...] = (
    ServiceNotFoundException,
    CategoryNotFoundException,
    ServiceOwnerCannotApplyException,
    ServiceFullException,
    CandidateAlreadyExistsException,
    CandidateDoesNotHaveCoinsException,
)


class CandidateResponseSchema(BaseSchema):
    """Minimal success payload for the routes under test."""

    id: UUID


class TestErrorResponses:
    """``error_responses`` builds the FastAPI ``responses=`` mapping."""

    def test_empty_call_returns_empty_mapping(self) -> None:
        """No exceptions means no responses, so the call is always safe."""
        assert error_responses() == {}

    def test_groups_by_status_code(self) -> None:
        """Six exceptions over four statuses collapse to four entries."""
        responses = error_responses(*ALL_EXCEPTIONS)
        assert sorted(responses) == [400, 403, 404, 409]

    def test_every_entry_points_at_the_envelope_model(self) -> None:
        """Each status documents the SDK error envelope."""
        responses = error_responses(*ALL_EXCEPTIONS)
        assert all(
            entry["model"] is ErrorResponseSchema for entry in responses.values()
        )

    def test_shared_status_codes_are_split_by_example(self) -> None:
        """Two 404s stay distinguishable through the examples map.

        OpenAPI allows one response object per status, so the codes have
        to live in ``examples`` — documenting only the status would erase
        the difference between the two 404s.
        """
        responses = error_responses(*ALL_EXCEPTIONS)
        examples = responses[404]["content"]["application/json"]["examples"]
        assert list(examples) == ["SERVICE_NOT_FOUND", "CATEGORY_NOT_FOUND"]
        assert examples["SERVICE_NOT_FOUND"]["value"]["code"] == "SERVICE_NOT_FOUND"
        assert examples["CATEGORY_NOT_FOUND"]["value"]["code"] == "CATEGORY_NOT_FOUND"

    def test_description_lists_the_codes(self) -> None:
        """The description names every code sharing the status."""
        responses = error_responses(*ALL_EXCEPTIONS)
        assert (
            responses[409]["description"] == "SERVICE_FULL | CANDIDATE_ALREADY_EXISTS"
        )

    def test_summary_comes_from_the_docstring(self) -> None:
        """The example summary reuses the class docstring's first line."""
        responses = error_responses(ServiceFullException)
        examples = responses[409]["content"]["application/json"]["examples"]
        assert (
            examples["SERVICE_FULL"]["summary"]
            == "The service reached its candidate limit."
        )

    def test_summary_falls_back_to_the_class_name(self) -> None:
        """A docstring-less class still gets a usable summary."""

        class Undocumented(ConflictException):
            code: str = "UNDOCUMENTED"

        responses = error_responses(Undocumented)
        examples = responses[409]["content"]["application/json"]["examples"]
        assert examples["UNDOCUMENTED"]["summary"] == "Undocumented"

    def test_details_example_reaches_the_payload(self) -> None:
        """``details_example`` shows a realistic context payload."""
        responses = error_responses(ServiceNotFoundException)
        examples = responses[404]["content"]["application/json"]["examples"]
        assert examples["SERVICE_NOT_FOUND"]["value"]["details"] == {
            "service_id": "8f2c1e40-0000-4000-8000-000000000000"
        }

    def test_details_example_is_copied_not_shared(self) -> None:
        """Mutating the generated example cannot corrupt the class."""
        responses = error_responses(ServiceNotFoundException)
        examples = responses[404]["content"]["application/json"]["examples"]
        examples["SERVICE_NOT_FOUND"]["value"]["details"]["injected"] = True
        assert "injected" not in ServiceNotFoundException.details_example

    def test_detail_defaults_to_the_class_message(self) -> None:
        """Without a catalog the example shows the literal message.

        Defaulting to no catalog keeps the generated spec free of an
        implicitly chosen language.
        """
        responses = error_responses(ServiceNotFoundException)
        examples = responses[404]["content"]["application/json"]["examples"]
        assert examples["SERVICE_NOT_FOUND"]["value"]["detail"] == "Service not found"

    def test_catalog_localizes_the_detail(self) -> None:
        """A catalog fills ``detail`` so the text is written once."""
        catalog = MessageCatalog(
            {"pt-BR": {"SERVICE_NOT_FOUND": "Serviço não encontrado"}}
        )
        responses = error_responses(
            ServiceNotFoundException, catalog=catalog, locale="pt-BR"
        )
        examples = responses[404]["content"]["application/json"]["examples"]
        assert examples["SERVICE_NOT_FOUND"]["value"]["detail"] == (
            "Serviço não encontrado"
        )

    def test_partial_catalog_falls_back_to_the_message(self) -> None:
        """An unknown code degrades to the literal message.

        Same fallback the runtime handler uses, so a half-translated
        catalog never blanks an example.
        """
        catalog = MessageCatalog({"pt-BR": {"OTHER_CODE": "Outro"}})
        responses = error_responses(
            CategoryNotFoundException, catalog=catalog, locale="pt-BR"
        )
        examples = responses[404]["content"]["application/json"]["examples"]
        assert examples["CATEGORY_NOT_FOUND"]["value"]["detail"] == "Category not found"

    def test_descriptions_override_per_status(self) -> None:
        """A caller can replace the generated description."""
        responses = error_responses(
            ServiceFullException, descriptions={409: "Cannot apply right now"}
        )
        assert responses[409]["description"] == "Cannot apply right now"

    def test_duplicate_classes_are_collapsed(self) -> None:
        """Passing the same class twice yields one example."""
        responses = error_responses(ServiceFullException, ServiceFullException)
        examples = responses[409]["content"]["application/json"]["examples"]
        assert list(examples) == ["SERVICE_FULL"]

    def test_classes_sharing_a_code_both_stay_visible(self) -> None:
        """A colliding code is qualified rather than overwritten."""

        class Narrower(ServiceFullException):
            """A narrowing subclass reusing the parent's code."""

        responses = error_responses(ServiceFullException, Narrower)
        examples = responses[409]["content"]["application/json"]["examples"]
        assert list(examples) == ["SERVICE_FULL", "SERVICE_FULL (Narrower)"]

    def test_instance_is_rejected(self) -> None:
        """Passing an instance fails loudly instead of documenting nothing."""
        with pytest.raises(TypeError, match="not an instance"):
            error_responses(ServiceFullException())  # type: ignore[arg-type]

    def test_non_exception_is_rejected(self) -> None:
        """An unrelated class is a programming error, not an empty schema."""
        with pytest.raises(TypeError, match="AppException subclasses"):
            error_responses(CandidateResponseSchema)  # type: ignore[arg-type]


class TestRaisesDecorator:
    """``raises`` tags the endpoint without wrapping it."""

    def test_returns_the_same_function_object(self) -> None:
        """No wrapper, so FastAPI still sees the original signature."""

        async def endpoint(service_id: UUID) -> None: ...

        decorated = raises(ServiceFullException)(endpoint)
        assert decorated is endpoint

    def test_attaches_the_spec(self) -> None:
        """The declared classes and options are readable afterwards."""

        @raises(ServiceFullException, locale="en-US")
        async def endpoint() -> None: ...

        spec = declared_raises(endpoint)
        assert spec is not None
        assert spec.exceptions == (ServiceFullException,)
        assert spec.locale == "en-US"
        assert spec.catalog is None

    def test_undecorated_endpoint_has_no_spec(self) -> None:
        """``declared_raises`` answers ``None`` for a plain handler."""

        async def endpoint() -> None: ...

        assert declared_raises(endpoint) is None

    def test_foreign_attribute_value_is_ignored(self) -> None:
        """A non-``RaisesSpec`` attribute never reaches the router."""

        async def endpoint() -> None: ...

        setattr(endpoint, RAISES_ATTRIBUTE, "not a spec")
        assert declared_raises(endpoint) is None

    def test_spec_is_immutable(self) -> None:
        """The attached spec cannot be mutated in place."""
        spec = RaisesSpec(exceptions=(ServiceFullException,), catalog=None, locale="en")
        with pytest.raises(AttributeError):
            spec.locale = "pt-BR"  # type: ignore[misc]


def _build_app(router: APIRouter) -> dict[str, Any]:
    """Mount a router on a fresh app and return its OpenAPI document.

    Args:
        router (APIRouter): The router under test.

    Returns:
        dict[str, Any]: The generated OpenAPI schema.
    """
    app = FastAPI()
    app.include_router(router)
    return app.openapi()


class TestOpenAPIIntegration:
    """The declarations reach the generated OpenAPI document."""

    def test_explicit_responses_document_every_status(self) -> None:
        """The gap from the issue closes: 201/422 becomes all six."""
        router: APIRouter = APIRouter()

        @router.post(
            "/{service_id}/candidates",
            status_code=201,
            responses=error_responses(*ALL_EXCEPTIONS),
        )
        async def apply(service_id: UUID) -> CandidateResponseSchema: ...

        spec = _build_app(router)
        statuses = spec["paths"]["/{service_id}/candidates"]["post"]["responses"]
        assert sorted(statuses) == ["201", "400", "403", "404", "409", "422"]

    def test_envelope_is_referenced_from_components(self) -> None:
        """The response body is a ``$ref``, not an inline blob."""
        router: APIRouter = APIRouter()

        @router.get("/x", responses=error_responses(ServiceFullException))
        async def read() -> CandidateResponseSchema: ...

        spec = _build_app(router)
        content = spec["paths"]["/x"]["get"]["responses"]["409"]["content"]
        assert content["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponseSchema"
        }
        assert "ErrorResponseSchema" in spec["components"]["schemas"]

    def test_tempest_router_expands_the_raises_tag(self) -> None:
        """``@raises`` on a ``TempestAPIRouter`` needs no ``responses=``."""
        router: TempestAPIRouter = TempestAPIRouter()

        @router.post("/{service_id}/candidates", status_code=201)
        @raises(*ALL_EXCEPTIONS)
        async def apply(service_id: UUID) -> CandidateResponseSchema: ...

        spec = _build_app(router)
        statuses = spec["paths"]["/{service_id}/candidates"]["post"]["responses"]
        assert sorted(statuses) == ["201", "400", "403", "404", "409", "422"]
        examples = statuses["404"]["content"]["application/json"]["examples"]
        assert list(examples) == ["SERVICE_NOT_FOUND", "CATEGORY_NOT_FOUND"]

    def test_tempest_router_matches_explicit_error_responses(self) -> None:
        """Both layers emit the same document for the same input."""
        plain: APIRouter = APIRouter()
        tempest: TempestAPIRouter = TempestAPIRouter()

        @plain.get("/x", responses=error_responses(*ALL_EXCEPTIONS))
        async def explicit() -> CandidateResponseSchema: ...

        @tempest.get("/x")
        @raises(*ALL_EXCEPTIONS)
        async def decorated() -> CandidateResponseSchema: ...

        assert (
            _build_app(plain)["paths"]["/x"]["get"]["responses"]
            == _build_app(tempest)["paths"]["/x"]["get"]["responses"]
        )

    def test_plain_router_ignores_the_tag(self) -> None:
        """``@raises`` is inert without the SDK router — documented behavior."""
        router: APIRouter = APIRouter()

        @router.get("/x")
        @raises(*ALL_EXCEPTIONS)
        async def read() -> CandidateResponseSchema: ...

        spec = _build_app(router)
        assert sorted(spec["paths"]["/x"]["get"]["responses"]) == ["200"]

    def test_explicit_responses_win_per_status(self) -> None:
        """A hand-written entry overrides the generated one."""
        router: TempestAPIRouter = TempestAPIRouter()

        @router.get("/x", responses={409: {"description": "handwritten"}})
        @raises(ServiceFullException, ServiceNotFoundException)
        async def read() -> CandidateResponseSchema: ...

        responses = _build_app(router)["paths"]["/x"]["get"]["responses"]
        assert responses["409"]["description"] == "handwritten"
        assert responses["404"]["description"] == "SERVICE_NOT_FOUND"

    def test_tempest_router_without_tag_behaves_like_apirouter(self) -> None:
        """An untagged handler is registered unchanged."""
        router: TempestAPIRouter = TempestAPIRouter()

        @router.get("/x")
        async def read() -> CandidateResponseSchema: ...

        spec = _build_app(router)
        assert sorted(spec["paths"]["/x"]["get"]["responses"]) == ["200"]

    def test_nested_include_router_keeps_the_responses(self) -> None:
        """Mounting through an intermediate plain router preserves the docs."""
        inner: TempestAPIRouter = TempestAPIRouter()

        @inner.get("/x")
        @raises(ServiceFullException)
        async def read() -> CandidateResponseSchema: ...

        outer: APIRouter = APIRouter(prefix="/api")
        outer.include_router(inner)
        spec = _build_app(outer)
        assert "409" in spec["paths"]["/api/x"]["get"]["responses"]
