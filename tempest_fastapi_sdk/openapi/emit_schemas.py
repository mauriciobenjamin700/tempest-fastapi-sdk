"""Render :class:`~tempest_fastapi_sdk.openapi.ir.SchemaIR` into ``schemas.py``.

The output has to read like code a careful person wrote by hand, because
that is the whole point of the generator: full type annotations, double
quotes, Google-style docstrings, and every ``Field`` carrying the
``title`` / ``description`` / ``examples`` the specification provided. That
last part is the issue's actual request — the generated module is where the
integration's documentation ends up living, so it survives the third party
changing or taking down their docs site.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from typing import Any

from tempest_fastapi_sdk.openapi.ir import FieldIR, SchemaIR, SpecIR
from tempest_fastapi_sdk.openapi.source import (
    MAX_LINE as _MAX_LINE,
)
from tempest_fastapi_sdk.openapi.source import (
    docstring_delimiter as _delimiter,
)
from tempest_fastapi_sdk.openapi.source import (
    string_chunks as _string_chunks,
)
from tempest_fastapi_sdk.openapi.source import (
    string_literal as _string_literal,
)
from tempest_fastapi_sdk.openapi.source import (
    unsupported_comment as _unsupported_comment,
)
from tempest_fastapi_sdk.openapi.source import (
    wrap as _wrap,
)

_TYPING_IMPORTS: tuple[str, ...] = ("Annotated", "Any")
_DATETIME_IMPORTS: tuple[str, ...] = ("date", "datetime", "time")


def _literal(value: Any) -> str:
    """Render a JSON value as Python source.

    Args:
        value (Any): A default or example value from the specification.

    Returns:
        str: Source text. Strings go through :func:`_string_literal`; lists
        and dicts are rebuilt element by element so the strings nested inside
        an object example get the same treatment; every remaining value in
        the JSON subset is exact under ``repr``.
    """
    if isinstance(value, str):
        return _string_literal(value)
    if isinstance(value, list):
        return "[" + ", ".join(_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        items = ", ".join(
            f"{_literal(key)}: {_literal(item)}" for key, item in value.items()
        )
        return "{" + items + "}"
    return repr(value)


def _docstring_lines(summary: str, indent: str) -> list[str]:
    """Render a one-line-or-wrapped docstring summary.

    Args:
        summary (str): The summary sentence.
        indent (str): Leading whitespace for the docstring.

    Returns:
        list[str]: Source lines, using the one-line form when it fits and
        wrapping the summary otherwise. A specification is free to write a
        two-sentence ``summary``; leaving it on one line put the overrun
        inside a docstring, which ``ruff format`` does not rewrap.

    Wrapping to a single content line does not survive the formatter.
    ``ruff format`` pulls the closing ``\"\"\"`` back up onto a docstring
    whose content is one line, and it does so **without checking the line
    budget**: measured on Mercado Pago's
    ``Allowed values for OrderTransactionPaymentPaymentMethodTransaction
    SecurityStatus.``, an 88-column content line came back at 91 and failed
    ``E501`` on generated code. So when the wrap collapses to one line, this
    re-wraps three columns narrower to force a second one, which the
    formatter leaves alone.
    """
    opening = _delimiter(summary)
    single = f'{indent}{opening}{summary}"""'
    if len(single) <= _MAX_LINE:
        return [single]
    lines = _wrap(summary, indent, opening, hanging=False)
    if len(lines) == 1:
        lines = _wrap(summary, indent, opening, hanging=False, budget=_MAX_LINE - 3)
    lines.append(f'{indent}"""')
    return lines


def _attributes_section(schema: SchemaIR) -> list[str]:
    """Render the ``Attributes:`` section of a model docstring.

    Args:
        schema (SchemaIR): The model being emitted.

    Returns:
        list[str]: Source lines, or empty when the model has no fields.
    """
    if not schema.fields:
        return []
    lines = ["", "    Attributes:"]
    for field in schema.fields:
        description = field.description or field.title or "Undocumented in the spec."
        first = f"{field.name} ({field.annotation}): "
        lines.extend(_wrap(description, "        ", first))
    return lines


