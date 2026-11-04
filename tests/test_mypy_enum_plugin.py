"""The ``tempest_fastapi_sdk.mypy`` plugin types enum fields as their value.

``BaseSchema`` sets ``use_enum_values=True``, so a field annotated with an
enum holds the value at runtime. Without the plugin, mypy ``--strict``
accepted ``order.status.value`` and passing ``order.status`` to a function
that expects the enum; both raise ``AttributeError``. These tests run mypy
for real over a probe module, under both plugin orders, and against the
baseline without the plugin.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mypy import api as mypy_api

PROBE: str = """
from enum import Enum

from pydantic import BaseModel, ConfigDict

from tempest_fastapi_sdk import BaseIntEnum, BaseSchema, BaseStrEnum


class StatusEnum(BaseStrEnum):
    PAID = "paid"
    OPEN = "open"


class PriorityEnum(BaseIntEnum):
    LOW = 1


class PlainEnum(Enum):
    A = "a"


class OrderSchema(BaseSchema):
    status: StatusEnum
    maybe: StatusEnum | None = None
    many: list[StatusEnum] = []
    priority: PriorityEnum = PriorityEnum.LOW
    plain: PlainEnum = PlainEnum.A
    title: str = ""


class ChildSchema(OrderSchema):
    extra: StatusEnum = StatusEnum.OPEN


class CopySchema(BaseSchema):
    status: StatusEnum


class MemberModel(BaseModel):
    status: StatusEnum


class OptOutSchema(BaseSchema):
    model_config = ConfigDict(use_enum_values=False)
    status: StatusEnum


def describe(status: StatusEnum) -> str:
    return status.name


order = OrderSchema(status=StatusEnum.PAID)
child = ChildSchema(status=StatusEnum.PAID)
reveal_type(order.status)  # R:status
reveal_type(order.maybe)  # R:maybe
reveal_type(order.many)  # R:many
reveal_type(order.priority)  # R:priority
reveal_type(order.plain)  # R:plain
reveal_type(order.title)  # R:title
reveal_type(child.extra)  # R:child_extra
reveal_type(child.status)  # R:child_status
reveal_type(MemberModel(status=StatusEnum.PAID).status)  # R:plain_model
reveal_type(OptOutSchema(status=StatusEnum.PAID).status)  # R:opt_out
reveal_type(StatusEnum.PAID.value)  # R:member_value
equal: bool = order.status == StatusEnum.PAID
copied = CopySchema(status=order.status)  # L:copy
bad_value = order.status.value  # L:value
bad_call = describe(order.status)  # L:call
missing = OrderSchema()  # L:missing
"""
"""A module exercising every rule; ``# R:`` and ``# L:`` tag the lines read."""


def _line_of(tag: str) -> int:
    """Return the 1-based line of :data:`PROBE` carrying ``tag``.

    Args:
        tag (str): The marker, such as ``"R:status"``.

    Returns:
        int: The line number.
    """
    for number, line in enumerate(PROBE.splitlines(), start=1):
        if line.endswith(f"# {tag}"):
            return number
    raise AssertionError(f"tag {tag} not in the probe")


def _run(tmp_path: Path, plugins: list[str]) -> dict[int, list[str]]:
    """Type-check :data:`PROBE` and group mypy's findings by line.

    Args:
        tmp_path (Path): Scratch directory for the module, config and cache.
        plugins (list[str]): The ``plugins`` list of the config.

    Returns:
        dict[int, list[str]]: Each line's findings, without the path prefix.
    """
    (tmp_path / "probe.py").write_text(PROBE)
    listed = ", ".join(f'"{name}"' for name in plugins)
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.mypy]\nstrict = true\nplugins = [{listed}]\n\n"
        "[tool.pydantic-mypy]\ninit_typed = true\n"
    )
    stdout, _stderr, _status = mypy_api.run(
        [
            "--config-file",
            str(tmp_path / "pyproject.toml"),
            "--no-error-summary",
            "--no-color-output",
            "--cache-dir",
            str(tmp_path / "cache"),
            str(tmp_path / "probe.py"),
        ]
    )
    findings: dict[int, list[str]] = {}
    for line in stdout.splitlines():
        location, _, message = line.partition(": ")
        number = int(location.rsplit(":", 1)[1])
        findings.setdefault(number, []).append(message)
    return findings


def _revealed(findings: dict[int, list[str]], tag: str) -> str:
    """Return the type mypy revealed on the line tagged ``tag``.

    Args:
        findings (dict[int, list[str]]): Output of :func:`_run`.
        tag (str): The ``R:`` marker.

    Returns:
        str: The revealed type, without the quotes.
    """
    for message in findings.get(_line_of(tag), []):
        if message.startswith("note: Revealed type is "):
            return message.removeprefix("note: Revealed type is ").strip('"')
    raise AssertionError(f"nothing revealed on {tag}: {findings}")


ORDERS: dict[str, list[str]] = {
    "pydantic-first": ["pydantic.mypy", "tempest_fastapi_sdk.mypy"],
    "sdk-first": ["tempest_fastapi_sdk.mypy", "pydantic.mypy"],
}
"""Both orders of the ``plugins`` list: mypy keeps only the first hook."""


@pytest.fixture(scope="module", params=sorted(ORDERS))
def with_plugin(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> dict[int, list[str]]:
    """Run mypy once per plugin order and share the findings.

    Args:
        request (pytest.FixtureRequest): Carries the order's name.
        tmp_path_factory (pytest.TempPathFactory): Scratch directories.

    Returns:
        dict[int, list[str]]: The findings, grouped by line.
    """
    name: str = request.param
    return _run(tmp_path_factory.mktemp(name), ORDERS[name])


def test_enum_fields_read_as_their_value(with_plugin: dict[int, list[str]]) -> None:
    findings = with_plugin

    assert _revealed(findings, "R:status") == "str"
    assert _revealed(findings, "R:maybe") == "str | None"
    assert _revealed(findings, "R:many") == "list[str]"
    assert _revealed(findings, "R:priority") == "int"
    assert _revealed(findings, "R:plain") == "object"
    assert _revealed(findings, "R:title") == "str"
    assert _revealed(findings, "R:child_extra") == "str"
    assert _revealed(findings, "R:child_status") == "str"


def test_runtime_failures_are_reported(with_plugin: dict[int, list[str]]) -> None:
    findings = with_plugin

    assert any('has no attribute "value"' in m for m in findings[_line_of("L:value")])
    assert any("[arg-type]" in m for m in findings[_line_of("L:call")])


def test_other_plugins_and_models_are_untouched(
    with_plugin: dict[int, list[str]],
) -> None:
    """Neither plugin shadows the other, and only qualifying models change."""
    findings = with_plugin

    assert _revealed(findings, "R:plain_model") == "probe.StatusEnum"
    assert _revealed(findings, "R:opt_out") == "probe.StatusEnum"
    assert _revealed(findings, "R:member_value") == "Literal['paid']?"
    assert any("[call-arg]" in m for m in findings[_line_of("L:missing")])
    assert _line_of("L:copy") not in findings


def test_without_the_plugin_mypy_accepts_the_crash(tmp_path: Path) -> None:
    """The baseline the plugin exists for: both runtime failures pass."""
    findings = _run(tmp_path, ["pydantic.mypy"])

    assert _revealed(findings, "R:status") == "probe.StatusEnum"
    assert _line_of("L:value") not in findings
    assert _line_of("L:call") not in findings
