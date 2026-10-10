"""Overlay steps shared by more than one vendored specification.

A step lives here once a second provider needs it, so a fix to it reaches
every overlay instead of drifting between copies.
"""

from __future__ import annotations

import copy
from typing import Any


def lift_enum(
    document: dict[str, Any],
    schema_name: str,
    properties: dict[str, str],
) -> tuple[str, ...]:
    """Move a property's inline ``enum`` into a component of its own.

    Args:
        document (dict[str, Any]): The document being patched.
        schema_name (str): The ``components.schemas`` key to correct.
        properties (dict[str, str]): Component name to lift into, by
            property name.

    Returns:
        tuple[str, ...]: ``Schema.property`` for each enum actually lifted.
        A property that no longer declares one is skipped, so the override
        retires by itself the day the provider unconstrains the field.

    The values are not discarded. They are declared as a standalone
    component and the property is rewritten as an ``anyOf`` of that
    component and the bare type, so the generator still emits the enum
    class of the same name — a component nothing references is pruned —
    and the field accepts a state the list does not name.
    """
    schemas = document.get("components", {}).get("schemas", {})
    target = schemas.get(schema_name)
    if not isinstance(target, dict):
        return ()
    declared = target.get("properties")
    if not isinstance(declared, dict):
        return ()
    lifted: list[str] = []
    for name, component in properties.items():
        current = declared.get(name)
        if not isinstance(current, dict) or "enum" not in current:
            continue
        schemas.setdefault(
            component,
            {
                "type": current.get("type", "string"),
                "enum": copy.deepcopy(current["enum"]),
                "description": (
                    f"The values `{schema_name}.{name}` is documented with. "
                    f"Declared as a component so the generated class survives "
                    f"the property being unconstrained: the API reports states "
                    f"outside this list, and a closed enum on a response makes "
                    f"an unrecognized state a refused read."
                ),
            },
        )
        declared[name] = {
            "anyOf": [
                {"$ref": f"#/components/schemas/{component}"},
                {"type": current.get("type", "string")},
            ],
            "description": current.get("description", ""),
        }
        lifted.append(f"{schema_name}.{name}")
    return tuple(lifted)


__all__: list[str] = ["lift_enum"]
