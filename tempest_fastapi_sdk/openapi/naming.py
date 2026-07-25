"""Identifier naming rules for generated OpenAPI code.

A third-party specification names things for the wire, not for Python:
``createdAt``, ``user-id``, ``HTTPStatusCode``, ``class``. This module
turns those into valid, idiomatic Python identifiers while keeping the
original around so the emitter can attach a Pydantic ``alias`` — which is
the manual chore the generator exists to remove.
"""

from __future__ import annotations

import keyword
import re

_ACRONYM_BOUNDARY = re.compile(r"([A-Z]+)([A-Z][a-z])")
_WORD_BOUNDARY = re.compile(r"([a-z\d])([A-Z])")
_NON_ALNUM = re.compile(r"[^0-9a-zA-Z]+")
_LEADING_DIGITS = re.compile(r"^\d")

_SHADOWED_BUILTINS: frozenset[str] = frozenset(
    {
        "all",
        "any",
        "bytes",
        "dict",
        "filter",
        "format",
        "hash",
        "id",
        "input",
        "list",
        "map",
        "max",
        "min",
        "next",
        "object",
        "property",
        "range",
        "set",
        "slice",
        "sum",
        "type",
        "vars",
        "zip",
    }
)
"""Builtins worth renaming when they appear as a field name.

Deliberately **not** every builtin. ``id`` and ``type`` are extremely
common wire field names; shadowing them inside a Pydantic model body is
harmless (the class namespace is not the module namespace) and renaming
them would make every generated schema read worse. They are listed here
only so :func:`is_shadowing_builtin` can report them — the field-name
policy in :func:`field_name` keeps them as-is.
"""


def _normalize(name: str) -> str:
    """Split an arbitrary name into lower-case underscore-joined words.

    Kept separate from :func:`to_snake` so :func:`to_pascal` does not
    inherit that function's ``"field"`` fallback — a component named
    ``"***"`` must become ``Model``, not ``Field``.

    Args:
        name (str): The name as written in the specification.

    Returns:
        str: The normalized form, **possibly empty** when the input holds
        no alphanumeric character.
    """
    spaced = _NON_ALNUM.sub("_", name)
    spaced = _ACRONYM_BOUNDARY.sub(r"\1_\2", spaced)
    spaced = _WORD_BOUNDARY.sub(r"\1_\2", spaced)
    return re.sub(r"_+", "_", spaced).strip("_").lower()


def to_snake(name: str) -> str:
    """Convert an arbitrary wire name to ``snake_case``.

    Handles the four shapes a specification actually uses: camelCase,
    PascalCase, kebab-case / dotted / spaced, and runs of acronyms.

    Args:
        name (str): The name as written in the specification.

    Returns:
        str: A ``snake_case`` rendering. ``"createdAt"`` -> ``"created_at"``,
        ``"HTTPStatusCode"`` -> ``"http_status_code"``, ``"user-id"`` ->
        ``"user_id"``, ``"already_snake"`` -> ``"already_snake"``.
        Never empty: an unusable name yields ``"field"``.
    """
    return _normalize(name) or "field"


def to_pascal(name: str) -> str:
    """Convert an arbitrary name to ``PascalCase`` for a class name.

    Args:
        name (str): The name as written in the specification (a
            ``components.schemas`` key, an ``info.title``, …).

    Returns:
        str: A ``PascalCase`` rendering, prefixed with ``Model`` when the
        result would start with a digit. Never empty: an unusable name
        yields ``"Model"``.
    """
    parts = [part for part in _normalize(name).split("_") if part]
    pascal = "".join(part[:1].upper() + part[1:] for part in parts)
    if not pascal:
        return "Model"
    if _LEADING_DIGITS.match(pascal):
        return f"Model{pascal}"
    return pascal


