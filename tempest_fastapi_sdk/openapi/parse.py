"""Turn an OpenAPI document into the intermediate representation.

Every OpenAPI quirk is absorbed here — ``allOf`` flattening, the two ways
3.0 and 3.1 spell "nullable", inline object schemas that need a synthesized
name, ``$ref`` cycles — so the emitters only ever see
:mod:`tempest_fastapi_sdk.openapi.ir` dataclasses.

The parser's contract on anything it cannot represent: never guess. It
falls back to ``Any``, records a human-readable note in
:attr:`~tempest_fastapi_sdk.openapi.ir.SpecIR.unsupported`, and lets the
command surface it. A wrong schema that looks right is worse than a
documented gap.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from tempest_fastapi_sdk.openapi.ir import (
    ClientIR,
    FieldIR,
    OperationIR,
    ParameterIR,
    SchemaIR,
    SpecIR,
)
from tempest_fastapi_sdk.openapi.loader import SpecError, deref
from tempest_fastapi_sdk.openapi.naming import (
    enum_member_name,
    field_name,
    method_name,
    to_pascal,
    unique,
)

_HTTP_METHODS: tuple[str, ...] = (
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "trace",
)

_FORMAT_TYPES: dict[str, str] = {
    "date-time": "datetime",
    "date": "date",
    "time": "time",
    "uuid": "UUID",
    "binary": "bytes",
    "byte": "bytes",
    "email": "EmailStr",
    "uri": "str",
    "url": "str",
    "hostname": "str",
    "ipv4": "str",
    "ipv6": "str",
    "decimal": "Decimal",
}
"""``format`` values mapped to a richer Python type than the base type."""

_PRIMITIVE_TYPES: dict[str, str] = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
}

_STRING_CONSTRAINTS: dict[str, str] = {
    "minLength": "min_length",
    "maxLength": "max_length",
    "pattern": "pattern",
}

_NUMERIC_CONSTRAINTS: dict[str, str] = {
    "minimum": "ge",
    "maximum": "le",
    "exclusiveMinimum": "gt",
    "exclusiveMaximum": "lt",
    "multipleOf": "multiple_of",
}

_ARRAY_CONSTRAINTS: dict[str, str] = {
    "minItems": "min_length",
    "maxItems": "max_length",
}


class _Parser:
    """Single-use parser holding the naming and note state for one document.

    A class rather than free functions because every step needs the same
    three pieces of mutable state — the document, the set of class names
    already taken, and the notes collected so far — and threading them
    through a dozen signatures would obscure the logic.
    """

    def __init__(self, document: Mapping[str, Any], *, client_name: str) -> None:
        """Initialize the parser.

        Args:
            document (Mapping[str, Any]): The loaded OpenAPI document.
            client_name (str): Base name for the generated client class.
        """
        self.document: Mapping[str, Any] = document
        self.client_name: str = client_name
        self.taken_class_names: set[str] = set()
        self.schemas: dict[str, SchemaIR] = {}
        self.wire_to_class: dict[str, str] = {}
        self.notes: list[str] = []
        self.imports: set[str] = set()

    def note(self, message: str) -> None:
        """Record an unsupported construct, de-duplicated.

        Args:
            message (str): Human-readable description of the gap.
        """
        if message not in self.notes:
            self.notes.append(message)

    # -- type rendering ---------------------------------------------------

    def _nullable(self, schema: Mapping[str, Any]) -> bool:
        """Report whether a schema admits ``null``.

        OpenAPI 3.0 spells this ``nullable: true``; 3.1 uses a type list
        containing ``"null"``. Both are accepted.

        Args:
            schema (Mapping[str, Any]): The schema fragment.

        Returns:
            bool: ``True`` when ``null`` is a valid value.
        """
        if schema.get("nullable") is True:
            return True
        raw_type = schema.get("type")
        return isinstance(raw_type, list) and "null" in raw_type

    def _base_type(self, schema: Mapping[str, Any]) -> str | None:
        """Return the schema's non-null ``type``, if it declares one.

        Args:
            schema (Mapping[str, Any]): The schema fragment.

        Returns:
            str | None: The type name, with ``"null"`` filtered out of a
            3.1 type list. ``None`` when no type is declared.
        """
        raw_type = schema.get("type")
        if isinstance(raw_type, str):
            return raw_type
        if isinstance(raw_type, list):
            concrete = [item for item in raw_type if item != "null"]
            if len(concrete) == 1:
                return str(concrete[0])
            if concrete:
                self.note(
                    f"union type {raw_type!r} rendered as Any (multi-type "
                    f"schemas are not modelled)"
                )
        return None

    def _register_import(self, annotation: str) -> None:
        """Record the imports a rendered annotation needs.

        Args:
            annotation (str): A rendered type annotation.
        """
        for name in ("datetime", "date", "time", "UUID", "Decimal", "EmailStr", "Any"):
            if name in annotation:
                self.imports.add(name)

    def render_type(
        self,
        schema: Mapping[str, Any],
        *,
        hint: str,
    ) -> str:
        """Render a schema fragment as a Python type annotation.

        Args:
            schema (Mapping[str, Any]): The schema fragment, possibly a
                ``$ref``.
            hint (str): Name used when an inline schema has to be promoted
                to its own class (``"UserAddress"``).

        Returns:
            str: The annotation as source text. Falls back to ``"Any"``
            for anything unrepresentable, always with a recorded note.
        """
        annotation = self._render_type(schema, hint=hint)
        self._register_import(annotation)
        return annotation

    def _render_type(self, schema: Mapping[str, Any], *, hint: str) -> str:
        """Render a type without registering imports (see :meth:`render_type`)."""
        ref = schema.get("$ref")
        if isinstance(ref, str):
            return self._render_ref(ref, hint=hint)

        for combinator in ("oneOf", "anyOf"):
            members = schema.get(combinator)
            if isinstance(members, list) and members:
                return self._render_union(
                    members,
                    combinator,
                    hint=hint,
                    discriminator=schema.get("discriminator"),
                )

        all_of = schema.get("allOf")
        if isinstance(all_of, list) and all_of:
            merged = self._flatten_all_of(all_of, hint=hint)
            return self._render_type(merged, hint=hint)

        if "not" in schema:
            self.note(f"`not` in {hint} rendered as Any (no Python equivalent)")
            return "Any"

        nullable = self._nullable(schema)
        base = self._render_concrete(schema, hint=hint)
        return f"{base} | None" if nullable and base != "Any" else base

    def _render_concrete(self, schema: Mapping[str, Any], *, hint: str) -> str:
        """Render a schema that is neither a ``$ref`` nor a combinator.

        An empty schema (`{}`) is not a gap in the specification — in
        OpenAPI it legitimately means "any JSON value" — so it renders as
        ``Any`` without a note.

        Args:
            schema (Mapping[str, Any]): The schema fragment.
            hint (str): Name used when an inline schema is promoted.

        Returns:
            str: The rendered annotation.
        """
        enum_values = schema.get("enum")
        base_type = self._base_type(schema)

        if isinstance(enum_values, list) and enum_values:
            return self._render_enum(enum_values, base_type, hint=hint)

        if base_type == "array":
            items = schema.get("items")
            if not isinstance(items, dict):
                self.note(f"array without `items` in {hint} rendered as list[Any]")
                return "list[Any]"
            return f"list[{self.render_type(items, hint=f'{hint}Item')}]"

        if base_type == "object" or "properties" in schema:
            return self._render_object(schema, hint=hint)

        if base_type in _PRIMITIVE_TYPES:
            fmt = schema.get("format")
            if isinstance(fmt, str) and fmt in _FORMAT_TYPES:
                return _FORMAT_TYPES[fmt]
            return _PRIMITIVE_TYPES[base_type]

        if base_type is None and not schema:
            return "Any"

        if base_type is not None:
            self.note(f"unknown type {base_type!r} in {hint} rendered as Any")
        return "Any"

    def _render_ref(self, ref: str, *, hint: str) -> str:
        """Render a ``$ref`` as the class name it points at."""
        target = deref(self.document, {"$ref": ref})
        if ref.startswith("#/components/schemas/"):
            wire_name = ref.rsplit("/", 1)[-1]
            return self.ensure_component(wire_name, target)
        return self._render_type(target, hint=hint)

    def _render_union(
        self,
        members: list[Any],
        combinator: str,
        *,
        hint: str,
        discriminator: object = None,
    ) -> str:
        """Render ``oneOf``/``anyOf`` as a Python union.

        Args:
            members (list[Any]): The combinator's member schemas.
            combinator (str): ``"oneOf"`` or ``"anyOf"``, for notes.
            hint (str): Name used for promoted inline members.
            discriminator (object): The schema's ``discriminator`` object,
                when present. A tagged union is rendered as
                ``Annotated[A | B, Field(discriminator="kind")]`` so
                pydantic dispatches on the tag instead of trying each
                member in order — that turns an ambiguous payload into a
                precise error and is much faster to validate.

        Returns:
            str: The rendered annotation.
        """
        rendered: list[str] = []
        nullable = False
        for index, member in enumerate(members):
            if not isinstance(member, dict):
                continue
            if member.get("type") == "null":
                nullable = True
                continue
            annotation = self._render_type(member, hint=f"{hint}Variant{index + 1}")
            if annotation not in rendered:
                rendered.append(annotation)
        if not rendered:
            self.note(f"empty {combinator} in {hint} rendered as Any")
            return "Any"

        union = " | ".join(rendered)
        tag = self._discriminator_property(discriminator, rendered, hint=hint)
        if tag is not None:
            self.imports.add("Annotated")
            self.imports.add("Field")
            union = f'Annotated[{union}, Field(discriminator="{tag}")]'
        if nullable:
            union = f"{union} | None"
        return union

    def _discriminator_property(
        self,
        discriminator: object,
        rendered: list[str],
        *,
        hint: str,
    ) -> str | None:
        """Return the discriminator property name, when usable.

        Args:
            discriminator (object): The raw ``discriminator`` object.
            rendered (list[str]): The rendered member annotations.
            hint (str): Name used for notes.

        Returns:
            str | None: The ``propertyName``, or ``None`` when there is no
            discriminator or it cannot be applied. Pydantic requires every
            member of a tagged union to be a model with the tag field, so
            a union that still contains a primitive is left untagged (with
            a note) rather than emitting code that fails at import.
        """
        if not isinstance(discriminator, dict):
            return None
        property_name = discriminator.get("propertyName")
        if not isinstance(property_name, str) or not property_name:
            self.note(f"discriminator without propertyName in {hint} ignored")
            return None
        if len(rendered) < 2:
            return None
        if not all(name in self.schemas for name in rendered):
            self.note(
                f"discriminator {property_name!r} in {hint} ignored — every "
                f"member of a tagged union must be a generated model"
            )
            return None
        return field_name(property_name)

    def _flatten_all_of(
        self,
        members: list[Any],
        *,
        hint: str,
    ) -> dict[str, Any]:
        """Merge ``allOf`` members into a single object schema.

        Args:
            members (list[Any]): The ``allOf`` entries.
            hint (str): Name used for notes.

        Returns:
            dict[str, Any]: One schema whose ``properties`` and
            ``required`` are the union of every member's. Later members
            win on a property collision, matching how OpenAPI authors use
            ``allOf`` to specialize a base.
        """
        merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        for member in members:
            if not isinstance(member, dict):
                continue
            resolved = deref(self.document, member)
            nested = resolved.get("allOf")
            if isinstance(nested, list) and nested:
                resolved = self._flatten_all_of(nested, hint=hint)
            properties = resolved.get("properties")
            if isinstance(properties, dict):
                merged["properties"].update(properties)
            required = resolved.get("required")
            if isinstance(required, list):
                merged["required"].extend(str(item) for item in required)
            for carried in ("description", "title"):
                if carried in resolved and carried not in merged:
                    merged[carried] = resolved[carried]
        merged["required"] = sorted(set(merged["required"]))
        return merged

    def _render_object(self, schema: Mapping[str, Any], *, hint: str) -> str:
        """Render an object schema, promoting inline ones to their own class."""
        properties = schema.get("properties")
        if not isinstance(properties, dict) or not properties:
            additional = schema.get("additionalProperties")
            if isinstance(additional, dict):
                value = self.render_type(additional, hint=f"{hint}Value")
                return f"dict[str, {value}]"
            return "dict[str, Any]"
        return self.ensure_inline(hint, schema)

    def _render_enum(
        self,
        values: list[Any],
        base_type: str | None,
        *,
        hint: str,
    ) -> str:
        """Render an inline ``enum`` as a generated enum class."""
        if base_type == "integer":
            kind = "int_enum"
        elif base_type in (None, "string"):
            kind = "str_enum"
        else:
            self.note(
                f"enum of {base_type!r} in {hint} rendered as its base type "
                f"(only string and integer enums become enum classes)"
            )
            return _PRIMITIVE_TYPES.get(base_type or "string", "Any")
        return self.ensure_enum(hint, values, kind)

    # -- class registration ----------------------------------------------

    def ensure_component(self, wire_name: str, schema: Mapping[str, Any]) -> str:
        """Register (once) the annotation for a ``components.schemas`` entry.

        The single entry point for every component, whether it is reached
        from the top-level sweep or from a ``$ref`` inside another schema.
        Routing both through here is what keeps a component that happens to
        be a bare enum from being built as an empty model by whichever path
        got there first.

        Args:
            wire_name (str): The ``components.schemas`` key.
            schema (Mapping[str, Any]): Its resolved schema.

        Returns:
            str: The generated class name, or the rendered annotation when
            the component is a bare scalar that gets no class of its own.

        Notes:
            The class name is reserved **before** recursing into the
            schema's properties. A self-referencing schema
            (``Node.children: Node[]``) would otherwise re-enter this method
            and register a second class for the same component.
        """
        existing = self.wire_to_class.get(wire_name)
        if existing is not None:
            return existing

        enum_values = schema.get("enum")
        if isinstance(enum_values, list) and enum_values:
            rendered = self._render_enum(
                enum_values,
                self._base_type(schema),
                hint=wire_name,
            )
            self.wire_to_class[wire_name] = rendered
            return rendered

        base_type = self._base_type(schema)
        has_properties = isinstance(schema.get("properties"), dict)
        is_composed = any(key in schema for key in ("allOf", "oneOf", "anyOf"))
        if base_type not in (None, "object") and not has_properties and not is_composed:
            rendered = self.render_type(schema, hint=wire_name)
            self.note(
                f"component {wire_name!r} is a bare {base_type} — used inline "
                f"as {rendered} instead of getting its own class"
            )
            self.wire_to_class[wire_name] = rendered
            return rendered

        class_name = unique(to_pascal(wire_name), self.taken_class_names)
        self.wire_to_class[wire_name] = class_name
        self.schemas[class_name] = self._build_schema(class_name, wire_name, schema)
        return class_name

    def ensure_inline(self, hint: str, schema: Mapping[str, Any]) -> str:
        """Register a class for an inline (unnamed) object schema.

        Args:
            hint (str): Suggested name, derived from where the schema
                appeared (``"UserAddress"`` for ``User.address``).
            schema (Mapping[str, Any]): The inline schema.

        Returns:
            str: The generated class name.
        """
        class_name = unique(to_pascal(hint), self.taken_class_names)
        self.schemas[class_name] = self._build_schema(class_name, hint, schema)
        return class_name

    def ensure_enum(
        self,
        hint: str,
        values: list[Any],
        kind: str,
    ) -> str:
        """Register an enum class, reusing an identical one when possible.

        Args:
            hint (str): Suggested class name.
            values (list[Any]): The enum values, in specification order.
            kind (str): ``"str_enum"`` or ``"int_enum"``.

        Returns:
            str: The generated class name.
        """
        members: list[tuple[str, Any]] = []
        used: set[str] = set()
        for value in values:
            members.append((unique(enum_member_name(value), used), value))
        signature = (kind, tuple(members))
        for existing in self.schemas.values():
            if (existing.kind, existing.enum_members) == signature:
                return existing.name
        class_name = unique(to_pascal(hint), self.taken_class_names)
        self.schemas[class_name] = SchemaIR(
            name=class_name,
            wire_name=hint,
            kind=kind,  # type: ignore[arg-type]
            docstring=f"Allowed values for {hint}.",
            enum_members=tuple(members),
        )
        return class_name

    def _build_schema(
        self,
        class_name: str,
        wire_name: str,
        schema: Mapping[str, Any],
    ) -> SchemaIR:
        """Build the :class:`SchemaIR` for one object schema."""
        resolved = dict(schema)
        all_of = resolved.get("allOf")
        if isinstance(all_of, list) and all_of:
            resolved = self._flatten_all_of(all_of, hint=class_name)

        properties = resolved.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        required_raw = resolved.get("required")
        required = (
            {str(item) for item in required_raw}
            if isinstance(required_raw, list)
            else set()
        )

        fields: list[FieldIR] = []
        used_names: set[str] = set()
        for wire_property, raw_property in properties.items():
            if not isinstance(raw_property, dict):
                continue
            fields.append(
                self._build_field(
                    wire_property=str(wire_property),
                    schema=raw_property,
                    required=str(wire_property) in required,
                    owner=class_name,
                    used_names=used_names,
                )
            )

        docstring = self._docstring_for(resolved, class_name)
        return SchemaIR(
            name=class_name,
            wire_name=wire_name,
            kind="model",
            docstring=docstring,
            fields=tuple(fields),
        )

    def _build_field(
        self,
        *,
        wire_property: str,
        schema: Mapping[str, Any],
        required: bool,
        owner: str,
        used_names: set[str],
    ) -> FieldIR:
        """Build the :class:`FieldIR` for one property.

        A non-required collection defaults to an empty list rather than
        ``None``, per the repo rule that "no matches" is an empty list and
        not a missing value.

        Dependencies between generated classes are **not** collected here.
        During recursion neither the owner nor a mutually-referencing peer
        is registered yet, so reading them from the in-progress registry
        misses exactly the cycles that matter. They are recomputed from the
        finished annotations by :func:`_resolve_dependencies`.
        """
        python_name = unique(field_name(wire_property), used_names)
        annotation = self.render_type(
            schema,
            hint=f"{owner}{to_pascal(wire_property)}",
        )

        resolved = deref(self.document, schema) if "$ref" in schema else dict(schema)
        is_list = annotation.startswith("list[")
        nullable = self._nullable(resolved)

        default: str | None = None
        default_is_factory = False
        if not required:
            if is_list:
                default = "list"
                default_is_factory = True
            else:
                default = "None"
                if not nullable and not annotation.endswith("| None"):
                    annotation = f"{annotation} | None"
        elif "default" in resolved:
            default = _render_literal(resolved["default"])

        examples = _collect_examples(resolved)
        return FieldIR(
            name=python_name,
            annotation=annotation,
            required=required,
            alias=wire_property if wire_property != python_name else None,
            default=default,
            default_is_factory=default_is_factory,
            title=_clean_text(resolved.get("title")),
            description=_clean_text(resolved.get("description")),
            examples=examples,
            constraints=_collect_constraints(resolved),
        )

    def _docstring_for(self, schema: Mapping[str, Any], class_name: str) -> str:
        """Return the summary line for a generated class docstring."""
        for key in ("description", "title"):
            text = _clean_text(schema.get(key))
            if text:
                first = text.splitlines()[0].strip()
                return first if first.endswith(".") else f"{first}."
        return f"Schema generated for {class_name}."

    # -- operations -------------------------------------------------------

    def build_client(self) -> ClientIR:
        """Build the :class:`ClientIR` from ``paths``.

        Returns:
            ClientIR: The client, with operations ordered by path then
            method so regenerating an unchanged specification produces an
            identical file.
        """
        info = self.document.get("info")
        info = info if isinstance(info, dict) else {}
        servers = self.document.get("servers")
        base_url = ""
        if isinstance(servers, list) and servers and isinstance(servers[0], dict):
            base_url = str(servers[0].get("url", ""))

        paths = self.document.get("paths")
        paths = paths if isinstance(paths, dict) else {}
        operations: list[OperationIR] = []
        used_names: set[str] = set()
        for path in sorted(paths):
            entry = paths[path]
            if not isinstance(entry, dict):
                continue
            shared = entry.get("parameters")
            shared = shared if isinstance(shared, list) else []
            for http_method in _HTTP_METHODS:
                raw = entry.get(http_method)
                if not isinstance(raw, dict):
                    continue
                operations.append(
                    self._build_operation(
                        path=str(path),
                        http_method=http_method,
                        operation=raw,
                        shared_parameters=shared,
                        used_names=used_names,
                    )
                )

        return ClientIR(
            class_name=f"{to_pascal(self.client_name)}Client",
            title=str(info.get("title", self.client_name)),
            version=str(info.get("version", "")),
            base_url=base_url,
            operations=tuple(operations),
        )

    def _build_operation(
        self,
        *,
        path: str,
        http_method: str,
        operation: Mapping[str, Any],
        shared_parameters: list[Any],
        used_names: set[str],
    ) -> OperationIR:
        """Build the :class:`OperationIR` for one path + method pair."""
        operation_id = operation.get("operationId")
        name = unique(
            method_name(
                str(operation_id) if isinstance(operation_id, str) else None,
                http_method,
                path,
            ),
            used_names,
        )

        parameters = self._build_parameters(
            [*shared_parameters, *(operation.get("parameters") or [])],
            owner=to_pascal(name),
        )
        body_annotation, body_required = self._build_body(
            operation, owner=to_pascal(name)
        )
        response_annotation, success_status = self._build_response(
            operation, owner=to_pascal(name)
        )

        summary = _clean_text(operation.get("summary")) or _clean_text(
            operation.get("description")
        )
        summary_line = (
            summary.splitlines()[0].strip()
            if summary
            else f"Call {http_method.upper()} {path}."
        )
        if not summary_line.endswith("."):
            summary_line = f"{summary_line}."

        description = _clean_text(operation.get("description")) or ""
        if description.splitlines()[:1] == [summary_line.rstrip(".")]:
            description = ""

        return OperationIR(
            name=name,
            http_method=http_method,
            path=path,
            summary=summary_line,
            description=description,
            parameters=parameters,
            body_annotation=body_annotation,
            body_required=body_required,
            response_annotation=response_annotation,
            success_status=success_status,
            error_statuses=self._error_statuses(operation),
        )

    def _build_parameters(
        self,
        raw_parameters: list[Any],
        *,
        owner: str,
    ) -> tuple[ParameterIR, ...]:
        """Build the path and query parameters of an operation.

        Required parameters are emitted before optional ones so the
        generated method signature is valid Python; ``header`` and
        ``cookie`` parameters are skipped with a note, since the
        ``HTTPClient`` already owns default headers.
        """
        path_params: list[ParameterIR] = []
        required_query: list[ParameterIR] = []
        optional_query: list[ParameterIR] = []
        used: set[str] = set()

        for raw in raw_parameters:
            if not isinstance(raw, dict):
                continue
            resolved = deref(self.document, raw)
            location = resolved.get("in")
            wire_name = str(resolved.get("name", ""))
            if not wire_name:
                continue
            if location not in ("path", "query"):
                self.note(
                    f"{location!r} parameter {wire_name!r} skipped (pass it via "
                    f"HTTPClient default_headers)"
                )
                continue
            schema = resolved.get("schema")
            schema = schema if isinstance(schema, dict) else {}
            annotation = self.render_type(schema, hint=f"{owner}{to_pascal(wire_name)}")
            required = bool(resolved.get("required")) or location == "path"
            if not required and not annotation.endswith("| None"):
                annotation = f"{annotation} | None"
            parameter = ParameterIR(
                name=unique(field_name(wire_name), used),
                wire_name=wire_name,
                location="path" if location == "path" else "query",
                annotation=annotation,
                required=required,
                description=_clean_text(resolved.get("description")),
            )
            if parameter.location == "path":
                path_params.append(parameter)
            elif required:
                required_query.append(parameter)
            else:
                optional_query.append(parameter)

        return (*path_params, *required_query, *optional_query)

    def _build_body(
        self,
        operation: Mapping[str, Any],
        *,
        owner: str,
    ) -> tuple[str | None, bool]:
        """Return the request body annotation and whether it is required."""
        body = operation.get("requestBody")
        if not isinstance(body, dict):
            return None, True
        resolved = deref(self.document, body)
        content = resolved.get("content")
        if not isinstance(content, dict) or not content:
            return None, True
        schema = _json_content_schema(content)
        if schema is None:
            self.note(
                f"request body of {owner} uses "
                f"{', '.join(sorted(content))} — only application/json is modelled"
            )
            return None, True
        return (
            self.render_type(schema, hint=f"{owner}Body"),
            bool(resolved.get("required", False)),
        )

    def _build_response(
        self,
        operation: Mapping[str, Any],
        *,
        owner: str,
    ) -> tuple[str | None, str]:
        """Return the success response annotation and its status code."""
        responses = operation.get("responses")
        if not isinstance(responses, dict):
            return None, "200"
        success = sorted(
            code for code in responses if str(code).isdigit() and 200 <= int(code) < 300
        )
        if not success:
            return None, "200"
        status = str(success[0])
        entry = responses[status]
        if not isinstance(entry, dict):
            return None, status
        resolved = deref(self.document, entry)
        content = resolved.get("content")
        if not isinstance(content, dict) or not content:
            return None, status
        schema = _json_content_schema(content)
        if schema is None:
            self.note(
                f"response of {owner} uses {', '.join(sorted(content))} — "
                f"only application/json is modelled"
            )
            return None, status
        return self.render_type(schema, hint=f"{owner}Response"), status

    def _error_statuses(
        self,
        operation: Mapping[str, Any],
    ) -> tuple[tuple[str, str], ...]:
        """Collect the documented non-2xx statuses for the docstring."""
        responses = operation.get("responses")
        if not isinstance(responses, dict):
            return ()
        collected: list[tuple[str, str]] = []
        for code in sorted(responses):
            text = str(code)
            if not text.isdigit() or int(text) < 400:
                continue
            entry = responses[code]
            description = ""
            if isinstance(entry, dict):
                description = _clean_text(entry.get("description")) or ""
            collected.append((text, description.splitlines()[0] if description else ""))
        return tuple(collected)


def _json_content_schema(content: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return the JSON media-type schema from a ``content`` mapping.

    Args:
        content (Mapping[str, Any]): An OpenAPI ``content`` object.

    Returns:
        Mapping[str, Any] | None: The schema under a JSON media type
        (``application/json``, ``application/vnd.x+json``, …), or ``None``
        when the operation only offers non-JSON media types.
    """
    for media_type, entry in content.items():
        if "json" not in str(media_type).lower():
            continue
        if not isinstance(entry, dict):
            continue
        schema = entry.get("schema")
        if isinstance(schema, dict):
            return schema
        return {}
    return None


