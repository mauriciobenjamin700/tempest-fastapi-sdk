"""Drop the enum components FastAPI publishes and nothing references.

FastAPI walks every field of every model a route uses and adds a component
to ``components.schemas`` for each ``Enum`` it finds in an annotation
(``fastapi._compat.v2.get_flat_models_from_field``), whether or not any
``$ref`` ends up pointing at it. A field whose JSON schema inlines a
narrower enum — :func:`~tempest_fastapi_sdk.utils.fields.image_media_type_field`
publishes only the accepted ``ImageMediaTypeEnum`` values — therefore leaves
the full enum behind as an orphan component, and a client generator turns
it into a dead type.

:func:`prune_orphan_enum_schemas` removes those orphans from the document
the app serves. Only enum components go: an unreferenced object schema may
have been added on purpose, an unreferenced enum never carries a contract.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

COMPONENT_REF_PREFIX: str = "#/components/schemas/"
"""Prefix of a ``$ref`` that points at a schema component."""


def _referenced_components(document: dict[str, Any]) -> set[str]:
    """Collect the schema components some ``$ref`` in ``document`` names.

    Args:
        document (dict[str, Any]): The OpenAPI document.

    Returns:
        set[str]: The component names referenced anywhere, the components'
        own bodies included.
    """
    names: set[str] = set()
    pending: list[Any] = [document]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith(COMPONENT_REF_PREFIX):
                names.add(ref.removeprefix(COMPONENT_REF_PREFIX))
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return names


def _is_enum_schema(schema: Any) -> bool:
    """Tell whether a component describes an enum and nothing else.

    Args:
        schema (Any): The component body.

    Returns:
        bool: ``True`` for a schema with ``enum`` and no ``properties``.
    """
    return isinstance(schema, dict) and "enum" in schema and "properties" not in schema


def drop_orphan_enum_schemas(document: dict[str, Any]) -> list[str]:
    """Remove, in place, every enum component no ``$ref`` points at.

    Args:
        document (dict[str, Any]): The OpenAPI document.

    Returns:
        list[str]: The names removed, sorted.
    """
    schemas: dict[str, Any] = document.get("components", {}).get("schemas", {})
    referenced = _referenced_components(document)
    orphans = sorted(
        name
        for name, schema in schemas.items()
        if name not in referenced and _is_enum_schema(schema)
    )
    for name in orphans:
        del schemas[name]
    return orphans


def prune_orphan_enum_schemas(app: FastAPI) -> None:
    """Make ``app.openapi()`` leave out the enum components nothing uses.

    Wraps ``app.openapi`` the way
    :func:`~tempest_fastapi_sdk.api.error_docs.describe_validation_envelope`
    does, so the pruning survives the schema being regenerated. Calling it
    twice is harmless: the second pass finds nothing to remove.

    Args:
        app (FastAPI): The application whose schema to prune.
    """
    original = app.openapi

    def pruned() -> dict[str, Any]:
        """Return the app's schema without orphan enum components.

        Returns:
            dict[str, Any]: The OpenAPI schema.
        """
        document: dict[str, Any] = original()
        drop_orphan_enum_schemas(document)
        return document

    app.openapi = pruned  # type: ignore[method-assign]


__all__: list[str] = [
    "COMPONENT_REF_PREFIX",
    "drop_orphan_enum_schemas",
    "prune_orphan_enum_schemas",
]