def is_shadowing_builtin(name: str) -> bool:
    """Report whether ``name`` shadows one of the builtins we track.

    Args:
        name (str): A candidate identifier.

    Returns:
        bool: ``True`` when the name is in :data:`_SHADOWED_BUILTINS`.
    """
    return name in _SHADOWED_BUILTINS


def field_name(wire_name: str) -> str:
    """Return the Python field name for a wire property name.

    Only **hard** keywords are suffixed. Soft keywords (``match``,
    ``case``, ``type``, ``_``) are deliberately left alone for two
    reasons. They are contextual — legal as an attribute name in every
    Python version — so suffixing buys nothing. And the soft-keyword list
    *grows*: ``type`` joined it in 3.12, so consulting
    :func:`keyword.issoftkeyword` would make the generated field name
    depend on the interpreter that ran the generator, and the same
    specification would produce different code on 3.11 and 3.13.

    Args:
        wire_name (str): The property name from the specification.

    Returns:
        str: The ``snake_case`` name, suffixed with ``_`` when it collides
        with a Python **keyword** (``class`` -> ``class_``, ``from`` ->
        ``from_``). Builtins like ``id`` are kept verbatim — a model
        attribute does not shadow the module namespace, and renaming them
        would make every generated schema read worse.
    """
    snake = to_snake(wire_name)
    if keyword.iskeyword(snake):
        return f"{snake}_"
    return snake


def method_name(operation_id: str | None, http_method: str, path: str) -> str:
    """Return the Python method name for an operation.

    Args:
        operation_id (str | None): The specification's ``operationId``.
            Preferred when present — it is the author's own name for the
            operation.
        http_method (str): The HTTP method, used in the fallback.
        path (str): The path template, used in the fallback.

    Returns:
        str: ``snake_case`` method name. From ``operationId`` when given,
        otherwise built from method + path with the ``{param}`` braces
        turned into words (``GET /users/{userId}/posts`` ->
        ``get_users_by_user_id_posts``).
    """
    if operation_id:
        return field_name(operation_id)
    segments: list[str] = [http_method.lower()]
    for raw in path.strip("/").split("/"):
        if not raw:
            continue
        if raw.startswith("{") and raw.endswith("}"):
            segments.append("by")
            segments.append(to_snake(raw[1:-1]))
        else:
            segments.append(to_snake(raw))
    return "_".join(part for part in segments if part) or "call"


def enum_member_name(value: object) -> str:
    """Return the member name for an enum value.

    Args:
        value (object): The raw enum value from the specification.

    Returns:
        str: An ``UPPER_SNAKE_CASE`` member name. Values that cannot form
        an identifier (``""``, ``"*"``) fall back to a readable stand-in,
        and a leading digit is prefixed with ``VALUE_`` so ``"2xx"``
        becomes ``VALUE_2XX``.
    """
    text = str(value)
    upper = to_snake(text).upper()
    if not upper or upper == "FIELD":
        return "EMPTY" if text == "" else "VALUE"
    if _LEADING_DIGITS.match(upper):
        return f"VALUE_{upper}"
    if keyword.iskeyword(upper.lower()):
        return f"{upper}_"
    return upper


def unique(name: str, taken: set[str]) -> str:
    """Return ``name`` made unique against ``taken``, and reserve it.

    Two different specification names can collapse onto one Python
    identifier (``user-id`` and ``userId`` both yield ``user_id``).
    Silently letting the second overwrite the first would drop a field,
    so the duplicate is suffixed instead.

    Args:
        name (str): The candidate identifier.
        taken (set[str]): Names already used in this scope. **Mutated** —
            the returned name is added.

    Returns:
        str: ``name`` when free, otherwise ``name_2``, ``name_3``, …
    """
    candidate = name
    counter = 2
    while candidate in taken:
        candidate = f"{name}_{counter}"
        counter += 1
    taken.add(candidate)
    return candidate


__all__: list[str] = [
    "enum_member_name",
    "field_name",
    "is_shadowing_builtin",
    "method_name",
    "to_pascal",
    "to_snake",
    "unique",
]