def _clean_text(value: object) -> str | None:
    """Return a stripped string, or ``None`` for anything else/empty.

    Args:
        value (object): A candidate description/title from the document.

    Returns:
        str | None: The trimmed text, or ``None``. Keeping this strict is
        what guarantees the generator never invents documentation — an
        absent description stays absent.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _collect_examples(schema: Mapping[str, Any]) -> tuple[Any, ...]:
    """Collect examples from a schema, normalizing 3.0 and 3.1 spellings.

    Args:
        schema (Mapping[str, Any]): The schema fragment.

    Returns:
        tuple[Any, ...]: OpenAPI 3.1's ``examples`` list, or 3.0's single
        ``example`` wrapped in a tuple. Empty when the schema documents
        none.
    """
    examples = schema.get("examples")
    if isinstance(examples, list) and examples:
        return tuple(examples)
    if "example" in schema:
        return (schema["example"],)
    return ()


def _collect_constraints(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Map OpenAPI validation keywords to Pydantic ``Field`` constraints.

    Args:
        schema (Mapping[str, Any]): The schema fragment.

    Returns:
        dict[str, Any]: Keyword arguments for ``Field``. ``exclusiveMinimum``
        as a *boolean* (the OpenAPI 3.0 spelling, which modifies
        ``minimum``) is translated into ``gt``; the 3.1 numeric spelling is
        used directly.

    Notes:
        In 3.0 ``exclusiveMinimum`` / ``exclusiveMaximum`` are booleans that
        qualify ``minimum`` / ``maximum``, so the flag is re-pointed at the
        value it qualifies and the plain ``ge`` / ``le`` it would otherwise
        produce is dropped. Emitting both would validate the wrong bound.
    """
    constraints: dict[str, Any] = {}
    for source, target in _STRING_CONSTRAINTS.items():
        if source in schema:
            constraints[target] = schema[source]
    for source, target in _ARRAY_CONSTRAINTS.items():
        if source in schema:
            constraints[target] = schema[source]
    for source, target in _NUMERIC_CONSTRAINTS.items():
        if source not in schema:
            continue
        value = schema[source]
        if isinstance(value, bool):
            partner = "minimum" if source == "exclusiveMinimum" else "maximum"
            if value and partner in schema:
                constraints["gt" if partner == "minimum" else "lt"] = schema[partner]
                constraints.pop("ge" if partner == "minimum" else "le", None)
            continue
        constraints[target] = value
    return constraints