def _field_arguments(field: FieldIR) -> list[str]:
    """Build the keyword arguments for a field's ``Field(...)`` call.

    Args:
        field (FieldIR): The field being emitted.

    Returns:
        list[str]: Rendered ``name=value`` fragments, ordered so the most
        useful metadata reads first.

    The wire name is emitted as ``validation_alias`` **plus**
    ``serialization_alias``, never as the single ``alias``. The three do the
    same thing at runtime here, but ``alias`` renames the parameter in the
    ``__init__`` a type-checker synthesizes: pyright then rejects
    ``ChargePayload(correlation_id=...)`` with *No parameter named
    "correlation_id"* and demands the wire spelling instead. Measured against
    the published wheel, with ``populate_by_name=True`` already set on the
    model — and measured again with ``validate_by_name``, which does not fix
    it either. The split form keeps the Python name in the signature while
    validation still accepts the wire spelling and
    ``model_dump(by_alias=True)`` still emits it.
    """
    arguments: list[str] = []
    if field.alias is not None:
        arguments.append(f"validation_alias={_literal(field.alias)}")
        arguments.append(f"serialization_alias={_literal(field.alias)}")
    if field.title:
        arguments.append(f"title={_literal(field.title)}")
    if field.description:
        arguments.append(f"description={_literal(field.description)}")
    if field.examples:
        rendered = ", ".join(_literal(example) for example in field.examples)
        arguments.append(f"examples=[{rendered}]")
    for name, value in field.constraints.items():
        arguments.append(f"{name}={_literal(value)}")
    if field.default is not None:
        if field.default_is_factory:
            arguments.append(f"default_factory={field.default}")
        else:
            arguments.append(f"default={field.default}")
    return arguments


def _field_lines(field: FieldIR) -> list[str]:
    """Render one model field.

    Args:
        field (FieldIR): The field to render.

    Returns:
        list[str]: Source lines. A field with no metadata and no default
        is emitted as a bare annotation rather than an empty
        ``Field()`` call. When the specification carried something the
        parser could not model, the lines are preceded by an
        ``# openapi: unsupported`` comment naming the gap — otherwise the
        reader sees an ``Any`` with no way to learn where it came from.

    Notes:
        When the default is the only thing to emit, it is written as a bare
        assignment rather than wrapped in an otherwise-empty ``Field()``
        call.

        A long **annotation** is handled by :func:`_assignment_lines`, not
        here. The generator synthesizes its own class names for inline
        schemas by concatenating the path
        (``PostApiV1DecodeEmvResponseEmvMerchantAccountInformationPix``), so
        the annotation alone can overrun before any argument is reached.
    """
    marker = _unsupported_comment(field.unsupported, "    ")
    arguments = _field_arguments(field)
    if not arguments:
        return [*marker, *_bare_annotation_lines(field)]

    if (
        len(arguments) == 1
        and field.default is not None
        and not field.default_is_factory
    ):
        return [*marker, *_bare_annotation_lines(field, value=field.default)]

    single = f"    {field.name}: {field.annotation} = Field({', '.join(arguments)})"
    if len(single) <= _MAX_LINE:
        return [*marker, single]
    return [*marker, *_assignment_lines(field.name, field.annotation, arguments)]


def _bare_annotation_lines(field: FieldIR, *, value: str | None = None) -> list[str]:
    """Render a field with no ``Field()`` call.

    Args:
        field (FieldIR): The field to render.
        value (str | None): Rendered default, or ``None`` for a required
            field that gets a bare annotation.

    Returns:
        list[str]: Source lines, with the annotation parenthesized when the
        flat form overruns the budget.

    ``ruff format`` does not rescue either form: it will not wrap the right
    side of ``x: T = None`` (``None`` is a single atom, and the redundant
    parentheses come straight back off), and a bare annotation has no right
    side to wrap at all. It **does** preserve a parenthesized annotation
    once nothing shorter fits, which is the only form that stays inside the
    budget here.
    """
    suffix = "" if value is None else f" = {value}"
    flat = f"    {field.name}: {field.annotation}{suffix}"
    if len(flat) <= _MAX_LINE:
        return [flat]
    return _broken_annotation(field.name, field.annotation, suffix)


