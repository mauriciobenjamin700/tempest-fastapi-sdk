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

import re
import textwrap
from collections.abc import Mapping
from typing import Any

from tempest_fastapi_sdk.openapi.ir import FieldIR, SchemaIR, SpecIR

_TYPING_IMPORTS: tuple[str, ...] = ("Annotated", "Any")
_DATETIME_IMPORTS: tuple[str, ...] = ("date", "datetime", "time")

_MAX_LINE: int = 88
"""Line budget matching the project's ruff configuration."""


def _literal(value: Any) -> str:
    """Render a JSON value as Python source.

    Args:
        value (Any): A default or example value from the specification.

    Returns:
        str: Source text. ``repr`` is exact for the JSON value subset, and
        produces the double-quoted strings the project requires for every
        value that has no embedded quote.
    """
    rendered = repr(value)
    if isinstance(value, str) and "'" in rendered and '"' not in value:
        return f'"{value}"'
    return rendered


def _docstring_lines(summary: str, indent: str) -> list[str]:
    """Render a one-line-or-wrapped docstring summary.

    Args:
        summary (str): The summary sentence.
        indent (str): Leading whitespace for the docstring.

    Returns:
        list[str]: Source lines, using the one-line form when it fits.
    """
    single = f'{indent}"""{summary}"""'
    if len(single) <= _MAX_LINE:
        return [single]
    lines = [f'{indent}"""{summary}']
    lines.append(f'{indent}"""')
    return lines


def _wrap(text: str, indent: str, first_prefix: str = "") -> list[str]:
    """Wrap prose to the line budget.

    Args:
        text (str): The prose to wrap.
        indent (str): Indentation for continuation lines.
        first_prefix (str): Text prefixed to the first line (e.g. an
            ``Attributes:`` entry's ``"name (type): "``).

    Returns:
        list[str]: Wrapped source lines.
    """
    body = f"{first_prefix}{text}"
    continuation = f"{indent}    "
    wrapped = textwrap.wrap(
        body,
        width=_MAX_LINE,
        initial_indent=indent,
        subsequent_indent=continuation,
        break_long_words=False,
        break_on_hyphens=False,
    )
    return wrapped or [f"{indent}{body}"]


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
    """
    arguments: list[str] = []
    if field.alias is not None:
        arguments.append(f"alias={_literal(field.alias)}")
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
        ``Field()`` call.

    Notes:
        When the default is the only thing to emit, it is written as a bare
        assignment rather than wrapped in an otherwise-empty ``Field()``
        call.
    """
    arguments = _field_arguments(field)
    if not arguments:
        return [f"    {field.name}: {field.annotation}"]

    if (
        len(arguments) == 1
        and field.default is not None
        and not field.default_is_factory
    ):
        return [f"    {field.name}: {field.annotation} = {field.default}"]

    single = f"    {field.name}: {field.annotation} = Field({', '.join(arguments)})"
    if len(single) <= _MAX_LINE:
        return [single]
    lines = [f"    {field.name}: {field.annotation} = Field("]
    lines.extend(f"        {argument}," for argument in arguments)
    lines.append("    )")
    return lines


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
        lines.append(f'    """{docstring}')
        lines.extend(body)
        lines.append('    """')
    else:
        lines.extend(_docstring_lines(docstring, "    "))
    lines.append("")

    if schema.needs_populate_by_name:
        lines.extend(
            [
                "    model_config = ConfigDict(populate_by_name=True)",
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


def _enum_lines(schema: SchemaIR) -> list[str]:
    """Render one enum class.

    Args:
        schema (SchemaIR): The enum to render.

    Returns:
        list[str]: Source lines for the class.
    """
    base = "BaseStrEnum" if schema.kind == "str_enum" else "BaseIntEnum"
    lines = [f"class {schema.name}({base}):"]
    lines.extend(_docstring_lines(schema.docstring, "    "))
    lines.append("")
    for member, value in schema.enum_members:
        lines.append(f"    {member} = {_literal(value)}")
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
        f"{field.annotation}" for schema in spec.schemas for field in schema.fields
    )
    needs_field = any(_field_arguments(f) for s in spec.schemas for f in s.fields)
    needs_config = any(s.needs_populate_by_name for s in spec.schemas)
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
        lines.extend(
            _enum_lines(schema) if schema.kind != "model" else _model_lines(schema)
        )
        lines.append("")

    if spec.cyclic:
        lines.extend(
            [
                "# These models reference each other, so their forward",
                "# references cannot resolve while the classes are being",
                "# created. Rebuilding here completes them at import time.",
            ]
        )
        for name in sorted(spec.cyclic):
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
