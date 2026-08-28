"""Tests for tempest_fastapi_sdk.openapi.parse."""

from __future__ import annotations

from typing import Any

import pytest

from tempest_fastapi_sdk.openapi.ir import SchemaIR, SpecIR
from tempest_fastapi_sdk.openapi.loader import SpecError
from tempest_fastapi_sdk.openapi.parse import parse_spec


def _spec(**components: Any) -> dict[str, Any]:
    """Build a minimal document around the given component schemas.

    Args:
        **components (Any): ``components.schemas`` entries.

    Returns:
        dict[str, Any]: A loadable OpenAPI document.
    """
    return {
        "openapi": "3.1.0",
        "info": {"title": "T", "version": "1"},
        "paths": {},
        "components": {"schemas": components},
    }


def _schema(spec: SpecIR, name: str) -> SchemaIR:
    """Return one generated class by name.

    Args:
        spec (SpecIR): The parsed specification.
        name (str): The class name to fetch.

    Returns:
        SchemaIR: The class.
    """
    return next(schema for schema in spec.schemas if schema.name == name)


def _field_annotation(spec: SpecIR, class_name: str, field_name: str) -> str:
    """Return one field's rendered annotation.

    Args:
        spec (SpecIR): The parsed specification.
        class_name (str): The owning class.
        field_name (str): The Python field name.

    Returns:
        str: The rendered annotation.
    """
    schema = _schema(spec, class_name)
    return next(f.annotation for f in schema.fields if f.name == field_name)


class TestTypeMapping:
    """Each documented OpenAPI construct maps to the promised Python type."""

    @pytest.mark.parametrize(
        ("schema", "expected"),
        [
            ({"type": "string"}, "str"),
            ({"type": "integer"}, "int"),
            ({"type": "number"}, "float"),
            ({"type": "boolean"}, "bool"),
            ({"type": "string", "format": "date-time"}, "datetime"),
            ({"type": "string", "format": "date"}, "date"),
            ({"type": "string", "format": "uuid"}, "UUID"),
            ({"type": "string", "format": "email"}, "EmailStr"),
            ({"type": "string", "format": "binary"}, "bytes"),
            ({"type": "string", "format": "decimal"}, "Decimal"),
            ({"type": "string", "format": "unheard-of"}, "str"),
            ({"type": "array", "items": {"type": "integer"}}, "list[int]"),
            ({"type": "object", "additionalProperties": {"type": "int"}}, "dict[str, "),
            ({}, "Any"),
        ],
    )
    def test_scalar_and_container_types(
        self, schema: dict[str, Any], expected: str
    ) -> None:
        """A required property renders the expected annotation."""
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": schema},
                }
            ),
            client_name="t",
        )
        assert _field_annotation(spec, "M", "value").startswith(expected)

    def test_nullable_30_syntax(self) -> None:
        """OpenAPI 3.0 spells optionality with ``nullable: true``."""
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": "string", "nullable": True}},
                }
            ),
            client_name="t",
        )
        assert _field_annotation(spec, "M", "value") == "str | None"

    def test_nullable_31_syntax(self) -> None:
        """OpenAPI 3.1 spells it with a type list containing ``null``."""
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": ["string", "null"]}},
                }
            ),
            client_name="t",
        )
        assert _field_annotation(spec, "M", "value") == "str | None"

    def test_union_renders_a_python_union(self) -> None:
        """``oneOf`` becomes ``A | B``."""
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["value"],
                    "properties": {
                        "value": {"oneOf": [{"type": "string"}, {"type": "integer"}]}
                    },
                }
            ),
            client_name="t",
        )
        assert _field_annotation(spec, "M", "value") == "str | int"

    def test_not_is_unsupported_and_reported(self) -> None:
        """``not`` has no Python equivalent, so it degrades to ``Any``.

        The note is the contract: a wrong schema that looks right would be
        worse than a documented gap.
        """
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"not": {"type": "string"}}},
                }
            ),
            client_name="t",
        )
        assert _field_annotation(spec, "M", "value") == "Any"
        assert any("`not`" in note for note in spec.unsupported)