def _whole_subscript(annotation: str) -> tuple[str, str] | None:
    """Split an annotation that is one subscript covering its whole text.

    Args:
        annotation (str): The rendered type annotation.

    Returns:
        tuple[str, str] | None: ``(outer, inner)`` for ``list[Item]``, or
        ``None`` when the annotation is anything else — a bare name, or a
        union like ``list[Item] | None`` whose top level is not the
        subscript.

    ``ruff format`` breaks inside the brackets it already has rather than
    adding parentheses, so ``list[VeryLongItem]`` becomes ``list[`` /
    ``VeryLongItem`` / ``]``. Emitting the parenthesized form there is
    stable Python that still fails ``ruff format --check``.
    """
    start = annotation.find("[")
    if start <= 0 or not annotation.endswith("]"):
        return None
    depth = 0
    for index, char in enumerate(annotation):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0 and index != len(annotation) - 1:
                return None
    if depth != 0:
        return None
    return annotation[:start], annotation[start + 1 : -1]


def _broken_annotation(name: str, annotation: str, tail: str) -> list[str]:
    """Render ``name: annotation`` across lines, followed by ``tail``.

    Args:
        name (str): The Python field name.
        annotation (str): The rendered type annotation.
        tail (str): Text closing the statement — ``" = Field("``,
            ``" = None"``, or empty for a bare annotation.

    Returns:
        list[str]: Source lines in the shape ``ruff format`` settles on:
        inside the brackets for a whole subscript, parenthesized otherwise.
    """
    subscript = _whole_subscript(annotation)
    if subscript is not None:
        outer, inner = subscript
        return [f"    {name}: {outer}[", f"        {inner}", f"    ]{tail}"]
    return [f"    {name}: (", f"        {annotation}", f"    ){tail}"]


def _assignment_lines(name: str, annotation: str, arguments: list[str]) -> list[str]:
    """Render ``name: annotation = Field(...)`` across lines.

    Args:
        name (str): The Python field name.
        annotation (str): The rendered type annotation.
        arguments (list[str]): Rendered ``name=value`` fragments.

    Returns:
        list[str]: Source lines, in whichever of three shapes ``ruff
        format`` would settle on.

    The shape matters because the emitter pre-splits long strings against a
    known indentation, and two of these shapes indent the arguments
    differently. Getting it wrong is not cosmetic: ``ruff format`` re-indents
    the arguments one level deeper, every pre-split chunk gains four columns,
    and lines the emitter had fitted to 88 come out at 92.

    ``ruff format`` picks the first shape that fits, so the emitter has to
    apply the same order or the file fails ``ruff format --check``:

    1. ``x: T = Field(`` with arguments at 8.
    2. ``x: T = (`` / ``Field(`` with arguments at 12, when the head still
       overruns but the assignment's own line fits.
    3. A parenthesized annotation with arguments back at 8, when neither
       fits — the only shape left once the annotation alone is too long.
    """
    head = f"    {name}: {annotation} = Field("
    if len(head) <= _MAX_LINE:
        return [head, *_arguments_block(arguments, "        "), "    )"]

    wrapped = f"    {name}: {annotation} = ("
    if len(wrapped) <= _MAX_LINE:
        return [
            wrapped,
            "        Field(",
            *_arguments_block(arguments, "            "),
            "        )",
            "    )",
        ]

    return [
        *_broken_annotation(name, annotation, " = Field("),
        *_arguments_block(arguments, "        "),
        "    )",
    ]


def _arguments_block(arguments: list[str], indent: str) -> list[str]:
    """Render every ``Field`` argument at one indentation.

    Args:
        arguments (list[str]): Rendered ``name=value`` fragments.
        indent (str): Leading whitespace for each argument.

    Returns:
        list[str]: Source lines, each argument split when it overruns.
    """
    lines: list[str] = []
    for argument in arguments:
        lines.extend(_argument_lines(argument, indent))
    return lines