def _render_literal(value: object) -> str:
    """Render a JSON value as Python source.

    Args:
        value (object): A default value from the specification.

    Returns:
        str: Source text for the value. Containers are rendered via
        ``repr``, which is exact for the JSON subset (``dict``/``list``/
        ``str``/``int``/``float``/``bool``/``None``).
    """
    return repr(value)


def _resolve_dependencies(
    schemas: Mapping[str, SchemaIR],
) -> dict[str, SchemaIR]:
    """Recompute each class's dependencies from its finished annotations.

    Runs after the whole document is parsed, when every generated class
    name is known. Collecting during recursion cannot work: while ``A`` is
    being built, neither ``A`` nor a mutually-referencing ``B`` is
    registered, so an ``A``/``B`` cycle would go unnoticed and the emitter
    would skip the ``model_rebuild()`` calls those forward references need.

    Args:
        schemas (Mapping[str, SchemaIR]): Every generated class by name.

    Returns:
        dict[str, SchemaIR]: The same classes with ``dependencies`` filled
        in. A self-reference is included; :func:`_order_schemas` discounts
        it when deciding readiness.
    """
    names = set(schemas)
    resolved: dict[str, SchemaIR] = {}
    for name, schema in schemas.items():
        found: set[str] = set()
        for schema_field in schema.fields:
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", schema_field.annotation):
                if token in names:
                    found.add(token)
        resolved[name] = replace(schema, dependencies=frozenset(found))
    return resolved