class TestOptionalityAndDefaults:
    """The repo's collection and optionality rules are honored."""

    def test_optional_scalar_becomes_none(self) -> None:
        """A non-required scalar is ``X | None = None``."""
        spec = parse_spec(
            _spec(M={"type": "object", "properties": {"value": {"type": "string"}}}),
            client_name="t",
        )
        field = _schema(spec, "M").fields[0]
        assert field.annotation == "str | None"
        assert field.default == "None"
        assert field.default_is_factory is False

    def test_optional_collection_defaults_to_empty_list(self) -> None:
        """A non-required array is ``list[X]`` with a factory default.

        The repo rule forbids ``list[X] | None`` — "no matches" is an empty
        list, not a missing value.
        """
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "properties": {
                        "tags": {"type": "array", "items": {"type": "string"}}
                    },
                }
            ),
            client_name="t",
        )
        field = _schema(spec, "M").fields[0]
        assert field.annotation == "list[str]"
        assert field.default == "list"
        assert field.default_is_factory is True

    def test_required_field_keeps_no_default(self) -> None:
        """A required property has no default expression."""
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": "string"}},
                }
            ),
            client_name="t",
        )
        assert _schema(spec, "M").fields[0].default is None

    def test_declared_default_is_carried(self) -> None:
        """A required property with a ``default`` keeps it."""
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": "integer", "default": 7}},
                }
            ),
            client_name="t",
        )
        assert _schema(spec, "M").fields[0].default == "7"


class TestMetadata:
    """The specification's documentation reaches the field, never invented."""

    def test_title_description_and_example_are_captured(self) -> None:
        """3.0's singular ``example`` is normalized into ``examples``."""
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["value"],
                    "properties": {
                        "value": {
                            "type": "string",
                            "title": "Value",
                            "description": "What it means.",
                            "example": "abc",
                        }
                    },
                }
            ),
            client_name="t",
        )
        field = _schema(spec, "M").fields[0]
        assert field.title == "Value"
        assert field.description == "What it means."
        assert field.examples == ("abc",)

    def test_examples_list_is_captured(self) -> None:
        """3.1's plural ``examples`` is used as-is."""
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": "string", "examples": ["a", "b"]}},
                }
            ),
            client_name="t",
        )
        assert _schema(spec, "M").fields[0].examples == ("a", "b")

    def test_absent_documentation_stays_absent(self) -> None:
        """Nothing is invented when the specification documents nothing."""
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": "string"}},
                }
            ),
            client_name="t",
        )
        field = _schema(spec, "M").fields[0]
        assert field.title is None
        assert field.description is None
        assert field.examples == ()
        assert field.has_metadata is False


class TestConstraints:
    """Validation keywords become Pydantic ``Field`` constraints."""

    @pytest.mark.parametrize(
        ("schema", "expected"),
        [
            ({"type": "string", "minLength": 2}, {"min_length": 2}),
            ({"type": "string", "maxLength": 9}, {"max_length": 9}),
            ({"type": "string", "pattern": "^a"}, {"pattern": "^a"}),
            ({"type": "integer", "minimum": 0}, {"ge": 0}),
            ({"type": "integer", "maximum": 5}, {"le": 5}),
            ({"type": "integer", "multipleOf": 3}, {"multiple_of": 3}),
            (
                {"type": "array", "items": {"type": "string"}, "minItems": 1},
                {"min_length": 1},
            ),
        ],
    )
    def test_keywords_map(
        self, schema: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        """Each family of keyword maps to its Pydantic name."""
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": schema},
                }
            ),
            client_name="t",
        )
        assert _schema(spec, "M").fields[0].constraints == expected

    @pytest.mark.parametrize(
        ("schema", "expected"),
        [
            ({"type": "string", "maximum": 140}, {"max_length": 140}),
            ({"type": "string", "minimum": 3}, {"min_length": 3}),
            ({"type": ["string", "null"], "maximum": 140}, {"max_length": 140}),
            (
                {"type": "string", "maxLength": 9, "maximum": 140},
                {"max_length": 9},
            ),
            ({"type": "string", "exclusiveMaximum": 140}, {}),
        ],
    )
    def test_a_numeric_bound_on_a_string_is_read_as_a_length(
        self, schema: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        """The shape that shipped a `TypeError` in the OpenPix client.

        OpenPix's `ChargeRefundPayload.comment` is `type: string` carrying
        `maximum: 140`, under a description that reads "Maximum length of
        140 characters". Emitted literally that is `Field(le=140)` on a
        `str`, and pydantic does not merely reject the value — it raises
        `TypeError: Unable to apply constraint 'le' to supplied value` when
        the model is constructed, so every refund with a comment failed
        before leaving the process.

        A real `maxLength` still wins over the re-read one, and an
        exclusive bound is dropped rather than guessed at.
        """
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": schema},
                }
            ),
            client_name="t",
        )
        assert _schema(spec, "M").fields[0].constraints == expected

    def test_a_numeric_bound_on_a_number_is_untouched(self) -> None:
        """The re-read is scoped to strings — numbers keep `ge`/`le`."""
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["value"],
                    "properties": {
                        "value": {"type": "number", "maximum": 140, "minimum": 1}
                    },
                }
            ),
            client_name="t",
        )
        assert _schema(spec, "M").fields[0].constraints == {"le": 140, "ge": 1}

    def test_openapi_30_boolean_exclusive_minimum(self) -> None:
        """3.0's boolean ``exclusiveMinimum`` re-points ``minimum`` at ``gt``.

        In 3.0 the keyword is a flag qualifying ``minimum``; emitting both
        ``ge`` and a stray boolean would validate the wrong bound.
        """
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["value"],
                    "properties": {
                        "value": {
                            "type": "integer",
                            "minimum": 0,
                            "exclusiveMinimum": True,
                        }
                    },
                }
            ),
            client_name="t",
        )
        assert _schema(spec, "M").fields[0].constraints == {"gt": 0}

    def test_openapi_31_numeric_exclusive_minimum(self) -> None:
        """3.1's numeric form is used directly."""
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": "integer", "exclusiveMinimum": 0}},
                }
            ),
            client_name="t",
        )
        assert _schema(spec, "M").fields[0].constraints == {"gt": 0}