def _argument_lines(argument: str, indent: str) -> list[str]:
    """Render one ``Field`` keyword argument, split when it overruns the line.

    Args:
        argument (str): A rendered ``name=value`` fragment.
        indent (str): Leading whitespace for the argument.

    Returns:
        list[str]: Source lines, ending with the trailing comma. A long
        string value becomes a parenthesized run of **at least two**
        adjacent literals — what a person writes, and what ``ruff format``
        leaves alone, where it joins a lone parenthesized literal back onto
        the long line.

    ``ruff format`` never breaks a string, so a description long enough to
    overrun the budget survives the format pass and fails the consumer's
    own ``E501``.
    """
    flat = f"{indent}{argument},"
    if len(flat) <= _MAX_LINE:
        return [flat]
    name, separator, literal = argument.partition("=")
    if not separator:
        return [flat]
    try:
        value = ast.literal_eval(literal)
    except (SyntaxError, ValueError):
        return [flat]

    if isinstance(value, str):
        chunks = _string_chunks(value, _MAX_LINE - len(indent) - 4)
        lines = [f"{indent}{name}=("]
        lines.extend(f"{indent}    {_string_literal(chunk)}" for chunk in chunks)
        lines.append(f"{indent}),")
        return lines

    if isinstance(value, list | dict):
        body = _value_lines(value, indent, prefix=f"{name}=")
        body[-1] = f"{body[-1]},"
        return body

    return [flat]


def _value_lines(value: Any, indent: str, *, prefix: str = "") -> list[str]:
    """Render a JSON value across lines, exploding it only where needed.

    Args:
        value (Any): A default or example value from the specification.
        indent (str): Leading whitespace for the value's first line.
        prefix (str): Text between the indent and the value on its first
            line — a keyword (``examples=``) or a dict key (``"k": ``).

    Returns:
        list[str]: Source lines, the first carrying ``prefix`` and the
        opening bracket, the last the closing one. A value whose flat form
        fits comes back on one line, so only the containers that actually
        overrun are exploded.

    ``prefix`` is measured, not merely prepended. Rendering the value alone
    and gluing the key on afterwards checks the wrong string: a dict entry
    whose value fits on its own can still overrun once ``"OPENPIX:…": ``
    sits in front of it, which is how two lines survived the first fix.

    ``examples`` on a webhook field can carry a list of objects that renders
    to a couple of hundred characters. ``ruff format`` would break it — a
    container is not a string — so this only ever showed up under
    ``--no-format``; the emitter still owes the caller output that lints
    without a fixing pass.
    """
    flat = f"{indent}{prefix}{_literal(value)}"
    if len(flat) <= _MAX_LINE:
        return [flat]

    inner = f"{indent}    "
    if isinstance(value, list):
        lines = [f"{indent}{prefix}["]
        for item in value:
            part = _value_lines(item, inner)
            part[-1] = f"{part[-1]},"
            lines.extend(part)
        lines.append(f"{indent}]")
        return lines

    if isinstance(value, dict):
        lines = [f"{indent}{prefix}{{"]
        for key, item in value.items():
            part = _value_lines(item, inner, prefix=f"{_literal(key)}: ")
            part[-1] = f"{part[-1]},"
            lines.extend(part)
        lines.append(f"{indent}}}")
        return lines

    return [flat]


def _model_lines(schema: SchemaIR) -> list[str]:
    """Render one Pydantic model class.

    Args:
        schema (SchemaIR): The model to render.

    Returns:
        list[str]: Source lines for the class.

    Notes:
        A model with only a docstring is already a complete class body, so
        no ``pass`` is emitted. The comment that goes in its place records
        that the emptiness came from the specification, not from a parsing
        failure.
    """
    lines = [f"class {schema.name}(BaseSchema):"]
    docstring = f"{schema.docstring}"
    body = _attributes_section(schema)
    if body:
        opening = _delimiter(docstring, *body)
        lines.extend(_wrap(docstring, "    ", opening, hanging=False))
        lines.extend(body)
        lines.append('    """')
    else:
        lines.extend(_docstring_lines(docstring, "    "))
    lines.append("")

    arguments = schema.config_arguments
    if arguments:
        lines.extend(
            [
                f"    model_config = ConfigDict({', '.join(arguments)})",
                "",
            ]
        )

    if not schema.fields:
        lines.append("    # The specification declares no properties.")
        lines.append("")
        return lines

    for field in schema.fields:
        lines.extend(_field_lines(field))
    lines.append("")
    return lines


