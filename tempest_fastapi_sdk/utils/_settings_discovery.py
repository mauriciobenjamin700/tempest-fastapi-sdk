"""Find a project's settings however the service exposes them.

Four SDK surfaces resolve a project's settings module from the working
directory: ``run_alembic_env`` (the generated ``alembic/env.py``),
``tempest db``, ``tempest doctor`` and ``tempest user``. Each one used to
look only under ``src`` and only for a module-level ``BaseSettings``
instance named ``settings``, so a service written the way the SDK
documents — a cached ``get_settings()`` and nothing else on the module —
was invisible to all four. This module is the single discovery they share,
so a new shape (or a new code root) is taught once.

Nothing here imports the CLI extra: the generated ``env.py`` runs under
the bare ``alembic`` CLI, where ``typer`` is not installed, so the module
stays on the standard library plus a lazily imported ``pydantic_settings``.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CODE_ROOTS: tuple[str, ...] = ("src", "app")
"""The two directory names a service may use as its code root."""

_PREFERRED_INSTANCE_ATTRS: tuple[str, ...] = ("settings", "config")
"""Instance names tried before the module is scanned by type."""

_PREFERRED_FACTORY_ATTRS: tuple[str, ...] = ("get_settings", "get_config")
"""Zero-arg factory names tried before the module is scanned by annotation."""

_SETTINGS_MODULE_SUBPATH: tuple[str, ...] = ("core", "settings.py")
"""Path under a code root where the settings module is expected."""


@dataclass
class SettingsDiscovery:
    """What the working directory exposed as settings.

    Attributes:
        candidates (list[Any]): Settings objects, best first — every
            ``BaseSettings`` instance found, followed by the result of every
            zero-arg factory that builds one. Empty when the working
            directory is not a Tempest project or exposes nothing usable.
        has_settings_file (bool): Whether any code root carries a
            ``core/settings.py`` at all. This is what separates "not a
            project" (skip) from "a project whose settings cannot be used"
            (fail).
        modules (list[Any]): The imported settings modules, in code-root
            order. Kept so a caller that historically accepted a plain
            attribute named ``settings`` can still read it.
        notes (list[str]): Human-readable reasons an import or a module
            yielded nothing, in the order they were hit. The caller decides
            whether to print them.
    """

    candidates: list[Any] = field(default_factory=list)
    has_settings_file: bool = False
    modules: list[Any] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def ensure_project_on_path(project_root: Path | None = None) -> Path:
    """Put the project root at the front of ``sys.path``.

    Every command that imports ``<root>.something`` has to do this
    **before its first import**, not before the import it cares about:
    once ``src`` resolves to some other directory, the package object is
    cached under that name and a later ``sys.path`` insert changes
    nothing. The symptom is a submodule that "does not exist" while its
    file is plainly there.

    The entry is moved to the front rather than appended, because an
    earlier entry from another project would keep winning.

    Args:
        project_root (Path | None): Root to import from. Defaults to the
            current working directory.

    Returns:
        Path: The resolved root that was put on the path.
    """
    root = (project_root or Path.cwd()).resolve()
    entry = str(root)
    while entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)
    return root


def settings_instances(module: object) -> list[Any]:
    """List the settings instances a project module exposes.

    The preferred names come first so a module holding more than one
    instance resolves predictably; the remainder is whatever else on the
    module is a ``BaseSettings`` instance, in definition order. The
    instance's *type* is the real test — its *name* is a convention no
    service signed up for.

    Args:
        module (object): The imported ``core.settings`` module.

    Returns:
        list[Any]: Candidate instances, best first. Empty when the module
        exposes none.
    """
    from pydantic_settings import BaseSettings

    members: dict[str, Any] = vars(module)
    found: list[Any] = []
    seen: set[int] = set()
    for name in _PREFERRED_INSTANCE_ATTRS:
        value = members.get(name)
        if isinstance(value, BaseSettings):
            found.append(value)
            seen.add(id(value))
    for name, value in members.items():
        if name.startswith("__") or id(value) in seen:
            continue
        if isinstance(value, BaseSettings):
            found.append(value)
            seen.add(id(value))
    return found


def _is_zero_arg(value: object) -> bool:
    """Return whether ``value`` can be called with no arguments.

    Args:
        value (object): The candidate callable.

    Returns:
        bool: True when no parameter is required, positionally or by
        keyword. ``*args`` / ``**kwargs`` do not count as required.
    """
    try:
        signature = inspect.signature(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    empty = inspect.Parameter.empty
    for parameter in signature.parameters.values():
        if parameter.default is empty and parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return False
    return True


def _annotated_to_settings(value: object) -> bool:
    """Return whether ``value``'s return annotation is a ``BaseSettings``.

    Args:
        value (object): The candidate callable.

    Returns:
        bool: True when ``value`` is annotated to return a ``BaseSettings``
        subclass. An annotation that cannot be resolved (a forward
        reference, a missing import) reads as False rather than raising.
    """
    from pydantic_settings import BaseSettings

    try:
        annotation = inspect.signature(value).return_annotation  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if annotation is inspect.Signature.empty:
        return False
    return bool(isinstance(annotation, type) and issubclass(annotation, BaseSettings))


def _call_factory(value: object) -> object | None:
    """Call a zero-arg factory, returning its result or ``None``.

    A factory that raises — missing environment, a validation error — is
    treated as "this candidate yielded nothing": the next source gets a
    turn, rather than one broken accessor aborting the whole lookup.

    Args:
        value (object): The factory callable.

    Returns:
        object | None: Whatever the factory returned, or ``None`` when it
        raised.
    """
    try:
        result: object = value()  # type: ignore[operator]
    except Exception:
        return None
    return result


def _settings_factories(module: object) -> list[Any]:
    """Build the settings objects a module's zero-arg factories return.

    ``get_settings`` and ``get_config`` are tried first by name — the
    convention the SDK documents — and then any other zero-arg callable
    annotated to return a ``BaseSettings`` subclass, so a service that
    named its accessor ``build_config`` is still found by its signature.

    Args:
        module (object): The imported ``core.settings`` module.

    Returns:
        list[Any]: Factory results, best first, skipping the ones that did
        not produce a ``BaseSettings``.
    """
    from pydantic_settings import BaseSettings

    members: dict[str, Any] = vars(module)
    built: list[Any] = []
    seen: set[int] = set()

    def _consider(value: object) -> None:
        if not callable(value) or not _is_zero_arg(value):
            return
        result = _call_factory(value)
        if isinstance(result, BaseSettings) and id(result) not in seen:
            built.append(result)
            seen.add(id(result))

    for name in _PREFERRED_FACTORY_ATTRS:
        _consider(members.get(name))
    for name, value in members.items():
        if name.startswith("__") or name in _PREFERRED_FACTORY_ATTRS:
            continue
        if _annotated_to_settings(value):
            _consider(value)
    return built


def discover_settings(
    project_root: Path | None = None,
    *,
    notes: list[str] | None = None,
) -> SettingsDiscovery:
    """Find the settings the project at ``project_root`` exposes.

    Each code root is probed in :data:`CODE_ROOTS` order. Under a root
    that carries ``core/settings.py`` the module is imported and asked for
    a ``BaseSettings`` instance first, then for a zero-arg factory that
    returns one — in either case best first, so an instance still wins
    over a factory.

    Args:
        project_root (Path | None): Root to import from. Defaults to the
            current working directory.
        notes (list[str] | None): Collector for the diagnostics. When
            omitted, a private list is used and discarded.

    Returns:
        SettingsDiscovery: The candidates found, whether any settings file
        existed, the imported modules and the diagnostics.
    """
    collector: list[str] = notes if notes is not None else []
    root = ensure_project_on_path(project_root)
    candidates: list[Any] = []
    modules: list[Any] = []
    has_settings_file = False
    for code_root in CODE_ROOTS:
        module_path = root.joinpath(code_root, *_SETTINGS_MODULE_SUBPATH)
        if not module_path.is_file():
            continue
        has_settings_file = True
        dotted = f"{code_root}.core.settings"
        try:
            module = importlib.import_module(dotted)
        except Exception as exc:
            collector.append(f"note: importing {dotted} failed: {exc!r}")
            continue
        modules.append(module)
        module_candidates = settings_instances(module)
        module_candidates += _settings_factories(module)
        if not module_candidates:
            collector.append(
                f"note: {dotted} imported, but exposes no "
                "pydantic_settings.BaseSettings instance and no zero-arg "
                "callable returning one."
            )
            continue
        candidates.extend(module_candidates)
    return SettingsDiscovery(
        candidates=candidates,
        has_settings_file=has_settings_file,
        modules=modules,
        notes=collector,
    )


def read_dotenv_key(env_file: Path, key: str) -> str | None:
    """Read one assignment out of a ``.env`` file.

    ``pydantic-settings`` treats ``.env`` as a first-class source and every
    service scaffolded by this SDK points at one, so a project with the
    value only in the file is the normal development case — checking
    ``os.environ`` alone reported "no database URL" to someone looking at
    the URL in their editor.

    Only the assignment form is honored: ``KEY=value``, optionally
    ``export``-prefixed, optionally quoted. Interpolation and multi-line
    values are not — a value needing either belongs in the environment.

    Args:
        env_file (Path): Path to the candidate ``.env``.
        key (str): The variable name to read.

    Returns:
        str | None: The value, or ``None`` when the file is absent or
        carries no usable assignment for ``key``.
    """
    try:
        lines: list[str] = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw in lines:
        line: str = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.removeprefix("export ").partition("=")
        if name.strip() != key:
            continue
        candidate: str = value.strip()
        if len(candidate) >= 2 and candidate[0] == candidate[-1] in "\"'":
            candidate = candidate[1:-1]
        if candidate:
            return candidate
    return None


def project_database_url(
    project_root: Path | None = None,
    *,
    notes: list[str] | None = None,
) -> str | None:
    """Resolve ``DATABASE_URL`` from the project's settings, then ``.env``.

    Options 3 and 4 of the documented order — the environment variable and
    the explicit flag are the caller's to handle, because they differ per
    command.

    Args:
        project_root (Path | None): Root to import from. Defaults to the
            current working directory.
        notes (list[str] | None): Collector for the diagnostics.

    Returns:
        str | None: The first non-empty ``DATABASE_URL`` a settings
        candidate carries, the ``.env`` value as a fallback, or ``None``
        when neither provides one.
    """
    collector: list[str] = notes if notes is not None else []
    root = (project_root or Path.cwd()).resolve()
    discovery = discover_settings(root, notes=collector)
    for candidate in discovery.candidates:
        url = getattr(candidate, "DATABASE_URL", None)
        if isinstance(url, str) and url:
            return url
    if discovery.candidates:
        collector.append(
            "note: the project's settings expose no non-empty DATABASE_URL."
        )
    from_dotenv = read_dotenv_key(root / ".env", "DATABASE_URL")
    if from_dotenv:
        return from_dotenv
    return None


__all__: list[str] = [
    "CODE_ROOTS",
    "SettingsDiscovery",
    "discover_settings",
    "ensure_project_on_path",
    "project_database_url",
    "read_dotenv_key",
    "settings_instances",
]