def _order_schemas(
    schemas: Mapping[str, SchemaIR],
) -> tuple[tuple[SchemaIR, ...], frozenset[str]]:
    """Order classes so each follows its dependencies, and report cycles.

    Enums come first (they never depend on anything), then models in
    dependency order. Classes in a cycle are appended in name order and
    reported, so the emitter can close the module with the
    ``model_rebuild()`` calls their forward references need.

    Args:
        schemas (Mapping[str, SchemaIR]): Every generated class by name.

    Returns:
        tuple[tuple[SchemaIR, ...], frozenset[str]]: The ordered classes
        and the names taking part in a cycle.
    """
    enums = [s for s in schemas.values() if s.kind != "model"]
    models = {s.name: s for s in schemas.values() if s.kind == "model"}

    ordered: list[SchemaIR] = sorted(enums, key=lambda s: s.name)
    emitted: set[str] = {s.name for s in ordered}
    remaining = dict(models)

    while remaining:
        ready = [
            name
            for name, schema in remaining.items()
            if not (schema.dependencies & remaining.keys()) - {name}
        ]
        if not ready:
            break
        for name in sorted(ready):
            ordered.append(remaining.pop(name))
            emitted.add(name)

    cyclic = frozenset(remaining)
    for name in sorted(remaining):
        ordered.append(remaining[name])
    return tuple(ordered), cyclic