def _alias_lines(schema: SchemaIR) -> list[str]:
    """Render one union alias assignment.

    Args:
        schema (SchemaIR): The alias to render.

    Returns:
        list[str]: Source lines for the assignment and its docstring.

    The output mirrors what ``ruff format`` produces: one line while it
    fits in the line budget, otherwise parenthesized with the ``|`` leading
    each continuation. Emitting the wrapped form unconditionally would make
    generation with ``run_format=False`` differ from generation with it,
    and the drift test compares bytes.
    """
    flat = f"{schema.name} = {schema.alias_target}"
    if len(flat) <= _MAX_LINE:
        lines = [flat]
    else:
        members = [member.strip() for member in schema.alias_target.split("|")]
        lines = [f"{schema.name} = (", f"    {members[0]}"]
        lines.extend(f"    | {member}" for member in members[1:])
        lines.append(")")
    lines.extend(_docstring_lines(schema.docstring, ""))
    lines.append("")
    return lines


def _enum_lines(schema: SchemaIR) -> list[str]:
    """Render one enum class.

    Args:
        schema (SchemaIR): The enum to render.

    Returns:
        list[str]: Source lines for the class.

    A member whose flat assignment overruns the budget has its value split
    into a parenthesized run of adjacent literals. The run always holds at
    least two: ``ruff format`` drops the parentheses around a lone literal
    and puts the long line straight back, which is why the first attempt at
    this — a single parenthesized value — still failed
    ``ruff format --check``. The member **name** is capped upstream in
    :func:`~tempest_fastapi_sdk.openapi.naming.enum_member_name`, since a
    long enough one overruns before the value is reached at all.
    """
    base = "BaseStrEnum" if schema.kind == "str_enum" else "BaseIntEnum"
    lines = [f"class {schema.name}({base}):"]
    lines.extend(_docstring_lines(schema.docstring, "    "))
    lines.append("")
    for member, value in schema.enum_members:
        assignment = f"    {member} = {_literal(value)}"
        if len(assignment) <= _MAX_LINE or not isinstance(value, str):
            lines.append(assignment)
            continue
        chunks = _string_chunks(value, _MAX_LINE - 8)
        lines.append(f"    {member} = (")
        lines.extend(f"        {_string_literal(chunk)}" for chunk in chunks)
        lines.append("    )")
    lines.append("")
    return lines


def _collect_imports(spec: SpecIR) -> list[str]:
    """Determine the import block the generated module needs.

    Args:
        spec (SpecIR): The parsed specification.

    Returns:
        list[str]: Source lines for the import block, alphabetically
        ordered within each group so ``ruff check`` accepts the output
        without a fixing pass.
    """
    rendered = "\n".join(
        [
            *(f.annotation for schema in spec.schemas for f in schema.fields),
            *(s.alias_target for s in spec.schemas if s.alias_target),
        ]
    )
    needs_field = any(_field_arguments(f) for s in spec.schemas for f in s.fields)
    needs_config = any(s.config_arguments for s in spec.schemas)
    has_models = any(s.kind == "model" for s in spec.schemas)
    has_str_enum = any(s.kind == "str_enum" for s in spec.schemas)
    has_int_enum = any(s.kind == "int_enum" for s in spec.schemas)

    stdlib: dict[str, list[str]] = {}
    datetime_names = [n for n in _DATETIME_IMPORTS if _uses(rendered, n)]
    if datetime_names:
        stdlib["datetime"] = datetime_names
    if _uses(rendered, "Decimal"):
        stdlib["decimal"] = ["Decimal"]
    typing_names = [n for n in _TYPING_IMPORTS if _uses(rendered, n)]
    if typing_names:
        stdlib["typing"] = typing_names
    if _uses(rendered, "UUID"):
        stdlib["uuid"] = ["UUID"]

    pydantic_names: list[str] = []
    if needs_config:
        pydantic_names.append("ConfigDict")
    if _uses(rendered, "EmailStr"):
        pydantic_names.append("EmailStr")
    if needs_field:
        pydantic_names.append("Field")

    sdk_names: list[str] = []
    if has_int_enum:
        sdk_names.append("BaseIntEnum")
    if has_models:
        sdk_names.append("BaseSchema")
    if has_str_enum:
        sdk_names.append("BaseStrEnum")

    third_party: dict[str, list[str]] = {}
    if pydantic_names:
        third_party["pydantic"] = pydantic_names
    if sdk_names:
        third_party["tempest_fastapi_sdk"] = sdk_names

    return _import_block(stdlib, third_party)


