"""Two ways a ``Protocol`` member fails the caller, neither one mypy's.

``LESSONS.md`` recorded this in v0.257.0 for one Redis store protocol and
the fix was applied there — while seven members of three other protocols
kept the same shape. Nothing in the gate noticed, because ``Any`` is not a
mistake a type-checker can report: it is the annotation that tells the
checker to stop asking.

What it costs is measurable. With ``def eval(...) -> Awaitable[Any]`` the
call site may declare whatever it likes and mypy agrees::

    raw: list[int] = await redis.eval("...")            # accepted
    raw: dict[str, complex] = await redis.eval("...")   # also accepted

With the concrete ``Awaitable[list[int]]`` the second line is an error.
The assertion the call site already makes becomes an assertion something
verifies.

This guard covers the origin — the protocol member — not the call site.
The call site would need ``disallow_any_expr``, which counts 674 bare
``Any`` across hand-written code here, most of them legitimate (JSON
payloads, decorator plumbing, third-party objects without stubs). So the
guard is deliberately narrow: it flags a return annotation that is exactly
``Any``, or an awaitable/iterator wrapper whose value slot is exactly
``Any``. ``dict[str, Any]`` — a decoded JWT claim set, a tool-calling
response — is a real JSON payload and is left alone.

The second rule is about parameter *names*. A protocol member written
``def expire(self, name: str, seconds: int)`` demands that the implementer
call the second parameter ``seconds``. ``redis.asyncio.Redis.expire`` calls
it ``time``, and ``delete`` takes ``*names``, which no keyword call reaches.
Measured against redis-py 8.1.0 and fakeredis 2.37.0: basedpyright rejected
**both** clients as ``ThrottleBackend`` — while the recipe told the reader
that ``redis.asyncio.Redis`` "works out of the box". Isolating the member
prints the reason the whole-protocol diagnostic truncates away: *Type
"(name: KeyT, time: ExpiryT, ...) -> Awaitable[bool]" is not assignable to
type "(name: str, seconds: int) -> Awaitable[Any]"*. mypy accepts either
spelling, which is how it shipped.
The fix is ``/``: a positional-only protocol member never asks the implementer
what it named anything.

That rule only applies where we do not own the implementer, so the second
guard walks an explicit list rather than every protocol in the package —
a protocol our own classes implement is free to name its parameters, because
the names are ours to keep in sync.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT: Path = Path(__file__).resolve().parent.parent / "tempest_fastapi_sdk"
"""The package this guard walks."""

SKIP_MARKER: str = "protocol-shape-guard: skip"
"""Comment opting one member out, for a case that is genuinely not this."""

THIRD_PARTY_CLIENT_PROTOCOLS: dict[str, str] = {
    "ThrottleBackend": "tempest_fastapi_sdk/utils/throttle.py",
    "_RedisHashClient": "tempest_fastapi_sdk/flags/backends.py",
    "RedisLike": "tempest_fastapi_sdk/api/middlewares/rate_limit.py",
}
"""Protocols describing a client we do not own, by defining module.