class TestEnums:
    """Enums become their own classes and are de-duplicated."""

    def test_string_enum_component(self) -> None:
        """A bare-enum component becomes a ``str`` enum class."""
        spec = parse_spec(
            _spec(Status={"type": "string", "enum": ["a", "b"]}),
            client_name="t",
        )
        status = _schema(spec, "Status")
        assert status.kind == "str_enum"
        assert status.enum_members == (("A", "a"), ("B", "b"))

    def test_integer_enum_component(self) -> None:
        """An integer enum becomes an ``int`` enum class."""
        spec = parse_spec(
            _spec(Level={"type": "integer", "enum": [1, 2]}),
            client_name="t",
        )
        assert _schema(spec, "Level").kind == "int_enum"

    def test_enum_reached_only_through_a_ref_is_still_an_enum(self) -> None:
        """A ``$ref`` to an enum component must not build an empty model.

        Regression: the referencing model was parsed first, took the
        ``$ref`` down the object path, and produced a field-less
        ``Status`` model — then the top-level sweep registered the real
        enum under the suffixed name ``Status_2``.
        """
        document = _spec(
            M={
                "type": "object",
                "required": ["status"],
                "properties": {"status": {"$ref": "#/components/schemas/Status"}},
            },
            Status={"type": "string", "enum": ["a", "b"]},
        )
        spec = parse_spec(document, client_name="t")
        assert [s.name for s in spec.schemas if s.kind != "model"] == ["Status"]
        assert not any(s.name.endswith("_2") for s in spec.schemas)
        assert _field_annotation(spec, "M", "status") == "Status"

    def test_identical_inline_enums_are_shared(self) -> None:
        """Two identical inline enums yield one class, not two."""
        document = _spec(
            M={
                "type": "object",
                "required": ["a", "b"],
                "properties": {
                    "a": {"type": "string", "enum": ["x", "y"]},
                    "b": {"type": "string", "enum": ["x", "y"]},
                },
            }
        )
        spec = parse_spec(document, client_name="t")
        assert len([s for s in spec.schemas if s.kind == "str_enum"]) == 1
        assert _field_annotation(spec, "M", "a") == _field_annotation(spec, "M", "b")


class TestComposition:
    """``allOf`` is flattened; recursion is representable."""

    def test_all_of_merges_properties_and_required(self) -> None:
        """The merged model carries every member's fields."""
        document = _spec(
            Composed={
                "allOf": [
                    {
                        "type": "object",
                        "required": ["a"],
                        "properties": {"a": {"type": "string"}},
                    },
                    {"type": "object", "properties": {"b": {"type": "integer"}}},
                ]
            }
        )
        spec = parse_spec(document, client_name="t")
        composed = _schema(spec, "Composed")
        assert [f.name for f in composed.fields] == ["a", "b"]
        assert composed.fields[0].required is True
        assert composed.fields[1].required is False

    def test_all_of_through_a_ref_is_flattened(self) -> None:
        """An ``allOf`` member that is a ``$ref`` is resolved and merged."""
        document = _spec(
            Base={
                "type": "object",
                "required": ["a"],
                "properties": {"a": {"type": "string"}},
            },
            Child={
                "allOf": [
                    {"$ref": "#/components/schemas/Base"},
                    {"type": "object", "properties": {"b": {"type": "integer"}}},
                ]
            },
        )
        spec = parse_spec(document, client_name="t")
        assert [f.name for f in _schema(spec, "Child").fields] == ["a", "b"]

    def test_self_reference_produces_one_class(self) -> None:
        """A self-referencing schema does not register a second class."""
        document = _spec(
            Node={
                "type": "object",
                "properties": {
                    "child": {"$ref": "#/components/schemas/Node"},
                    "children": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Node"},
                    },
                },
            }
        )
        spec = parse_spec(document, client_name="t")
        assert [s.name for s in spec.schemas] == ["Node"]
        assert _field_annotation(spec, "Node", "child") == "Node | None"

    def test_mutual_reference_is_reported_as_cyclic(self) -> None:
        """A cycle is reported so the emitter can call ``model_rebuild()``."""
        document = _spec(
            A={
                "type": "object",
                "properties": {"b": {"$ref": "#/components/schemas/B"}},
            },
            B={
                "type": "object",
                "properties": {"a": {"$ref": "#/components/schemas/A"}},
            },
        )
        spec = parse_spec(document, client_name="t")
        assert spec.cyclic == {"A", "B"}

    def test_dependencies_order_the_output(self) -> None:
        """A class is emitted after the ones it depends on."""
        document = _spec(
            Outer={
                "type": "object",
                "required": ["inner"],
                "properties": {"inner": {"$ref": "#/components/schemas/Inner"}},
            },
            Inner={"type": "object", "properties": {"x": {"type": "string"}}},
        )
        spec = parse_spec(document, client_name="t")
        names = [s.name for s in spec.schemas]
        assert names.index("Inner") < names.index("Outer")


