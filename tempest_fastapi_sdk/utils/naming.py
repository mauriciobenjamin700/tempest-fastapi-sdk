"""Name conversions shared by the ORM and the migration tooling."""

import re

_CAMEL_TO_SNAKE_RE = re.compile(r"(?<!^)(?=[A-Z])")


def to_snake_case(name: str) -> str:
    """Convert ``CamelCase`` to ``snake_case``.

    Used wherever a Python class name has to become a stable database
    identifier — a table name, a PostgreSQL ``ENUM`` type name — so the
    same class always yields the same identifier across machines and
    across a model/migration pair that never see each other.

    Args:
        name (str): The class name to convert.

    Returns:
        str: The snake_case version.
    """
    return _CAMEL_TO_SNAKE_RE.sub("_", name).lower()


__all__: list[str] = ["to_snake_case"]
