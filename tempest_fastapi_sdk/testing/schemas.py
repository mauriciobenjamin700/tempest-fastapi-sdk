"""Check that every exported schema field documents itself.

The OpenAPI document is what a client reads, and a field without a
``title``, a ``description`` and an ``examples`` list reaches it as a bare
name and type. In the transport service that #616 measured, 370 fields
were exported that way. Prose in the project's ``CLAUDE.md`` did not keep
them documented; a test does::

    import src.schemas
    from tempest_fastapi_sdk.testing import assert_schema_metadata


    def test_exported_schemas_carry_metadata() -> None:
        assert_schema_metadata(src.schemas)

A **leaf** field (a value: ``str``, ``int``, an enum, ``list[str]``) needs
all three. A field whose type holds another schema (``AddressSchema``,
``list[ItemSchema]``, ``ItemSchema | None``) needs only ``title`` and
``description``: its examples are the nested schema's own.

Each field is reported once, on the class that declares it, however many
exported schemas inherit it — a base the module does not export is still
checked through its subclasses. The check imports nothing itself: it
reads the module object it is given, so the project decides how ``src``
gets on the path.
"""

from __future__ import annotations

import inspect
import typing
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from pydantic import BaseModel
from pydantic.fields import FieldInfo

LEAF_METADATA: tuple[str, ...] = ("title", "description", "examples")
"""What a field holding a plain value must declare."""

NESTED_METADATA: tuple[str, ...] = ("title", "description")
"""What a field holding another schema must declare."""


@dataclass(frozen=True)
class SchemaMetadataFinding:
    """One field that is missing part of its documentation.

    Attributes:
        schema (str): ``module.ClassName`` of the class declaring the
            field.
        field (str): The field name.
        missing (tuple[str, ...]): The metadata it lacks, in
            :data:`LEAF_METADATA` order.
    """

    schema: str
    field: str
    missing: tuple[str, ...]

    def render(self) -> str:
        """Format the finding as ``module.Class.field: missing a, b``.

        Returns:
            str: The one-line report.
        """
        return f"{self.schema}.{self.field}: missing {', '.join(self.missing)}"


def _exported_schemas(module: ModuleType) -> list[type[BaseModel]]:
    """List the Pydantic models a module exports.

    Args:
        module (ModuleType): A module or package, usually the project's
            ``schemas`` package.

    Returns:
        list[type[BaseModel]]: The classes named in ``__all__`` (every
        public attribute when there is none) that subclass
        :class:`pydantic.BaseModel`, in that order.
    """
    names: list[str] = list(
        getattr(module, "__all__", None)
        or [name for name in vars(module) if not name.startswith("_")]
    )
    schemas: list[type[BaseModel]] = []
    for name in names:
        value = getattr(module, name, None)
        if (
            inspect.isclass(value)
            and issubclass(value, BaseModel)
            and value not in schemas
        ):
            schemas.append(value)
    return schemas


def _holds_schema(annotation: Any) -> bool:
    """Tell whether a field type contains a Pydantic model at any depth.

    Args:
        annotation (Any): The field's annotation (``FieldInfo.annotation``).

    Returns:
        bool: True for ``Schema``, ``list[Schema]``, ``Schema | None``,
        ``dict[str, Schema]`` and the like.
    """
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        return True
    origin = typing.get_origin(annotation)
    if inspect.isclass(origin) and issubclass(origin, BaseModel):
        return True
    return any(_holds_schema(argument) for argument in typing.get_args(annotation))


def _declaring_class(schema: type[BaseModel], field: str) -> type[BaseModel]:
    """Return the class in a schema's MRO whose own body declares a field.

    Args:
        schema (type[BaseModel]): The exported schema.
        field (str): The field name.

    Returns:
        type[BaseModel]: The first class in the MRO whose own annotations
        name the field; ``schema`` itself when none does.
    """
    for klass in schema.__mro__:
        if (
            inspect.isclass(klass)
            and issubclass(klass, BaseModel)
            and field in inspect.get_annotations(klass)
        ):
            return klass
    return schema


def _missing_metadata(info: FieldInfo) -> tuple[str, ...]:
    """List what a field's documentation lacks.

    Args:
        info (FieldInfo): The field.

    Returns:
        tuple[str, ...]: Empty when complete. A blank ``title`` or
        ``description`` and an empty ``examples`` list count as missing.
    """
    required = NESTED_METADATA if _holds_schema(info.annotation) else LEAF_METADATA
    present: dict[str, bool] = {
        "title": bool((info.title or "").strip()),
        "description": bool((info.description or "").strip()),
        "examples": bool(info.examples),
    }
    return tuple(name for name in required if not present[name])


def schema_metadata_findings(module: ModuleType) -> list[SchemaMetadataFinding]:
    """Check every field of every schema a module exports.

    Args:
        module (ModuleType): A module or package, usually the project's
            ``schemas`` package.

    Returns:
        list[SchemaMetadataFinding]: One finding per undocumented field,
        each on the class declaring it, sorted by schema and field. Empty
        when every field is documented.
    """
    findings: dict[tuple[str, str], SchemaMetadataFinding] = {}
    for schema in _exported_schemas(module):
        for name, info in schema.model_fields.items():
            missing = _missing_metadata(info)
            if not missing:
                continue
            owner = _declaring_class(schema, name)
            key = (f"{owner.__module__}.{owner.__qualname__}", name)
            findings[key] = SchemaMetadataFinding(
                schema=key[0], field=name, missing=missing
            )
    return [findings[key] for key in sorted(findings)]


def assert_schema_metadata(module: ModuleType) -> None:
    """Fail when an exported schema field is missing its documentation.

    Args:
        module (ModuleType): A module or package, usually the project's
            ``schemas`` package.

    Raises:
        AssertionError: Listing one line per undocumented field.
    """
    findings = schema_metadata_findings(module)
    if findings:
        lines = "\n".join(f"  {finding.render()}" for finding in findings)
        raise AssertionError(
            f"{len(findings)} schema field(s) without title/description/examples "
            f"in {module.__name__}:\n{lines}"
        )


__all__: list[str] = [
    "LEAF_METADATA",
    "NESTED_METADATA",
    "SchemaMetadataFinding",
    "assert_schema_metadata",
    "schema_metadata_findings",
]
