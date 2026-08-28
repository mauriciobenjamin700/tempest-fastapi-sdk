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
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
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
    class_name as _class_name_for,
)
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

_PLACEHOLDERS: re.Pattern[str] = re.compile(r"\{([^{}]+)\}")
"""Every ``{name}`` an OpenAPI path template interpolates."""

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

        ``_resolving`` holds the components whose annotation is currently
        being rendered. A component that gets a class reserves its name
        before recursing, but one rendered as a union has no name to
        reserve until the union is built — so a self-referencing union
        would re-enter :meth:`ensure_component` forever without this.
        """
        self.document: Mapping[str, Any] = document
        self.client_name: str = client_name
        self.taken_class_names: set[str] = set()
        self.schemas: dict[str, SchemaIR] = {}
        self.wire_to_class: dict[str, str] = {}
        self.notes: list[str] = []
        self.response_annotations: list[str] = []
        self.imports: set[str] = set()
        self._sinks: list[list[str]] = []
        self._resolving: set[str] = set()

    def note(self, message: str) -> None:
        """Record an unsupported construct, de-duplicated.

        Args:
            message (str): Human-readable description of the gap.

        The message also reaches every open :meth:`capture` sink, each with
        its own de-duplication. The summary's list must not repeat itself,
        but two fields hitting the *same* gap both need marking — sharing
        one de-duplicated list would mark only the first one.
        """
        if message not in self.notes:
            self.notes.append(message)
        for sink in self._sinks:
            if message not in sink:
                sink.append(message)

    @contextmanager
    def capture(self) -> Iterator[list[str]]:
        """Collect every note raised inside the block.

        Yields:
            list[str]: The notes, appended as they are raised. Read it
            **after** the block exits.

        Used to attach a gap to the specific field, parameter or operation
        it came from, so the emitter can mark that line in the output
        instead of leaving the reader with an unexplained ``Any``.

        Removal is **by identity**, scanning from the end. Parsing is
        re-entrant — rendering a field's type resolves a ``$ref``, which
        builds that component, whose own fields open their own captures —
        so several sinks are open at once. ``list.remove`` compares by
        equality, and two sinks that have collected nothing are both
        ``[]``, hence equal: the inner block would drop the *outer* sink
        and the outer block would then die with
        ``ValueError: list.remove(x): x not in list``. That is not
        hypothetical — it is what parsing Stripe's specification hit,
        aborting generation entirely.
        """
        sink: list[str] = []
        self._sinks.append(sink)
        try:
            yield sink
        finally:
            for index in range(len(self._sinks) - 1, -1, -1):
                if self._sinks[index] is sink:
                    del self._sinks[index]
                    break

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
            annotation = self._render_type(
                member,
                hint=self._variant_hint(member, hint=hint, index=index),
            )
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

    def _variant_hint(
        self,
        member: Mapping[str, Any],
        *,
        hint: str,
        index: int,
    ) -> str:
        """Name one member of a union.

        Args:
            member (Mapping[str, Any]): The member schema.
            hint (str): Name of the union itself.
            index (int): Position of the member, zero-based.

        Returns:
            str: ``"PaymentCreatePayloadQrCode"`` when the member carries a
            ``title``, ``"PaymentCreatePayloadVariant2"`` otherwise. A
            title is the only name the specification gives a variant, and
            ``Variant2`` tells a caller nothing about which shape to build.
        """
        title = member.get("title")
        if isinstance(title, str) and title.strip():
            return f"{hint}{_class_name_for(title)}"
        return f"{hint}Variant{index + 1}"

    def _component_from_combinator(
        self,
        wire_name: str,
        schema: Mapping[str, Any],
        members: list[Any],
        combinator: str,
    ) -> str:
        """Register a component whose top level is ``oneOf`` or ``anyOf``.

        Args:
            wire_name (str): The ``components.schemas`` key.
            schema (Mapping[str, Any]): The component's schema.
            members (list[Any]): The combinator's member schemas.
            combinator (str): ``"oneOf"`` or ``"anyOf"``.

        Returns:
            str: The generated name — a model when the variants merge, a
            union alias when they stay apart. Either way the component
            keeps a name callers can import and annotate with.

        Two shapes, because specifications use the same construct for two
        different things. **Untitled object variants** are almost always one
        payload described several times over — OpenPix spells "name plus one
        of taxID, email or phone" as three near-identical objects — so they
        merge into a single model carrying every property, required only
        where every variant requires it. **Titled variants, or a
        discriminator**, are a real sum type (``PIX_KEY`` vs ``QR_CODE`` vs
        ``MANUAL``): each becomes its own class and the component name
        becomes a union alias over them.

        Before this existed the component became a class with **no fields**,
        and ``BaseSchema``'s ``extra="ignore"`` then dropped everything a
        caller passed — a charge went out with ``"customer": {}`` and no
        error anywhere.
        """
        if wire_name in self._resolving:
            self.note(
                f"self-referencing {combinator} in component {wire_name!r} "
                f"rendered as Any"
            )
            return "Any"

        resolved = [
            deref(self.document, member)
            for member in members
            if isinstance(member, dict)
        ]
        objects = [
            member for member in resolved if isinstance(member.get("properties"), dict)
        ]
        titled = any(
            isinstance(member.get("title"), str) and member["title"].strip()
            for member in resolved
        )
        tagged = isinstance(schema.get("discriminator"), dict)

        if objects and len(objects) == len(resolved) and not titled and not tagged:
            merged = self._merge_variants(resolved, hint=wire_name)
            description = schema.get("description")
            if isinstance(description, str):
                merged["description"] = description
            name = unique(
                _class_name_for(wire_name), self.taken_class_names, separator=""
            )
            self.wire_to_class[wire_name] = name
            self.schemas[name] = self._build_schema(name, wire_name, merged)
            self.note(
                f"{combinator} in {wire_name!r} merged into one model — every "
                f"variant's properties are accepted together, so "
                f"'exactly one variant' is not enforced"
            )
            return name

        name = unique(_class_name_for(wire_name), self.taken_class_names, separator="")
        self._resolving.add(wire_name)
        try:
            annotation = self._render_union(
                members,
                combinator,
                hint=wire_name,
                discriminator=schema.get("discriminator"),
            )
        finally:
            self._resolving.discard(wire_name)
        if not _is_aliasable(annotation):
            self.taken_class_names.discard(name)
            self.wire_to_class[wire_name] = annotation
            return annotation
        self.wire_to_class[wire_name] = name
        self.schemas[name] = SchemaIR(
            name=name,
            wire_name=wire_name,
            kind="alias",
            docstring=self._docstring_for(schema, name),
            alias_target=annotation,
        )
        return name

    def _merge_variants(
        self,
        members: Sequence[Mapping[str, Any]],
        *,
        hint: str,
    ) -> dict[str, Any]:
        """Merge ``oneOf``/``anyOf`` object variants into one schema.

        Args:
            members (Sequence[Mapping[str, Any]]): The resolved members.
            hint (str): Name used for notes.

        Returns:
            dict[str, Any]: One object schema whose ``properties`` are the
            union of every variant's and whose ``required`` is their
            **intersection**. Union of required would demand fields that only
            one variant asks for, and the caller could then satisfy no
            variant at all; the intersection is what every variant agrees on.
        """
        merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        required_sets: list[set[str]] = []
        for member in members:
            resolved: Mapping[str, Any] = member
            nested = resolved.get("allOf")
            if isinstance(nested, list) and nested:
                resolved = self._flatten_all_of(nested, hint=hint)
            properties = resolved.get("properties")
            if isinstance(properties, dict):
                merged["properties"].update(properties)
            required = resolved.get("required")
            required_sets.append(
                {str(item) for item in required}
                if isinstance(required, list)
                else set()
            )
        if required_sets:
            merged["required"] = sorted(set.intersection(*required_sets))
        return merged

    def _distribute_all_of(
        self,
        members: list[Any],
        *,
        hint: str,
    ) -> dict[str, Any] | None:
        """Push an ``allOf`` down into a member that is a union.

        Args:
            members (list[Any]): The ``allOf`` entries.
            hint (str): Name used for notes.

        Returns:
            dict[str, Any] | None: ``{"oneOf": [...]}`` where each entry is
            one variant combined with every other ``allOf`` member, or
            ``None`` when no member is a union and the plain merge applies.

        ``allOf: [PaymentCreatePayload, {autoApprove}]`` — a sum type
        narrowed by a flag — is how OpenPix declares its payment body. A
        flat merge asks the union for ``properties``, a union has none, and
        the body collapses to the single ``autoApprove`` flag: the request
        that endpoint exists for becomes unrepresentable. Distributing keeps
        each variant whole and gives every one of them the flag.

        Only the first union member is distributed over. Two of them would
        be a cartesian product of variants, which no specification here
        does, and a silent explosion is worse than a note.
        """
        resolved: list[Any] = [
            deref(self.document, member) if isinstance(member, dict) else member
            for member in members
        ]
        for index, member in enumerate(resolved):
            if not isinstance(member, dict):
                continue
            for combinator in ("oneOf", "anyOf"):
                variants = member.get(combinator)
                if not (isinstance(variants, list) and variants):
                    continue
                others = [
                    other for position, other in enumerate(members) if position != index
                ]
                if any(
                    isinstance(rest, dict)
                    and any(key in rest for key in ("oneOf", "anyOf"))
                    for position, rest in enumerate(resolved)
                    if position != index
                ):
                    self.note(
                        f"allOf in {hint} combines two unions — only the first "
                        f"is expanded, the rest render as their own annotation"
                    )
                combined: list[Any] = []
                for variant in variants:
                    if not isinstance(variant, dict):
                        continue
                    entry: dict[str, Any] = {"allOf": [variant, *others]}
                    title = variant.get("title")
                    if isinstance(title, str) and title.strip():
                        entry["title"] = title
                    combined.append(entry)
                if combined:
                    return {combinator: combined}
        return None

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
            ``allOf`` to specialize a base. When one member is itself a
            ``oneOf``/``anyOf``, the result is that combinator instead —
            see :meth:`_distribute_all_of`.
        """
        distributed = self._distribute_all_of(members, hint=hint)
        if distributed is not None:
            return distributed

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

        if not has_properties:
            effective: Mapping[str, Any] = schema
            all_of = schema.get("allOf")
            if isinstance(all_of, list) and all_of:
                effective = self._flatten_all_of(all_of, hint=wire_name)
            for combinator in ("oneOf", "anyOf"):
                members = effective.get(combinator)
                if isinstance(members, list) and members:
                    return self._component_from_combinator(
                        wire_name,
                        effective,
                        members,
                        combinator,
                    )

        if base_type not in (None, "object") and not has_properties and not is_composed:
            rendered = self.render_type(schema, hint=wire_name)
            self.note(
                f"component {wire_name!r} is a bare {base_type} — used inline "
                f"as {rendered} instead of getting its own class"
            )
            self.wire_to_class[wire_name] = rendered
            return rendered

        class_name = unique(
            _class_name_for(wire_name), self.taken_class_names, separator=""
        )
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
        class_name = unique(_class_name_for(hint), self.taken_class_names, separator="")
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
        class_name = unique(_class_name_for(hint), self.taken_class_names, separator="")
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
        with self.capture() as gaps:
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
            unsupported=tuple(gaps),
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
            path=path,
        )
        with self.capture() as gaps:
            body_annotation, body_required, body_encoding = self._build_body(
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
            body_encoding=body_encoding,
            response_annotation=response_annotation,
            success_status=success_status,
            error_statuses=self._error_statuses(operation),
            unsupported=tuple(gaps),
        )

    def _build_parameters(
        self,
        raw_parameters: list[Any],
        *,
        owner: str,
        path: str,
    ) -> tuple[ParameterIR, ...]:
        """Build the path, query and header parameters of an operation.

        Required parameters are emitted before optional ones so the
        generated method signature is valid Python; ``cookie`` parameters
        are skipped with a note, since a cookie is connection state the
        ``HTTPClient`` owns, not a per-call value.

        ``header`` parameters **are** emitted, as keyword-only arguments.
        A header the specification attaches to one operation is a per-call
        value — ``X-Idempotency-Key`` exists precisely to differ on every
        request — so routing it through the client's ``default_headers``
        would send one fixed value for every call. For an idempotency key
        that is not a limitation but a defect: the second charge would be
        deduplicated onto the first.

        A ``path`` parameter whose placeholder is absent from ``path`` is
        skipped with a note rather than emitted. Keeping it would put an
        argument in the signature that the request never carries — the
        caller passes an identifier and it is dropped on the floor, which
        is the one failure mode the generator must not produce in silence.

        The mirror case — a placeholder in ``path`` that no parameter
        declares — is invalid OpenAPI and is repaired rather than skipped:
        a required ``str`` parameter is synthesized, since the emitted path
        is an f-string and dropping the argument would leave the generated
        method referencing an undefined name.

        Path parameters come out ordered by their **position in the
        template**, not by their position in ``parameters``. They are the
        only positional arguments of the generated method, so a
        specification listing them out of order would hand a caller reading
        ``/{a}/{b}`` a signature spelled ``(b, a)``.

        Args:
            raw_parameters (list[Any]): The operation's ``parameters``,
                already merged with the path item's shared ones.
            owner (str): PascalCase prefix for any inline schema this
                parameter needs to name.
            path (str): The path template the operation is bound to, used
                to confirm each path parameter is actually interpolated,
                and to order the ones that are.
        """
        path_params: list[ParameterIR] = []
        required_query: list[ParameterIR] = []
        optional_query: list[ParameterIR] = []
        headers: list[ParameterIR] = []
        used: set[str] = set()

        for raw in raw_parameters:
            if not isinstance(raw, dict):
                continue
            resolved = deref(self.document, raw)
            location = resolved.get("in")
            wire_name = str(resolved.get("name", ""))
            if not wire_name:
                continue
            if location not in ("path", "query", "header"):
                self.note(
                    f"{location!r} parameter {wire_name!r} skipped (pass it via "
                    f"HTTPClient default_headers)"
                )
                continue
            if location == "path" and f"{{{wire_name}}}" not in path:
                self.note(
                    f"path parameter {wire_name!r} of {path!r} is declared but "
                    f"absent from the path template — skipped, since the value "
                    f"would never reach the request"
                )
                continue
            schema = resolved.get("schema")
            schema = schema if isinstance(schema, dict) else {}
            with self.capture() as gaps:
                annotation = self.render_type(
                    schema, hint=f"{owner}{to_pascal(wire_name)}"
                )
            required = bool(resolved.get("required")) or location == "path"
            if not required and not annotation.endswith("| None"):
                annotation = f"{annotation} | None"
            parameter = ParameterIR(
                name=unique(field_name(wire_name), used),
                wire_name=wire_name,
                location=(
                    "path"
                    if location == "path"
                    else "header"
                    if location == "header"
                    else "query"
                ),
                annotation=annotation,
                required=required,
                description=_clean_text(resolved.get("description")),
                unsupported=tuple(gaps),
            )
            if parameter.location == "path":
                path_params.append(parameter)
            elif parameter.location == "header":
                headers.append(parameter)
            elif required:
                required_query.append(parameter)
            else:
                optional_query.append(parameter)

        path_params.extend(self._undeclared_path_params(path, path_params, used))
        path_params.sort(key=lambda item: path.index(f"{{{item.wire_name}}}"))
        return (*path_params, *required_query, *optional_query, *headers)

    def _undeclared_path_params(
        self,
        path: str,
        declared: list[ParameterIR],
        used: set[str],
    ) -> list[ParameterIR]:
        """Synthesize a parameter for every placeholder nothing declares.

        Args:
            path (str): The path template the operation is bound to.
            declared (list[ParameterIR]): The path parameters built from the
                specification's own ``parameters``.
            used (set[str]): Python names already taken in this operation.
                **Mutated** — each synthesized name is reserved.

        Returns:
            list[ParameterIR]: One required ``str`` parameter per
            placeholder with no declaration, in template order.

        OpenAPI requires every placeholder to be declared, so reaching this
        means the specification is wrong. Skipping it is not an option: the
        emitted path is an f-string, so the generated method would reference
        a name that does not exist and the module would not import.
        """
        known = {parameter.wire_name for parameter in declared}
        synthesized: list[ParameterIR] = []
        for wire_name in _PLACEHOLDERS.findall(path):
            if wire_name in known:
                continue
            known.add(wire_name)
            with self.capture() as gaps:
                self.note(
                    f"path {path!r} interpolates {wire_name!r}, which no parameter "
                    f"declares — generated as a required str"
                )
            synthesized.append(
                ParameterIR(
                    name=unique(field_name(wire_name), used),
                    wire_name=wire_name,
                    location="path",
                    annotation="str",
                    required=True,
                    description=None,
                    unsupported=tuple(gaps),
                )
            )
        return synthesized

    def _build_body(
        self,
        operation: Mapping[str, Any],
        *,
        owner: str,
    ) -> tuple[str | None, bool, str]:
        """Return the body annotation, whether it is required, and its encoding.

        Args:
            operation (Mapping[str, Any]): The operation object.
            owner (str): PascalCase name used to hint inline schemas.

        Returns:
            tuple[str | None, bool, str]: Annotation (``None`` when the
            operation takes no modelled body), required flag, and
            ``"json"`` or ``"form"``.

        JSON wins when the operation offers both, since it is the richer
        encoding. Form is not a fallback for "we could not model it": it is
        the only encoding some APIs accept — every write in Stripe's API is
        ``application/x-www-form-urlencoded`` — and treating it as JSON
        produced a client whose every write failed.
        """
        body = operation.get("requestBody")
        if not isinstance(body, dict):
            return None, True, "json"
        resolved = deref(self.document, body)
        content = resolved.get("content")
        if not isinstance(content, dict) or not content:
            return None, True, "json"
        schema = _json_content_schema(content)
        encoding = "json"
        if schema is None:
            schema = _form_content_schema(content)
            encoding = "form"
        if schema is None:
            self.note(
                f"request body of {owner} uses "
                f"{', '.join(sorted(content))} — only application/json and "
                f"application/x-www-form-urlencoded are modelled"
            )
            return None, True, "json"
        hint = f"{owner}Body"
        annotation = self.render_type(schema, hint=hint)
        return (
            self._name_body_union(annotation, hint=hint),
            bool(resolved.get("required", False)),
            encoding,
        )

    def _name_body_union(self, annotation: str, *, hint: str) -> str:
        """Give a request body that renders as a union a name of its own.

        Args:
            annotation (str): The rendered body annotation.
            hint (str): The name the body would have had as a class.

        Returns:
            str: The alias name, or ``annotation`` unchanged when it is not a
            union of generated classes.

        A four-variant union spelled out in the signature also lands in the
        method's ``Args:`` section, where it wraps across lines — and a
        wrapped ``name (type):`` pair is one griffe cannot parse, which fails
        the documentation build in strict mode. It also reads worse: the
        caller wants one name to import.
        """
        if not _is_aliasable(annotation):
            return annotation
        members = [member.strip() for member in annotation.split("|")]
        if not all(member in self.schemas for member in members):
            return annotation
        name = unique(_class_name_for(hint), self.taken_class_names, separator="")
        self.schemas[name] = SchemaIR(
            name=name,
            wire_name=hint,
            kind="alias",
            docstring=f"Request body of {hint}, one variant per shape.",
            alias_target=annotation,
        )
        return name

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
        annotation = self.render_type(schema, hint=f"{owner}Response")
        self.response_annotations.append(annotation)
        return annotation, status

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