``RedisLike`` is declared three times — in ``rate_limit.py``, ``quota.py``
and ``auth/webauthn.py`` — so the guard matches on the class name and this
map records one representative path per entry, which
``test_every_listed_protocol_still_exists`` keeps honest.
"""

VALUE_LAST: frozenset[str] = frozenset({"Awaitable", "Coroutine"})
"""Wrappers whose resolved value is the *last* type argument."""

VALUE_FIRST: frozenset[str] = frozenset(
    {
        "AsyncGenerator",
        "AsyncIterable",
        "AsyncIterator",
        "Generator",
        "Iterable",
        "Iterator",
    }
)
"""Wrappers whose yielded value is the *first* type argument."""


def _base_names(node: ast.ClassDef) -> list[str]:
    """Return the bare names of a class's bases.

    Args:
        node (ast.ClassDef): The class to inspect.

    Returns:
        list[str]: One entry per base, unwrapping ``typing.Protocol`` to
        ``Protocol`` and ``Generic[T]`` to ``Generic``, so the check does
        not depend on how the module spells its imports.
    """
    names: list[str] = []
    for base in node.bases:
        target: ast.expr = base.value if isinstance(base, ast.Subscript) else base
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, ast.Attribute):
            names.append(target.attr)
    return names


def _erases_the_type(annotation: ast.expr) -> bool:
    """Report whether a return annotation resolves to ``Any``.

    Args:
        annotation (ast.expr): The return annotation node.

    Returns:
        bool: ``True`` for a bare ``Any``, and for a wrapper from
        :data:`VALUE_LAST` or :data:`VALUE_FIRST` whose value slot is
        exactly ``Any``. A payload type such as ``dict[str, Any]`` is not
        erasure — the container is still checked — so it returns ``False``.
    """
    if isinstance(annotation, ast.Name) and annotation.id == "Any":
        return True
    if not isinstance(annotation, ast.Subscript):
        return False
    origin = annotation.value
    name = (
        origin.id
        if isinstance(origin, ast.Name)
        else origin.attr
        if isinstance(origin, ast.Attribute)
        else ""
    )
    args = (
        list(annotation.slice.elts)
        if isinstance(annotation.slice, ast.Tuple)
        else [annotation.slice]
    )
    if name in VALUE_LAST:
        value = args[-1]
    elif name in VALUE_FIRST:
        value = args[0]
    else:
        return False
    return isinstance(value, ast.Name) and value.id == "Any"


def _erased_protocol_members(path: Path) -> list[str]:
    """Find protocol members whose return annotation resolves to ``Any``.

    Args:
        path (Path): The module to inspect.

    Returns:
        list[str]: One ``file:line ClassName.member -> annotation`` entry
        per violation.
    """
    source = path.read_text(encoding="utf-8")
    lines = source.split("\n")
    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef):
            continue
        if "Protocol" not in _base_names(node):
            continue
        for member in node.body:
            if not isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if member.returns is None or not _erases_the_type(member.returns):
                continue
            if SKIP_MARKER in lines[member.returns.lineno - 1]:
                continue
            rendered = ast.unparse(member.returns)
            problems.append(
                f"{_label(path)}:{member.returns.lineno} "
                f"{node.name}.{member.name} -> {rendered}"
            )
    return problems


def _named_parameters(path: Path) -> list[str]:
    """Find members of a third-party client protocol that are not positional.

    Only *required* parameters are in scope. An optional one can only be
    passed by keyword, so its name is genuinely part of the contract — the
    WebAuthn store calls ``set(key, payload, ex=ttl)`` and ``redis-py``
    spells that parameter ``ex`` too. A required parameter is passed
    positionally, and then what the client calls it is its own business.

    Args:
        path (Path): The module to inspect.

    Returns:
        list[str]: One ``file:line ClassName.member(param)`` entry per
        required parameter the implementer would be forced to name our way.
        ``self`` is excluded — it is bound before the signature is compared.
    """
    source = path.read_text(encoding="utf-8")
    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef):
            continue
        if "Protocol" not in _base_names(node):
            continue
        if node.name not in THIRD_PARTY_CLIENT_PROTOCOLS:
            continue
        for member in node.body:
            if not isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            named = member.args.args
            required = named[: len(named) - len(member.args.defaults)]
            for arg in required:
                if arg.arg == "self":
                    continue
                problems.append(
                    f"{_label(path)}:{arg.lineno} {node.name}.{member.name}({arg.arg})"
                )
    return problems


def _label(path: Path) -> str:
    """Render a path for the failure message.

    Args:
        path (Path): The inspected file.

    Returns:
        str: A repo-relative path when possible, the absolute one otherwise
        — this guard's own tests point it at a temporary directory.
    """
    try:
        return str(path.relative_to(PACKAGE_ROOT.parent))
    except ValueError:
        return str(path)


def test_no_protocol_member_returns_an_erased_type() -> None:
    """Every protocol member states the contract its callers rely on."""
    problems: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        problems.extend(_erased_protocol_members(path))
    assert not problems, (
        "a `Protocol` member returning `Any` lets every call site declare "
        "whatever it likes; state the concrete type, or `Awaitable[object]` "
        "when the result is discarded:\n  " + "\n  ".join(problems)
    )


def test_third_party_client_protocols_are_positional_only() -> None:
    """We never dictate what ``redis-py`` calls its parameters."""
    problems: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        problems.extend(_named_parameters(path))
    assert not problems, (
        "a protocol member that names its parameters forces the implementer "
        "to use those names; add `/` so the client we do not own is accepted "
        "whatever it calls them:\n  " + "\n  ".join(problems)
    )


def test_every_listed_protocol_still_exists() -> None:
    """The list is only worth something while it points at real classes."""
    missing: list[str] = []
    for name, path in THIRD_PARTY_CLIENT_PROTOCOLS.items():
        module = PACKAGE_ROOT.parent / path
        found = any(
            isinstance(node, ast.ClassDef)
            and node.name == name
            and "Protocol" in _base_names(node)
            for node in ast.walk(ast.parse(module.read_text(encoding="utf-8")))
        )
        if not found:
            missing.append(f"{path}:{name}")
    assert not missing, (
        "THIRD_PARTY_CLIENT_PROTOCOLS names a protocol that moved or was "
        "renamed:\n  " + "\n  ".join(missing)
    )


def test_the_positional_guard_fires_on_the_shape_that_shipped(
    tmp_path: Path,
) -> None:
    """basedpyright rejected both Redis clients on exactly this signature.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
    """
    module = tmp_path / "shipped_names.py"
    module.write_text(
        "from collections.abc import Awaitable\n"
        "from typing import Protocol\n\n\n"
        "class ThrottleBackend(Protocol):\n"
        "    def expire(self, name: str, seconds: int) -> Awaitable[object]:\n"
        '        """Set a TTL on ``name``."""\n'
        "        ...\n",
        encoding="utf-8",
    )

    assert _named_parameters(module) == [
        f"{module}:6 ThrottleBackend.expire(name)",
        f"{module}:6 ThrottleBackend.expire(seconds)",
    ]


def test_the_positional_form_passes(tmp_path: Path) -> None:
    """``/`` is the whole fix.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
    """
    module = tmp_path / "fixed_names.py"
    module.write_text(
        "from collections.abc import Awaitable\n"
        "from typing import Protocol\n\n\n"
        "class ThrottleBackend(Protocol):\n"
        "    def expire(self, name: str, seconds: int, /) -> Awaitable[object]:\n"
        '        """Set a TTL on ``name``."""\n'
        "        ...\n",
        encoding="utf-8",
    )

    assert not _named_parameters(module)


def test_an_optional_keyword_is_a_real_contract(tmp_path: Path) -> None:
    """``ex=`` is passed by keyword, so its name is ours to match.

    This is the shape ``auth/webauthn.py`` already ships, and it is correct:
    ``redis.asyncio.Redis.set`` calls that parameter ``ex`` as well.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
    """
    module = tmp_path / "optional_keyword.py"
    module.write_text(
        "from collections.abc import Awaitable\n"
        "from typing import Protocol\n\n\n"
        "class RedisLike(Protocol):\n"
        "    def set(\n"
        "        self, name: str, value: str, /, ex: int | None = None\n"
        "    ) -> Awaitable[object]:\n"
        '        """Store ``value`` under ``name``."""\n'
        "        ...\n",
        encoding="utf-8",
    )

    assert not _named_parameters(module)


def test_a_protocol_we_implement_is_out_of_scope(tmp_path: Path) -> None:
    """Only the listed third-party client protocols are checked.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
    """
    module = tmp_path / "ours.py"
    module.write_text(
        "from typing import Protocol\n\n\n"
        "class FeatureFlagBackend(Protocol):\n"
        "    def get(self, name: str) -> bool:\n"
        '        """Return the flag value."""\n'
        "        ...\n",
        encoding="utf-8",
    )

    assert not _named_parameters(module)


@pytest.mark.parametrize(
    "returns",
    [
        "Awaitable[Any]",
        "Coroutine[Any, Any, Any]",
        "AsyncIterator[Any]",
        "Any",
    ],
)
def test_the_guard_fires_on_the_shapes_that_shipped(
    tmp_path: Path, returns: str
) -> None:
    """A guard that cannot fail is one nobody should trust.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
        returns (str): The erased return annotation under test.
    """
    module = tmp_path / "shipped.py"
    module.write_text(
        "from collections.abc import AsyncIterator, Awaitable, Coroutine\n"
        "from typing import Any, Protocol\n\n\n"
        "class ThrottleBackend(Protocol):\n"
        f"    def get(self, name: str) -> {returns}:\n"
        '        """Return the value at ``name``."""\n'
        "        ...\n",
        encoding="utf-8",
    )

    assert _erased_protocol_members(module)


@pytest.mark.parametrize(
    "returns",
    [
        "Awaitable[str | bytes | None]",
        "Awaitable[object]",
        "Awaitable[list[int]]",
        "dict[str, Any]",
        "dict[str, Any] | None",
        "Awaitable[Mapping[str | bytes, str | bytes]]",
    ],
)
def test_a_stated_contract_passes(tmp_path: Path, returns: str) -> None:
    """The replacement forms, and a genuine JSON payload, are left alone.

    ``Awaitable[object]`` is the form for a discarded result: it accepts
    any implementer — ``redis-py`` resolves ``int`` for ``delete`` — while
    ``Awaitable[None]`` would be the too-narrow annotation the same lesson
    describes as the other failure mode.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
        returns (str): The stated return annotation under test.
    """
    module = tmp_path / "fixed.py"
    module.write_text(
        "from collections.abc import Awaitable, Mapping\n"
        "from typing import Any, Protocol\n\n\n"
        "class ThrottleBackend(Protocol):\n"
        f"    def get(self, name: str, /) -> {returns}:\n"
        '        """Return the value at ``name``."""\n'
        "        ...\n",
        encoding="utf-8",
    )

    assert not _erased_protocol_members(module)


def test_a_plain_class_is_not_a_protocol(tmp_path: Path) -> None:
    """Only ``Protocol`` members are in scope.

    A concrete class returning ``Any`` is a different question — often a
    real payload — and this guard does not answer it.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.
    """
    module = tmp_path / "concrete.py"
    module.write_text(
        "from typing import Any\n\n\n"
        "class Decoder:\n"
        "    def decode(self, raw: str) -> Any:\n"
        '        """Return the decoded payload."""\n'
        "        return raw\n",
        encoding="utf-8",
    )

    assert not _erased_protocol_members(module)