class TestAliases:
    """Wire names survive as aliases, Python names stay idiomatic."""

    def test_camel_case_gets_an_alias(self) -> None:
        """A differing wire name is attached as ``alias``."""
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["createdAt"],
                    "properties": {"createdAt": {"type": "string"}},
                }
            ),
            client_name="t",
        )
        field = _schema(spec, "M").fields[0]
        assert field.name == "created_at"
        assert field.alias == "createdAt"

    def test_matching_name_gets_no_alias(self) -> None:
        """An already-snake_case name needs no alias."""
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["created_at"],
                    "properties": {"created_at": {"type": "string"}},
                }
            ),
            client_name="t",
        )
        assert _schema(spec, "M").fields[0].alias is None

    def test_reserved_word_gets_an_alias(self) -> None:
        """``class`` becomes ``class_`` with the wire name preserved."""
        spec = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["class"],
                    "properties": {"class": {"type": "string"}},
                }
            ),
            client_name="t",
        )
        field = _schema(spec, "M").fields[0]
        assert field.name == "class_"
        assert field.alias == "class"

    def test_populate_by_name_is_required_only_with_aliases(self) -> None:
        """The config opt-in follows the presence of an alias."""
        aliased = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["createdAt"],
                    "properties": {"createdAt": {"type": "string"}},
                }
            ),
            client_name="t",
        )
        plain = parse_spec(
            _spec(
                M={
                    "type": "object",
                    "required": ["created_at"],
                    "properties": {"created_at": {"type": "string"}},
                }
            ),
            client_name="t",
        )
        assert _schema(aliased, "M").needs_populate_by_name is True
        assert _schema(plain, "M").needs_populate_by_name is False