def _import_block(
    stdlib: Mapping[str, list[str]],
    third_party: Mapping[str, list[str]],
) -> list[str]:
    """Render an isort-clean import block.

    Emitting the groups in isort's own order — ``__future__``, standard
    library, third party — with modules sorted and one line per module is
    what lets the generated file pass ``ruff check`` **without** the
    post-generation ``ruff`` pass. That matters because ``--no-format``
    must still produce lint-clean code, and because ruff may not be
    installed at all.

    Args:
        stdlib (Mapping[str, list[str]]): Standard-library module to the
            names imported from it.
        third_party (Mapping[str, list[str]]): Third-party module to the
            names imported from it.

    Returns:
        list[str]: Source lines, ending with two blank lines so the first
        top-level definition has the spacing ``ruff format`` expects.
    """
    lines: list[str] = ["from __future__ import annotations", ""]
    for group in (stdlib, third_party):
        if not group:
            continue
        for module in sorted(group):
            names = ", ".join(sorted(group[module]))
            lines.append(f"from {module} import {names}")
        lines.append("")
    lines.append("")
    return lines


def _uses(rendered: str, name: str) -> bool:
    """Report whether a type name appears in the rendered annotations.

    Args:
        rendered (str): Every annotation, newline-joined.
        name (str): The type name to look for.

    Returns:
        bool: ``True`` when the name appears as a whole word.
    """
    return re.search(rf"\b{re.escape(name)}\b", rendered) is not None


def emit_schemas(spec: SpecIR, *, title: str) -> str:
    """Render the generated ``schemas.py`` module.

    Args:
        spec (SpecIR): The parsed specification.
        title (str): The integration's human name, used in the module
            docstring.

    Returns:
        str: The complete module source, ending in a newline.
    """
    header = [
        f'"""Pydantic schemas generated from the {title} OpenAPI specification.',
        "",
        "Do not edit by hand — rerun `tempest openapi-client` to refresh.",
        "",
        "Field names are Python-idiomatic; the wire name is attached as a",
        "Pydantic ``alias`` whenever the two differ, and every model enables",
        "``populate_by_name`` so both spellings are accepted on input. Call",
        "``model_dump(by_alias=True)`` to serialize back to the wire shape.",
        '"""',
        "",
    ]
    lines = [*header, *_collect_imports(spec)]

    for schema in spec.schemas:
        if schema.kind == "alias":
            lines.extend(_alias_lines(schema))
        elif schema.kind == "model":
            lines.extend(_model_lines(schema))
        else:
            lines.extend(_enum_lines(schema))
        lines.append("")

    rebuildable = sorted(
        spec.cyclic & {s.name for s in spec.schemas if s.kind == "model"}
    )
    if rebuildable:
        lines.extend(
            [
                "# These models reference each other, so their forward",
                "# references cannot resolve while the classes are being",
                "# created. Rebuilding here completes them at import time.",
            ]
        )
        for name in rebuildable:
            lines.append(f"{name}.model_rebuild()")
        lines.append("")

    exported = sorted(schema.name for schema in spec.schemas)
    lines.append("__all__: list[str] = [")
    lines.extend(f'    "{name}",' for name in exported)
    lines.append("]")

    return _normalize(lines)


def _normalize(lines: list[str]) -> str:
    """Collapse runs of blank lines and terminate the file with a newline.

    Args:
        lines (list[str]): The emitted source lines.

    Returns:
        str: The module source, with at most two consecutive blank lines
        so ``ruff format`` has nothing to change.
    """
    output: list[str] = []
    blanks = 0
    for line in lines:
        if line.strip():
            blanks = 0
            output.append(line.rstrip())
            continue
        blanks += 1
        if blanks <= 2:
            output.append("")
    while output and not output[-1]:
        output.pop()
    return "\n".join(output) + "\n"


__all__: list[str] = [
    "emit_schemas",
]
