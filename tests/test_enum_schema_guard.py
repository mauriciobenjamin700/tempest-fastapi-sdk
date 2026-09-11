"""Every enum a schema field carries must be ``str``-based.

``BaseSchema`` sets ``use_enum_values=True``, so a field holds the
enum's *value*, not the member. Measured here: with a ``StrEnum`` the
value still compares equal to the member (``==`` works, ``is`` does
not); with a plain ``Enum`` **even ``==`` is False**, because the field
holds a ``str`` and the member is not one.

Both mistakes pass the type-checker, run without raising, and silently
never enter the branch. A reviewer cannot see it either — the guard
below is the only mechanical check available, so it walks the SDK's own
schemas and refuses a non-``str`` enum annotation.
"""

from __future__ import annotations

import enum
import importlib
import pkgutil
from types import ModuleType
from typing import Any, get_args, get_origin

import tempest_fastapi_sdk
from tempest_fastapi_sdk.schemas.base import BaseSchema


def _iter_modules() -> list[ModuleType]:
    """Import every importable module of the package.

    A module whose optional extra is missing is skipped rather than
    failed: the guard is about annotations, not about the install.

    Returns:
        list[ModuleType]: The modules that imported cleanly.
    """
    modules: list[ModuleType] = [tempest_fastapi_sdk]
    for info in pkgutil.walk_packages(
        tempest_fastapi_sdk.__path__,
        prefix="tempest_fastapi_sdk.",
    ):
        try:
            modules.append(importlib.import_module(info.name))
        except Exception:
            continue
    return modules


def _enum_annotations(annotation: Any) -> list[type[enum.Enum]]:
    """Collect every enum class reachable from one annotation.

    Args:
        annotation (Any): A field annotation, possibly a union or a
            generic container.

    Returns:
        list[type[enum.Enum]]: The enum classes it mentions.
    """
    found: list[type[enum.Enum]] = []
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return [annotation]
    if get_origin(annotation) is not None:
        for arg in get_args(annotation):
            found.extend(_enum_annotations(arg))
    return found


def _schema_classes() -> list[type[BaseSchema]]:
    """Return every ``BaseSchema`` subclass defined in this package.

    Returns:
        list[type[BaseSchema]]: The schema classes, de-duplicated.
    """
    seen: dict[str, type[BaseSchema]] = {}
    for module in _iter_modules():
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseSchema)
                and value is not BaseSchema
                and value.__module__.startswith("tempest_fastapi_sdk")
            ):
                seen[f"{value.__module__}.{value.__qualname__}"] = value
    return list(seen.values())


class TestSchemaEnumsAreStrBased:
    """The guard itself."""

    def test_no_schema_field_carries_a_non_str_enum(self) -> None:
        offenders: list[str] = []
        for schema in _schema_classes():
            for name, field in schema.model_fields.items():
                for enum_class in _enum_annotations(field.annotation):
                    if not issubclass(enum_class, str):
                        offenders.append(
                            f"{schema.__module__}.{schema.__qualname__}.{name}"
                            f" -> {enum_class.__qualname__}",
                        )

        assert offenders == [], (
            "these schema fields annotate a non-str enum; under "
            "use_enum_values the field holds the value, so `== Member` is "
            "False and the branch never runs: " + ", ".join(offenders)
        )

    def test_the_guard_sees_something(self) -> None:
        """A guard that walks nothing passes vacuously."""
        assert len(_schema_classes()) > 20


class TestTheMeasuredBehaviour:
    """Pinned so the advice in ``BaseSchema`` stays true."""

    def test_str_enum_survives_equality_but_not_identity(self) -> None:
        class Kind(enum.StrEnum):
            GROUP = "group"

        class Payload(BaseSchema):
            kind: Kind

        value = Payload(kind=Kind.GROUP).kind

        assert value == Kind.GROUP
        assert value is not Kind.GROUP
        assert value in {Kind.GROUP}

    def test_plain_enum_loses_equality_too(self) -> None:
        class Plain(enum.Enum):
            GROUP = "group"

        class Payload(BaseSchema):
            kind: Plain

        value = Payload(kind=Plain.GROUP).kind

        assert value == "group"
        assert value != Plain.GROUP

    def test_base_str_enum_is_str_based(self) -> None:
        from tempest_fastapi_sdk import BaseStrEnum

        assert issubclass(BaseStrEnum, str)