class TestOperations:
    """``paths`` becomes the client's operations."""

    def test_billing_operations(self, billing_document: dict[str, Any]) -> None:
        """Every documented operation becomes a method."""
        spec = parse_spec(billing_document, client_name="billing")
        assert [operation.name for operation in spec.client.operations] == [
            "list_customers",
            "create_customer",
            "get_customer",
            "delete_customers_by_customer_id",
        ]

    def test_client_metadata(self, billing_document: dict[str, Any]) -> None:
        """Title, version and base URL come from the specification."""
        client = parse_spec(billing_document, client_name="billing").client
        assert client.class_name == "BillingClient"
        assert client.title == "Billing API"
        assert client.version == "2.1.0"
        assert client.base_url == "https://api.billing.example.com/v2"

    def test_path_and_query_parameters_are_split(
        self, billing_document: dict[str, Any]
    ) -> None:
        """Path parameters are positional; query parameters are keyword."""
        spec = parse_spec(billing_document, client_name="billing")
        listing = next(o for o in spec.client.operations if o.name == "list_customers")
        detail = next(o for o in spec.client.operations if o.name == "get_customer")
        assert [p.name for p in listing.query_parameters] == ["page_size", "status"]
        assert listing.path_parameters == ()
        assert [p.name for p in detail.path_parameters] == ["customer_id"]
        assert detail.path_parameters[0].annotation == "UUID"

    def test_header_parameter_becomes_an_argument(
        self, billing_document: dict[str, Any]
    ) -> None:
        """A declared header is a per-call argument, not a default header.

        Routing it through ``HTTPClient.default_headers`` would send one
        fixed value for every call. For ``X-Idempotency-Key`` — the header
        that motivated this — that is not a limitation but a defect: the
        second charge would be deduplicated onto the first.
        """
        spec = parse_spec(billing_document, client_name="billing")
        listing = next(o for o in spec.client.operations if o.name == "list_customers")

        header = next(p for p in listing.header_parameters if p.wire_name == "X-Trace")
        assert header.name == "x_trace"
        assert header.required is False
        assert header.annotation == "str | None"
        assert not any("X-Trace" in note for note in spec.unsupported)

    def test_headers_come_after_query_parameters(
        self, billing_document: dict[str, Any]
    ) -> None:
        """Headers sort last, so adding one cannot reorder an existing call.

        Every one of these is keyword-only, so the order is cosmetic for
        callers — but it keeps the generated diff of an unrelated
        specification change small.
        """
        spec = parse_spec(billing_document, client_name="billing")
        listing = next(o for o in spec.client.operations if o.name == "list_customers")

        assert [p.name for p in listing.parameters] == [
            "page_size",
            "status",
            "x_trace",
        ]

    def test_cookie_parameter_is_still_skipped_with_a_note(
        self, billing_document: dict[str, Any]
    ) -> None:
        """A cookie is connection state, not a per-call value.

        Headers stopped being skipped; cookies did not, and the note has to
        keep naming the one that was dropped.
        """
        spec = parse_spec(billing_document, client_name="billing")
        listing = next(o for o in spec.client.operations if o.name == "list_customers")

        assert all(p.wire_name != "sessionHint" for p in listing.parameters)
        assert any("sessionHint" in note for note in spec.unsupported)

    def test_body_and_response_types(self, billing_document: dict[str, Any]) -> None:
        """Request body and success response resolve to generated classes."""
        spec = parse_spec(billing_document, client_name="billing")
        create = next(o for o in spec.client.operations if o.name == "create_customer")
        assert create.body_annotation == "CustomerCreate"
        assert create.body_required is True
        assert create.response_annotation == "Customer"
        assert create.success_status == "201"

    def test_array_response(self, billing_document: dict[str, Any]) -> None:
        """A JSON array response renders as ``list[Model]``."""
        spec = parse_spec(billing_document, client_name="billing")
        listing = next(o for o in spec.client.operations if o.name == "list_customers")
        assert listing.response_annotation == "list[Customer]"

    def test_no_content_response(self, billing_document: dict[str, Any]) -> None:
        """A 204 operation returns nothing."""
        spec = parse_spec(billing_document, client_name="billing")
        delete = next(o for o in spec.client.operations if o.name.startswith("delete_"))
        assert delete.response_annotation is None
        assert delete.success_status == "204"

    def test_error_statuses_are_collected(
        self, billing_document: dict[str, Any]
    ) -> None:
        """Documented 4xx codes reach the docstring data."""
        spec = parse_spec(billing_document, client_name="billing")
        create = next(o for o in spec.client.operations if o.name == "create_customer")
        assert create.error_statuses == (("409", "Email already registered"),)

    def test_shared_path_parameters_are_inherited(self) -> None:
        """A parameter declared on the path item applies to each method."""
        document = {
            "openapi": "3.1.0",
            "info": {"title": "T", "version": "1"},
            "paths": {
                "/things/{id}": {
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "get": {"operationId": "getThing", "responses": {}},
                }
            },
        }
        spec = parse_spec(document, client_name="t")
        assert [p.name for p in spec.client.operations[0].path_parameters] == ["id"]

    def test_non_json_body_is_reported(self) -> None:
        """A form-encoded body is not modelled, and says so."""
        document = {
            "openapi": "3.1.0",
            "info": {"title": "T", "version": "1"},
            "paths": {
                "/upload": {
                    "post": {
                        "operationId": "upload",
                        "requestBody": {
                            "content": {"multipart/form-data": {"schema": {}}}
                        },
                        "responses": {},
                    }
                }
            },
        }
        spec = parse_spec(document, client_name="t")
        assert spec.client.operations[0].body_annotation is None
        assert any("multipart/form-data" in note for note in spec.unsupported)


class TestEmptyDocument:
    """A document with nothing to generate fails instead of writing nothing."""

    def test_no_schemas_and_no_paths_is_an_error(self) -> None:
        """An empty output directory is a worse answer than an error."""
        document = {"openapi": "3.1.0", "info": {"title": "T", "version": "1"}}
        with pytest.raises(SpecError, match="nothing to generate"):
            parse_spec(document, client_name="t")

    def test_paths_without_schemas_is_fine(self) -> None:
        """A specification of only inline types still generates a client."""
        document = {
            "openapi": "3.1.0",
            "info": {"title": "T", "version": "1"},
            "paths": {"/ping": {"get": {"operationId": "ping", "responses": {}}}},
        }
        spec = parse_spec(document, client_name="t")
        assert spec.schemas == ()
        assert len(spec.client.operations) == 1


