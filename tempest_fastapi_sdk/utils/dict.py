"""Dictionary helpers used by the SDK base schemas and models."""

from typing import Any


def modify_dict(
    data: dict[str, Any],
    exclude: list[str] | None = None,
    include: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Filter and extend a dictionary in a single pass.

    Removes any keys listed in ``exclude`` and merges the
    ``include`` mapping on top of the remaining items.

    Args:
        data (dict[str, Any]): The source dictionary.
        exclude (list[str] | None): Keys to drop from the output.
        include (dict[str, Any] | None): Extra entries to merge into
            the output (these win over keys already in ``data``).

    Returns:
        dict[str, Any]: A new dictionary with the requested mutations.
    """
    excluded: set[str] = set(exclude or [])
    result: dict[str, Any] = {
        key: value for key, value in data.items() if key not in excluded
    }
    if include:
        result.update(include)
    return result


__all__: list[str] = [
    "modify_dict",
]
