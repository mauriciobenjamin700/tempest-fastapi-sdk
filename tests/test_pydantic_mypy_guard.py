"""``plugins = ["pydantic.mypy"]`` alone type-checks no constructor at all.

The plugin synthesizes ``__init__`` with one named keyword-only parameter
per field, which looks like coverage. It is not: ``init_typed`` defaults to
``False``, so every one of those parameters is annotated ``Any``. Measured
here with mypy 2.3.0 and pydantic 2.13.4, before the setting existed in this
repo::

    reveal_type(Probe.__init__)
    # def (__pydantic_self__: Probe, *, name: Any, age: Any, **kwargs: Any)
    Probe(name="x", age="doze")
    # Success: no issues found in 1 source file

With ``init_typed = true`` the same file reports *Argument "age" to "Probe"
has incompatible type "str"; expected "int"*, and ``mypy
tempest_fastapi_sdk`` stayed green across 409 source files — the setting
reported a class of error, not a backlog.

Pyright and Pylance never load the plugin, so they read the annotations and
flag these call sites regardless. Without the setting the editor and the
gate disagree, and the gate is the one that is wrong. This guard keeps the
plugin and its config together, in this repo's ``pyproject.toml`` and in
every project ``tempest new`` scaffolds.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
"""The repository root."""

CONFIGS: tuple[Path, ...] = (
    REPO_ROOT / "pyproject.toml",
    REPO_ROOT / "tempest_fastapi_sdk/cli/_templates/pyproject.toml.tmpl",
)
"""Every TOML in the repo that configures mypy: this package, and the one
``tempest new`` writes into a scaffolded service."""


def _violations(text: str) -> list[str]:
    """Check one TOML document for the plugin without its config.

    Args:
        text (str): The document contents. The scaffold template parses as
            TOML as-is — its placeholders live inside string values.

    Returns:
        list[str]: One message per problem, empty when the document either
        does not load the plugin or loads it with ``init_typed`` on.
    """
    tool = tomllib.loads(text).get("tool", {})
    if "pydantic.mypy" not in tool.get("mypy", {}).get("plugins", []):
        return []
    settings = tool.get("pydantic-mypy")
    if settings is None:
        return ["loads `pydantic.mypy` with no `[tool.pydantic-mypy]` section"]
    if settings.get("init_typed") is not True:
        return ["`[tool.pydantic-mypy]` is present but `init_typed` is not true"]
    return []


def test_every_mypy_config_types_constructor_arguments() -> None:
    """The plugin never ships without ``init_typed``."""
    problems: list[str] = []
    for path in CONFIGS:
        problems.extend(
            f"{path.relative_to(REPO_ROOT)}: {problem}"
            for problem in _violations(path.read_text(encoding="utf-8"))
        )
    assert not problems, (
        '`plugins = ["pydantic.mypy"]` without `init_typed = true` types '
        "every model constructor argument as `Any`:\n  " + "\n  ".join(problems)
    )


def test_the_guard_fires_on_the_shape_that_shipped() -> None:
    """A guard that cannot fail is one nobody should trust.

    The exact form this repo and the scaffold template carried until
    v0.241.0: the plugin declared, no plugin section anywhere.
    """
    shipped = (
        "[tool.mypy]\n"
        'python_version = "3.11"\n'
        "strict = true\n"
        'plugins = ["pydantic.mypy"]\n'
    )

    assert _violations(shipped)


def test_the_guard_fires_when_the_setting_is_turned_back_off() -> None:
    """A section that names the setting and disables it is the same hole."""
    disabled = (
        '[tool.mypy]\nplugins = ["pydantic.mypy"]\n\n'
        "[tool.pydantic-mypy]\ninit_typed = false\n"
    )

    assert _violations(disabled)


def test_the_fixed_form_passes() -> None:
    """The replacement is not flagged."""
    fixed = (
        '[tool.mypy]\nplugins = ["pydantic.mypy"]\n\n'
        "[tool.pydantic-mypy]\ninit_typed = true\n"
        "warn_required_dynamic_aliases = true\n"
    )

    assert not _violations(fixed)


def test_a_project_without_the_plugin_is_left_alone() -> None:
    """The guard asks for the setting only where the plugin runs."""
    plain = '[tool.mypy]\npython_version = "3.11"\nstrict = true\n'

    assert not _violations(plain)
