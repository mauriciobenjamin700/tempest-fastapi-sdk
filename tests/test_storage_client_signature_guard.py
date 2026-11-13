"""Guard: every SDK object store accepts every call its storage protocols make.

Three protocols describe what the SDK calls on an object store —
:class:`~tempest_fastapi_sdk.storage.DownloadableStore` (the download
response), :class:`~tempest_fastapi_sdk.privacy.SubjectStorageClient` (the
per-subject storage) and
:class:`~tempest_fastapi_sdk.admin.storage.AdminStorageClient` (the admin
browser) — and three clients stand behind them: MinIO, local disk and the
in-memory fake. Up to #757 the fake and ``SubjectStorageClient`` lacked the
``chunk_size`` that ``DownloadableStore`` declares and the download response
passes, so serving a file from the fake raised ``TypeError``.

The check is by ``inspect.signature``, not by mypy: a protocol method whose
parameter is missing from a client is invisible to the type checker until a
call site passes that client where the protocol is expected, and the fake was
never passed there inside the package.

For each protocol method, each client must:

* be async exactly where the protocol is;
* take the protocol's positional parameters first, in order, and every
  keyword-only one by name, each with the same kind and default;
* give any extra parameter a default, so a call written against the
  protocol still binds;
* return the same annotation.

The protocols must also agree with each other on the parameters they share
— one may omit an option the other declares — which is what lets one client
satisfy all three.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from tempest_fastapi_sdk.admin.storage import AdminStorageClient
from tempest_fastapi_sdk.privacy import SubjectStorageClient
from tempest_fastapi_sdk.storage import (
    AsyncMinIOClient,
    DownloadableStore,
    LocalObjectStorageClient,
)
from tempest_fastapi_sdk.testing.fakes import FakeObjectStorageClient

PROTOCOLS: list[type[Any]] = [
    DownloadableStore,
    SubjectStorageClient,
    AdminStorageClient,
]
"""The storage protocols the package calls through."""

CLIENTS: list[type[Any]] = [
    AsyncMinIOClient,
    LocalObjectStorageClient,
    FakeObjectStorageClient,
]
"""The object stores the package ships."""


def _methods(protocol: type[Any]) -> list[str]:
    """List the public callables a protocol declares.

    Args:
        protocol (type[Any]): The protocol.

    Returns:
        list[str]: Method names declared directly on it.
    """
    return [
        name
        for name, member in vars(protocol).items()
        if not name.startswith("_") and callable(member)
    ]


def _parameters(owner: type[Any], name: str) -> list[inspect.Parameter]:
    """Return a method's parameters without ``self``.

    Args:
        owner (type[Any]): The class.
        name (str): The method.

    Returns:
        list[inspect.Parameter]: The parameters after ``self``.
    """
    return list(inspect.signature(getattr(owner, name)).parameters.values())[1:]


def _shape(parameter: inspect.Parameter) -> tuple[str, Any, Any]:
    """Reduce a parameter to what a caller depends on.

    Args:
        parameter (inspect.Parameter): The parameter.

    Returns:
        tuple[str, Any, Any]: Name, kind and default.
    """
    return (parameter.name, parameter.kind, parameter.default)


def _positional(parameters: list[inspect.Parameter]) -> list[inspect.Parameter]:
    """Keep the parameters a caller may pass by position.

    Args:
        parameters (list[inspect.Parameter]): A method's parameters.

    Returns:
        list[inspect.Parameter]: The positional ones, in order.
    """
    return [p for p in parameters if p.kind is not inspect.Parameter.KEYWORD_ONLY]


def _drift(client: type[Any], protocol: type[Any], name: str) -> list[str]:
    """Describe how ``client.name`` fails to accept the protocol's call.

    Positional parameters are compared in order; keyword-only ones by name,
    because their order never reaches a caller.

    Args:
        client (type[Any]): The concrete store.
        protocol (type[Any]): The protocol.
        name (str): The method.

    Returns:
        list[str]: One message per mismatch; empty when compatible.
    """
    label = f"{client.__name__}.{name} vs {protocol.__name__}"
    if not hasattr(client, name):
        return [f"{label}: missing"]
    problems: list[str] = []
    if inspect.iscoroutinefunction(getattr(client, name)) != (
        inspect.iscoroutinefunction(getattr(protocol, name))
    ):
        problems.append(f"{label}: async mismatch")
    expected = _parameters(protocol, name)
    actual = _parameters(client, name)
    expected_positional = _positional(expected)
    actual_positional = _positional(actual)
    head = actual_positional[: len(expected_positional)]
    if [_shape(p) for p in head] != [_shape(p) for p in expected_positional]:
        problems.append(
            f"{label}: positional {[p.name for p in actual_positional]}, "
            f"protocol declares {[p.name for p in expected_positional]}"
        )
    by_name = {p.name: p for p in actual}
    for wanted in expected:
        if wanted.kind is not inspect.Parameter.KEYWORD_ONLY:
            continue
        found = by_name.get(wanted.name)
        if found is None:
            problems.append(f"{label}: does not take {wanted.name!r}")
        elif _shape(found) != _shape(wanted):
            problems.append(
                f"{label}: {wanted.name!r} is {found.kind.name} default "
                f"{found.default!r}, protocol declares {wanted.kind.name} "
                f"default {wanted.default!r}"
            )
    declared = {p.name for p in expected}
    for extra in actual:
        if extra.name not in declared and extra.default is inspect.Parameter.empty:
            if extra.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            problems.append(f"{label}: extra parameter {extra.name!r} is required")
    client_return = str(inspect.signature(getattr(client, name)).return_annotation)
    protocol_return = str(inspect.signature(getattr(protocol, name)).return_annotation)
    if client_return != protocol_return:
        problems.append(f"{label}: returns {client_return}, not {protocol_return}")
    return problems


@pytest.mark.parametrize("protocol", PROTOCOLS, ids=lambda p: p.__name__)
@pytest.mark.parametrize("client", CLIENTS, ids=lambda c: c.__name__)
def test_client_accepts_every_protocol_call(
    client: type[Any], protocol: type[Any]
) -> None:
    """Every method of every storage protocol binds on every SDK store."""
    problems = [
        problem
        for name in _methods(protocol)
        for problem in _drift(client, protocol, name)
    ]

    assert not problems, "\n".join(problems)


def test_protocols_agree_on_shared_methods() -> None:
    """A parameter two protocols declare has one kind and default in both.

    One protocol may omit an option the other declares —
    ``AdminStorageClient.remove_object`` never passes ``version_id`` — but a
    parameter both name cannot mean two things, or no client satisfies both.
    """
    problems: list[str] = []
    for index, first in enumerate(PROTOCOLS):
        for second in PROTOCOLS[index + 1 :]:
            for name in sorted(set(_methods(first)) & set(_methods(second))):
                left = {p.name: _shape(p) for p in _parameters(first, name)}
                right = {p.name: _shape(p) for p in _parameters(second, name)}
                for shared in sorted(left.keys() & right.keys()):
                    if left[shared] != right[shared]:
                        problems.append(
                            f"{first.__name__}.{name}({shared}) != "
                            f"{second.__name__}.{name}({shared})"
                        )

    assert not problems, "\n".join(problems)


class _ShippedFake:
    """``FakeObjectStorageClient.stream_object`` as it shipped before #757."""

    async def stat_object(self, key: str, *, bucket: str | None = None) -> Any:
        """Stand-in with the shipped signature.

        Args:
            key (str): Object key.
            bucket (str | None): Source bucket.
        """

    async def stream_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
        offset: int = 0,
        length: int | None = None,
    ) -> Any:
        """Stand-in with the shipped signature, no ``chunk_size``.

        Args:
            key (str): Object key.
            bucket (str | None): Source bucket.
            offset (int): First byte.
            length (int | None): Bytes to read.
        """


def test_guard_fires_on_the_shipped_fake() -> None:
    """The guard flags the signature that raised ``TypeError`` in #757."""
    problems = _drift(_ShippedFake, DownloadableStore, "stream_object")

    expected = (
        "_ShippedFake.stream_object vs DownloadableStore: does not take 'chunk_size'"
    )

    assert expected in problems, problems


class _RequiredExtra:
    """A store whose ``stat_object`` demands a parameter no caller passes."""

    async def stat_object(
        self, key: str, *, bucket: str | None = None, version: str
    ) -> Any:
        """Stand-in with a required extra keyword.

        Args:
            key (str): Object key.
            bucket (str | None): Source bucket.
            version (str): Required, so the protocol call cannot bind.
        """


def test_guard_fires_on_a_required_extra_parameter() -> None:
    """An extra parameter without a default breaks the protocol call."""
    problems = _drift(_RequiredExtra, DownloadableStore, "stat_object")

    assert any("'version' is required" in problem for problem in problems), problems
