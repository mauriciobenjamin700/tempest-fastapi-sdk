"""Intermediate representation between an OpenAPI document and emitted code.

Parsing and emitting are split by this layer so neither has to know the
other's shape: :mod:`tempest_fastapi_sdk.openapi.parse` resolves every
OpenAPI quirk (``allOf``, ``nullable``, ``$ref``, 3.0 vs 3.1 null syntax)
into these dataclasses, and the two emitters render them without ever
touching the raw document again.

Every node is frozen. The parse pass builds each one in a single step, and
freezing means an emitter cannot quietly mutate what another emitter is
about to read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SchemaKind = Literal["model", "str_enum", "int_enum", "alias"]
"""What a :class:`SchemaIR` renders as.

``alias`` is a module-level union assignment rather than a class. A
component whose top level is a ``oneOf`` of genuinely different shapes has
no single class to be, but dropping the name would leave the caller with
nothing to annotate and would remove a name the previous generation
exported.
"""


@dataclass(frozen=True, slots=True)
class FieldIR:
    """One property of a generated model.

    Attributes:
        name (str): The Python field name (``snake_case``).
        alias (str | None): The wire name, set **only** when it differs
            from ``name``. ``None`` means no ``alias=`` is emitted, which
            keeps the common all-snake_case specification clean.
        annotation (str): The rendered type annotation, as source text
            (``"str"``, ``"list[Address] | None"``).
        required (bool): Whether the specification lists the property in
            ``required``.
        default (str | None): Rendered default expression, or ``None``
            when the field is required. ``"None"`` and
            ``"default_factory=list"`` are both possible values, so the
            emitter reads :attr:`default_is_factory` to decide the syntax.
        default_is_factory (bool): Whether :attr:`default` names a
            factory callable rather than a literal.
        title (str | None): ``title`` from the specification.
        description (str | None): ``description`` from the specification.
        examples (tuple[Any, ...]): Examples from the specification —
            ``examples``, or a single ``example`` wrapped in a tuple.
        constraints (dict[str, Any]): Pydantic ``Field`` constraints
            derived from the specification (``ge``, ``le``,
            ``min_length``, ``pattern``, …).
        unsupported (tuple[str, ...]): Notes raised while rendering this
            field's type — the reasons its annotation is not what the
            specification described. The emitter writes each one above the
            field as an ``# openapi: unsupported`` comment, so a reader who
            opens the module months later can see why a field is ``Any``
            without regenerating and re-reading the command's summary.
    """

    name: str
    annotation: str
    required: bool
    alias: str | None = None
    default: str | None = None
    default_is_factory: bool = False
    title: str | None = None
    description: str | None = None
    examples: tuple[Any, ...] = ()
    constraints: dict[str, Any] = field(default_factory=dict)
    unsupported: tuple[str, ...] = ()

    @property
    def has_metadata(self) -> bool:
        """Whether the specification supplied any documentation for this field.

        Returns:
            bool: Whether the condition holds.
        """
        return bool(self.title or self.description or self.examples)


@dataclass(frozen=True, slots=True)
class SchemaIR:
    """One generated class — a Pydantic model or an enum.

    Attributes:
        name (str): The Python class name (``PascalCase``).
        wire_name (str): The ``components.schemas`` key (or the
            synthesized name for an inline schema), kept for the
            docstring so the generated code points back at the source.
        kind (SchemaKind): Whether to render a model or an enum.
        docstring (str): Summary line for the class docstring, from the
            specification's ``description``/``title``.
        fields (tuple[FieldIR, ...]): Properties, in specification order.
            Empty for enums.
        enum_members (tuple[tuple[str, Any], ...]): ``(member_name,
            value)`` pairs. Empty for models.
        alias_target (str): The rendered annotation an ``alias`` assigns —
            ``"A | B | C"``. Empty for every other kind.
        dependencies (frozenset[str]): Names of other generated classes
            this one references, used to order the output.
        unsupported (tuple[str, ...]): Human-readable notes about
            constructs that could not be represented, surfaced to the
            user instead of silently producing a wrong schema.
    """

    name: str
    wire_name: str
    kind: SchemaKind
    docstring: str
    fields: tuple[FieldIR, ...] = ()
    enum_members: tuple[tuple[str, Any], ...] = ()
    alias_target: str = ""
    dependencies: frozenset[str] = frozenset()
    unsupported: tuple[str, ...] = ()

    @property
    def needs_populate_by_name(self) -> bool:
        """Whether the class must opt into ``populate_by_name``.

        ``BaseSchema.model_config`` does not enable it, so a model with
        aliases would otherwise reject its own Python field names on
        input. Pydantic merges ``model_config`` across inheritance, so
        declaring it on the generated class keeps everything else the base
        sets.

        Returns:
            bool: Whether the condition holds.
        """
        return any(f.alias for f in self.fields)


@dataclass(frozen=True, slots=True)
class ParameterIR:
    """A path, query or header parameter of an operation.

    Attributes:
        name (str): The Python argument name.
        wire_name (str): The name to send on the wire.
        location (Literal["path", "query", "header"]): Where the value
            goes.
        annotation (str): Rendered type annotation.
        required (bool): Whether the specification marks it required.
            Path parameters are always required.
        description (str | None): Description from the specification, for
            the generated ``Args:`` section.
        unsupported (tuple[str, ...]): Notes raised while rendering this
            parameter — why its annotation is not what the specification
            described, or that it was synthesized because the path template
            interpolated a name nothing declared.
    """

    name: str
    wire_name: str
    location: Literal["path", "query", "header"]
    annotation: str
    required: bool
    description: str | None = None
    unsupported: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OperationIR:
    """One method of the generated client.

    Attributes:
        name (str): The Python method name.
        http_method (str): Lower-cased HTTP method.
        path (str): The path template, ``{braces}`` intact.
        summary (str): Docstring summary line.
        description (str): Extra docstring prose, may be empty.
        parameters (tuple[ParameterIR, ...]): Path parameters first, then
            query parameters — required before optional, so the generated
            signature is valid Python.
        body_annotation (str | None): Type of the request body, or
            ``None`` when the operation takes none.
        body_required (bool): Whether the body is mandatory.
        body_encoding (str): How the body reaches the wire —
            ``"json"`` for a JSON media type, ``"form"`` for
            ``application/x-www-form-urlencoded``. The emitter picks
            ``json=`` or ``data=`` from this; guessing JSON is what made
            the generator unusable against a form-only API like Stripe,
            whose every write is form-encoded.
        response_annotation (str | None): Type of the success response, or
            ``None`` when the operation returns no content.
        success_status (str): The documented success status code, used in
            the docstring.
        error_statuses (tuple[tuple[str, str], ...]): ``(status,
            description)`` pairs from the specification, rendered into the
            docstring so the caller sees what can fail.
        unsupported (tuple[str, ...]): Notes raised while rendering this
            operation's body and response — a non-JSON content type, or a
            schema that could not be modelled. Emitted above the method as
            ``# openapi: unsupported`` comments.
    """

    name: str
    http_method: str
    path: str
    summary: str
    description: str = ""
    parameters: tuple[ParameterIR, ...] = ()
    body_annotation: str | None = None
    body_required: bool = True
    body_encoding: str = "json"
    response_annotation: str | None = None
    success_status: str = "200"
    error_statuses: tuple[tuple[str, str], ...] = ()
    unsupported: tuple[str, ...] = ()

    @property
    def path_parameters(self) -> tuple[ParameterIR, ...]:
        """Parameters interpolated into the URL.

        Returns:
            tuple[ParameterIR, ...]: The matching parameters, in declaration
                order.
        """
        return tuple(p for p in self.parameters if p.location == "path")

    @property
    def query_parameters(self) -> tuple[ParameterIR, ...]:
        """Parameters sent in the query string.

        Returns:
            tuple[ParameterIR, ...]: The matching parameters, in declaration
                order.
        """
        return tuple(p for p in self.parameters if p.location == "query")

    @property
    def header_parameters(self) -> tuple[ParameterIR, ...]:
        """Parameters sent as request headers.

        Returns:
            tuple[ParameterIR, ...]: The matching parameters, in declaration
                order.

        A header the specification declares on the operation is a
        per-request value — ``X-Idempotency-Key`` is the clearest case: the
        whole point is that it differs on every call. Folding it into the
        client's ``default_headers`` would send one key for every charge,
        which is worse than sending none.
        """
        return tuple(p for p in self.parameters if p.location == "header")


@dataclass(frozen=True, slots=True)
class ClientIR:
    """The generated HTTP client class.

    Attributes:
        class_name (str): Name of the generated class.
        title (str): ``info.title`` from the specification.
        version (str): ``info.version`` from the specification.
        base_url (str): First entry of ``servers``, or empty when the
            specification declares none.
        operations (tuple[OperationIR, ...]): Every documented operation,
            ordered by path then method for a stable diff.
    """

    class_name: str
    title: str
    version: str
    base_url: str
    operations: tuple[OperationIR, ...] = ()


@dataclass(frozen=True, slots=True)
class SpecIR:
    """Everything parsed out of one OpenAPI document.

    Attributes:
        schemas (tuple[SchemaIR, ...]): Generated classes, already ordered
            so a class appears after the ones it depends on where the
            dependency graph is acyclic.
        client (ClientIR): The generated client.
        cyclic (frozenset[str]): Classes taking part in a reference cycle.
            The emitter closes the module with a ``model_rebuild()`` call
            for each, since their forward references cannot resolve at
            class-creation time.
        unsupported (tuple[str, ...]): Every note collected across the
            document, for the command's summary.
    """

    schemas: tuple[SchemaIR, ...]
    client: ClientIR
    cyclic: frozenset[str] = frozenset()
    unsupported: tuple[str, ...] = ()


__all__: list[str] = [
    "ClientIR",
    "FieldIR",
    "OperationIR",
    "ParameterIR",
    "SchemaIR",
    "SchemaKind",
    "SpecIR",
]