class TestCombinatorComponents:
    """A component whose top level is ``oneOf`` keeps its data.

    Before this, such a component became a class with no fields, and
    ``BaseSchema``'s ``extra="ignore"`` then dropped everything a caller
    passed to it — a charge went out with ``"customer": {}`` and no error
    anywhere. Each test below fails on that shape.
    """

    def test_untitled_object_variants_merge_into_one_model(self) -> None:
        """The same payload described three times is one model."""
        document = _spec(
            CustomerPayload={
                "description": "name plus one of taxID, email or phone.",
                "oneOf": [
                    {
                        "type": "object",
                        "required": ["name", "taxID"],
                        "properties": {
                            "name": {"type": "string"},
                            "taxID": {"type": "string"},
                        },
                    },
                    {
                        "type": "object",
                        "required": ["name", "email"],
                        "properties": {
                            "name": {"type": "string"},
                            "email": {"type": "string"},
                        },
                    },
                ],
            }
        )
        spec = parse_spec(document, client_name="t")
        merged = _schema(spec, "CustomerPayload")
        assert merged.kind == "model"
        assert [f.name for f in merged.fields] == ["name", "tax_id", "email"]

    def test_merged_required_is_the_intersection(self) -> None:
        """Only what every variant demands stays required.

        The union would demand fields a caller can never satisfy together:
        ``taxID`` belongs to one variant and ``email`` to another, so
        requiring both leaves no valid payload at all.
        """
        document = _spec(
            CustomerPayload={
                "oneOf": [
                    {
                        "type": "object",
                        "required": ["name", "taxID"],
                        "properties": {
                            "name": {"type": "string"},
                            "taxID": {"type": "string"},
                        },
                    },
                    {
                        "type": "object",
                        "required": ["name", "email"],
                        "properties": {
                            "name": {"type": "string"},
                            "email": {"type": "string"},
                        },
                    },
                ],
            }
        )
        spec = parse_spec(document, client_name="t")
        fields = _schema(spec, "CustomerPayload").fields
        required = {f.name for f in fields if f.required}
        assert required == {"name"}

    def test_titled_variants_become_a_union_alias(self) -> None:
        """A real sum type keeps its variants apart, named by their title."""
        document = _spec(
            PaymentCreatePayload={
                "oneOf": [
                    {
                        "title": "Pix key",
                        "type": "object",
                        "required": ["destinationAlias"],
                        "properties": {"destinationAlias": {"type": "string"}},
                    },
                    {
                        "title": "QR Code",
                        "type": "object",
                        "required": ["qrCode"],
                        "properties": {"qrCode": {"type": "string"}},
                    },
                ],
            }
        )
        spec = parse_spec(document, client_name="t")
        alias = _schema(spec, "PaymentCreatePayload")
        assert alias.kind == "alias"
        assert alias.alias_target == (
            "PaymentCreatePayloadPixKey | PaymentCreatePayloadQrCode"
        )
        assert {s.name for s in spec.schemas} == {
            "PaymentCreatePayload",
            "PaymentCreatePayloadPixKey",
            "PaymentCreatePayloadQrCode",
        }

    def test_the_alias_is_emitted_after_its_variants(self) -> None:
        """``A | B`` is evaluated on import, so it must follow A and B."""
        document = _spec(
            Sum={
                "oneOf": [
                    {
                        "title": "One",
                        "type": "object",
                        "properties": {"a": {"type": "string"}},
                    },
                    {
                        "title": "Two",
                        "type": "object",
                        "properties": {"b": {"type": "string"}},
                    },
                ]
            }
        )
        spec = parse_spec(document, client_name="t")
        names = [s.name for s in spec.schemas]
        assert names.index("SumOne") < names.index("Sum")
        assert names.index("SumTwo") < names.index("Sum")

    def test_all_of_over_a_union_is_distributed(self) -> None:
        """``allOf: [sum type, {flag}]`` gives every variant the flag.

        A flat merge asks the union for ``properties``, finds none, and the
        body collapses to the flag alone — which is how OpenPix's payment
        endpoint became unable to express a payment.
        """
        document = _spec(
            PaymentCreatePayload={
                "oneOf": [
                    {
                        "title": "Pix key",
                        "type": "object",
                        "required": ["destinationAlias"],
                        "properties": {"destinationAlias": {"type": "string"}},
                    },
                    {
                        "title": "QR Code",
                        "type": "object",
                        "required": ["qrCode"],
                        "properties": {"qrCode": {"type": "string"}},
                    },
                ],
            },
            PaymentBody={
                "type": "object",
                "allOf": [
                    {"$ref": "#/components/schemas/PaymentCreatePayload"},
                    {
                        "type": "object",
                        "properties": {"autoApprove": {"type": "boolean"}},
                    },
                ],
            },
        )
        spec = parse_spec(document, client_name="t")
        body = _schema(spec, "PaymentBody")
        assert body.kind == "alias"
        assert body.alias_target == "PaymentBodyPixKey | PaymentBodyQrCode"
        pix_key = _schema(spec, "PaymentBodyPixKey")
        assert [f.name for f in pix_key.fields] == ["destination_alias", "auto_approve"]

    def test_a_self_referencing_union_does_not_recurse_forever(self) -> None:
        """A component that unions itself renders as ``Any``, with a note."""
        document = _spec(
            Loop={
                "oneOf": [
                    {"title": "Self", "$ref": "#/components/schemas/Loop"},
                    {
                        "title": "Leaf",
                        "type": "object",
                        "properties": {"a": {"type": "string"}},
                    },
                ]
            }
        )
        spec = parse_spec(document, client_name="t")
        assert any("self-referencing" in note for note in spec.unsupported)