def parse_spec(document: Mapping[str, Any], *, client_name: str) -> SpecIR:
    """Parse a loaded OpenAPI document into the intermediate representation.

    Args:
        document (Mapping[str, Any]): The document, as returned by
            :func:`tempest_fastapi_sdk.openapi.load_spec`.
        client_name (str): Base name for the generated client class.

    Returns:
        SpecIR: Schemas (ordered), the client, the cyclic class names and
        every unsupported-construct note.

    Raises:
        SpecError: When the document declares neither ``components.schemas``
            nor ``paths`` — there would be nothing to generate, and an
            empty output directory is a worse answer than an error.
    """
    parser = _Parser(document, client_name=client_name)

    components = document.get("components")
    components = components if isinstance(components, dict) else {}
    raw_schemas = components.get("schemas")
    raw_schemas = raw_schemas if isinstance(raw_schemas, dict) else {}
    paths = document.get("paths")
    paths = paths if isinstance(paths, dict) else {}

    if not raw_schemas and not paths:
        raise SpecError(
            "The document declares neither `components.schemas` nor `paths`, "
            "so there is nothing to generate."
        )

    for wire_name in raw_schemas:
        raw = raw_schemas[wire_name]
        if not isinstance(raw, dict):
            continue
        parser.ensure_component(str(wire_name), deref(document, raw))

    client = parser.build_client()
    ordered, cyclic = _order_schemas(_resolve_dependencies(parser.schemas))
    return SpecIR(
        schemas=ordered,
        client=client,
        cyclic=cyclic,
        unsupported=tuple(parser.notes),
    )


__all__: list[str] = [
    "parse_spec",
]