def _is_aliasable(annotation: str) -> bool:
    """Report whether an annotation can be emitted as a union alias.

    Args:
        annotation (str): A rendered annotation.

    Returns:
        bool: ``True`` for a plain ``A | B`` union. ``False`` for anything
        else, including a tagged union — ``Annotated[A | B,
        Field(discriminator="kind")]`` carries a ``|`` that is **inside**
        brackets, and the emitter's line wrapping splits on that character.
        The union is still rendered inline; it just does not get a name.
    """
    return "|" in annotation and "Annotated[" not in annotation


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


def _form_content_schema(content: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return the form-encoded media-type schema from a ``content`` mapping.

    Args:
        content (Mapping[str, Any]): An OpenAPI ``content`` object.

    Returns:
        Mapping[str, Any] | None: The schema under
        ``application/x-www-form-urlencoded``, or ``None`` when the
        operation does not offer it. ``multipart/form-data`` is
        deliberately excluded — it carries file parts, which need a
        different call shape than a flattened field mapping.
    """
    entry = content.get("application/x-www-form-urlencoded")
    if not isinstance(entry, dict):
        return None
    schema = entry.get("schema")
    return schema if isinstance(schema, dict) else {}


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


_LENGTH_FROM_NUMERIC: dict[str, str] = {
    "maximum": "max_length",
    "minimum": "min_length",
}
"""How a numeric bound is re-read when the schema is a string.

`maximum` on a string is a spec defect, not a shape pydantic can honor —
see :func:`_collect_constraints`. Only the two inclusive bounds map; an
exclusive one has no length equivalent worth guessing at.
"""


def _is_string_typed(schema: Mapping[str, Any]) -> bool:
    """Report whether a schema fragment describes a string.

    Args:
        schema (Mapping[str, Any]): The schema fragment.

    Returns:
        bool: ``True`` when ``type`` is ``"string"``, or a list of types
        whose only non-``null`` member is ``"string"`` (the 3.1 spelling of
        a nullable string).
    """
    declared = schema.get("type")
    if declared == "string":
        return True
    if isinstance(declared, list):
        concrete = [entry for entry in declared if entry != "null"]
        return concrete == ["string"]
    return False


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

        A numeric bound on a ``type: string`` schema is re-read as a length
        bound. Nothing legitimate produces that pair — a string has no
        magnitude — and specs in the wild write it anyway: OpenPix's
        ``ChargeRefundPayload.comment`` carries ``maximum: 140`` under a
        description that says "Maximum length of 140 characters". Passed
        through literally it emits ``Field(le=140)`` on a ``str``, and
        pydantic does not reject the value, it raises ``TypeError: Unable
        to apply constraint 'le' to supplied value`` at construction — so
        every refund carrying a comment failed before leaving the process.
    """
    constraints: dict[str, Any] = {}
    for source, target in _STRING_CONSTRAINTS.items():
        if source in schema:
            constraints[target] = schema[source]
    for source, target in _ARRAY_CONSTRAINTS.items():
        if source in schema:
            constraints[target] = schema[source]
    string_typed = _is_string_typed(schema)
    for source, target in _NUMERIC_CONSTRAINTS.items():
        if source not in schema:
            continue
        if string_typed:
            length = _LENGTH_FROM_NUMERIC.get(source)
            value = schema[source]
            if length is not None and isinstance(value, int):
                constraints.setdefault(length, value)
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
        annotations = [f.annotation for f in schema.fields]
        if schema.alias_target:
            annotations.append(schema.alias_target)
        for annotation in annotations:
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", annotation):
                if token in names:
                    found.add(token)
        resolved[name] = replace(schema, dependencies=frozenset(found))
    return resolved


def _mark_response_reachable(
    schemas: Mapping[str, SchemaIR],
    annotations: Sequence[str],
) -> dict[str, SchemaIR]:
    """Flag every class an operation's success response can reach.

    A third-party API adds a field without asking, and a response model
    that drops it turns "the specification is behind" into "the value is
    gone". That was measured on OpenPix: ``Charge`` omits ``fee``,
    ``discount`` and ``valueWithDiscount``, the API sends all three, and a
    consumer persisting ``charge.fee`` would have written zero into every
    row. So a response model keeps what it did not expect
    (``extra="allow"``) and the caller reads it back through
    ``model_extra``.

    Payload models are deliberately left alone: there, an unexpected key
    is the caller's own typo, and carrying it to the provider is worse
    than dropping it.

    Reachability is transitive — ``ChargeResponse`` naming ``Charge``,
    which names ``Customer``, marks all three — because a nested object is
    exactly where the dropped field hides.

    Args:
        schemas (Mapping[str, SchemaIR]): Every generated class by name,
            with ``dependencies`` already resolved.
        annotations (Sequence[str]): Rendered success-response
            annotations, one per operation that declares a JSON body.

    Returns:
        dict[str, SchemaIR]: The same classes, with
        ``reached_by_response`` set on the models the closure covers.
        Enums and unions are skipped — neither takes a ``model_config``.
    """
    pending: list[str] = []
    for annotation in annotations:
        pending.extend(
            token for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", annotation)
        )
    reached: set[str] = set()
    while pending:
        name = pending.pop()
        if name in reached or name not in schemas:
            continue
        reached.add(name)
        pending.extend(schemas[name].dependencies)
    return {
        name: replace(schema, reached_by_response=True)
        if name in reached and schema.kind == "model"
        else schema
        for name, schema in schemas.items()
    }


def _order_schemas(
    schemas: Mapping[str, SchemaIR],
) -> tuple[tuple[SchemaIR, ...], frozenset[str]]:
    """Order classes so each follows its dependencies, and report cycles.

    Enums come first (they never depend on anything), then models and
    union aliases in dependency order. Classes in a cycle are appended in
    name order and reported, so the emitter can close the module with the
    ``model_rebuild()`` calls their forward references need.

    An alias is ordered like a model, not like an enum: ``A | B`` is
    evaluated when the module executes, so it has to follow the classes it
    names — emitting it with the enums would raise ``NameError`` on import.

    Args:
        schemas (Mapping[str, SchemaIR]): Every generated class by name.

    Returns:
        tuple[tuple[SchemaIR, ...], frozenset[str]]: The ordered classes
        and the names taking part in a cycle.
    """
    enums = [s for s in schemas.values() if s.kind in ("str_enum", "int_enum")]
    models = {s.name: s for s in schemas.values() if s.kind in ("model", "alias")}

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
    resolved = _mark_response_reachable(
        _resolve_dependencies(parser.schemas),
        parser.response_annotations,
    )
    ordered, cyclic = _order_schemas(resolved)
    return SpecIR(
        schemas=ordered,
        client=client,
        cyclic=cyclic,
        unsupported=tuple(parser.notes),
    )


__all__: list[str] = [
    "parse_spec",
]