class TestTaggedUnionComponents:
    """A discriminated union component stays inline rather than aliased.

    ``Annotated[A | B, Field(discriminator="kind")]`` carries a ``|``
    **inside** brackets, and the emitter wraps a long alias by splitting on
    that character. Naming it would emit code the formatter then rewrites,
    so the tag wins and the name is dropped.
    """

    def test_a_discriminated_union_is_not_given_an_alias(self) -> None:
        """The field renders the tagged union directly."""
        document = _spec(
            Cat={
                "type": "object",
                "required": ["kind"],
                "properties": {"kind": {"type": "string"}, "meow": {"type": "string"}},
            },
            Dog={
                "type": "object",
                "required": ["kind"],
                "properties": {"kind": {"type": "string"}, "bark": {"type": "string"}},
            },
            Pet={
                "oneOf": [
                    {"$ref": "#/components/schemas/Cat"},
                    {"$ref": "#/components/schemas/Dog"},
                ],
                "discriminator": {"propertyName": "kind"},
            },
            Owner={
                "type": "object",
                "properties": {"pet": {"$ref": "#/components/schemas/Pet"}},
            },
        )
        spec = parse_spec(document, client_name="t")
        assert not [schema for schema in spec.schemas if schema.kind == "alias"]
        assert _field_annotation(spec, "Owner", "pet") == (
            'Annotated[Cat | Dog, Field(discriminator="kind")] | None'
        )


class TestResponseModelsKeepWhatTheSpecOmitted:
    """A response model carries the field the document did not predict.

    A third-party API adds a field without asking. With ``extra="ignore"``
    the value is not merely undeclared, it is **gone** — measured twice in
    this repository: Mercado Pago's ``point_of_interaction`` (the Pix QR,
    which is why ``parse_pix_payment`` exists) and OpenPix's ``Charge.fee``,
    which a consumer was persisting into a ledger and would have written as
    zero on every row.

    Payload models are left alone: there an unexpected key is the caller's
    own typo, and forwarding it to the provider is worse than dropping it.
    """

    @staticmethod
    def _document() -> dict[str, Any]:
        """Build a document with one response schema and one payload schema.

        Returns:
            dict[str, Any]: A loadable OpenAPI document where ``Charge``
            answers a ``GET``, ``Customer`` is reached only through
            ``Charge``, and ``ChargePayload`` is only ever sent.
        """
        return {
            "openapi": "3.0.3",
            "info": {"title": "T", "version": "1"},
            "paths": {
                "/charge": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "$ref": "#/components/schemas/Charge"
                                        }
                                    }
                                },
                            }
                        }
                    },
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ChargePayload"
                                    }
                                }
                            }
                        },
                        "responses": {"201": {"description": "created"}},
                    },
                }
            },
            "components": {
                "schemas": {
                    "Charge": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "integer"},
                            "customer": {"$ref": "#/components/schemas/Customer"},
                        },
                    },
                    "Customer": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    },
                    "ChargePayload": {
                        "type": "object",
                        "properties": {"correlationID": {"type": "string"}},
                    },
                }
            },
        }

    def test_a_response_schema_allows_extras(self) -> None:
        """The class the operation answers with keeps the unexpected key."""
        spec = parse_spec(self._document(), client_name="t")

        assert _schema(spec, "Charge").reached_by_response
        assert 'extra="allow"' in _schema(spec, "Charge").config_arguments

    def test_a_nested_schema_is_reached_too(self) -> None:
        """The dropped field hides in the nested object, so it goes deep."""
        spec = parse_spec(self._document(), client_name="t")

        assert _schema(spec, "Customer").reached_by_response

    def test_a_payload_schema_is_left_alone(self) -> None:
        """An unexpected key on the way out is the caller's own typo."""
        spec = parse_spec(self._document(), client_name="t")

        payload = _schema(spec, "ChargePayload")
        assert not payload.reached_by_response
        assert 'extra="allow"' not in payload.config_arguments

    def test_populate_by_name_still_comes_first(self) -> None:
        """Both keywords land on one `ConfigDict`, in a stable order."""
        spec = parse_spec(self._document(), client_name="t")

        assert _schema(spec, "ChargePayload").config_arguments == (
            "populate_by_name=True",
        )
        assert _schema(spec, "Charge").config_arguments == ('extra="allow"',)


class TestPathTemplateGaps:
    """A parameter that can never reach the URL is named, not dropped."""

    def test_a_declared_path_parameter_absent_from_the_template_is_marked(
        self,
    ) -> None:
        """The shape that shipped as `get_api_v1_account_register`.

        OpenPix declared `CorrelationID` as `in: path` on
        `/api/v1/account-register`, a template with no placeholder. The
        parser skipped it and noted why, but the note reached only the
        summary — `_build_parameters` ran outside the operation's capture —
        so the generated method took no arguments while its own docstring
        said "Get account register by CorrelationID".
        """
        document: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/api/v1/account-register": {
                    "get": {
                        "parameters": [
                            {
                                "name": "CorrelationID",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
            "components": {"schemas": {}},
        }

        spec = parse_spec(document, client_name="t")
        operation = spec.client.operations[0]

        assert not operation.parameters
        assert any("CorrelationID" in note for note in operation.unsupported)

    def test_express_syntax_in_a_path_is_marked(self) -> None:
        """The shape that shipped as `post_api_v1_dispute_id_evidence`.

        OpenPix published `/api/v1/dispute/:id/evidence`. A colon is not
        OpenAPI templating, so the generator emitted the segment literally
        and the request went to a URL containing `:id` — with no argument
        anywhere to name the dispute.
        """
        document: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/api/v1/dispute/:id/evidence": {
                    "post": {"responses": {"200": {"description": "ok"}}}
                }
            },
            "components": {"schemas": {}},
        }

        spec = parse_spec(document, client_name="t")
        operation = spec.client.operations[0]

        assert any("Express syntax" in note for note in operation.unsupported)

    def test_a_well_formed_path_is_not_marked(self) -> None:
        """The guard must stay quiet on the ordinary case."""
        document: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/api/v1/dispute/{id}/evidence": {
                    "post": {
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
            "components": {"schemas": {}},
        }

        spec = parse_spec(document, client_name="t")
        operation = spec.client.operations[0]

        assert [p.name for p in operation.parameters] == ["id"]
        assert operation.unsupported == ()


class TestPayloadModelsNeverAllowExtra:
    """A class the caller fills in keeps dropping what it was not given."""

    def test_a_class_used_as_body_and_response_stays_ignore(self) -> None:
        """The shape that shipped as `PreRegistrationPayloadObject`.

        It is the request body and the `200` of the same operation, so the
        response closure reached it and it was emitted with
        `extra="allow"` — and a typo in the caller's dictionary travelled
        to the provider verbatim. Body reachability wins: carrying a typo
        to a payment provider is worse than dropping a field nobody
        declared.
        """
        document: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/thing": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Thing"}
                                }
                            }
                        },
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Thing"}
                                    }
                                },
                            }
                        },
                    }
                }
            },
            "components": {
                "schemas": {
                    "Thing": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    }
                }
            },
        }

        spec = parse_spec(document, client_name="t")
        thing = next(s for s in spec.schemas if s.name == "Thing")

        assert not thing.reached_by_response
        assert 'extra="allow"' not in thing.config_arguments

    def test_a_response_only_class_still_allows_extra(self) -> None:
        """Subtracting the body closure must not disarm the feature."""
        document: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/thing": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Thing"}
                                    }
                                },
                            }
                        }
                    }
                }
            },
            "components": {
                "schemas": {
                    "Thing": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    }
                }
            },
        }

        spec = parse_spec(document, client_name="t")
        thing = next(s for s in spec.schemas if s.name == "Thing")

        assert thing.reached_by_response
        assert 'extra="allow"' in thing.config_arguments
