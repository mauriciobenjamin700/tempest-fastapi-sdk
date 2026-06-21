"""Auto-discovery of SQLAlchemy models for the admin site.

Lets a project skip the one-``register``-call-per-model boilerplate:
point :meth:`AdminSite.automap` (backed by :func:`discover_models`) at
the package that holds the ORM models and every concrete table is
collected and registered in one shot.

Only **concrete, mapped** ``BaseModel`` subclasses are returned —
abstract bases (``BaseModel`` itself, ``BaseUserModel``,
``BaseUserTokenModel`` and friends carry ``__abstract__ = True`` and no
``__tablename__``) are skipped, so the discovery never trips over the
SDK's own mixins.
"""

from __future__ import annotations

import importlib
import inspect as _inspect
import pkgutil
from collections.abc import Iterable, Sequence
from types import ModuleType

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Mapper

from tempest_fastapi_sdk.db.model import BaseModel


def _iter_modules(root: ModuleType) -> Iterable[ModuleType]:
    """Yield ``root`` plus every importable submodule when it is a package.

    Walking submodules matters because a project usually declares one
    model per file (``src/db/models/user.py``) and may not re-export
    every class in the package ``__init__``. A flat module (no
    ``__path__``) just yields itself.

    Args:
        root (ModuleType): The already-imported package or module.

    Yields:
        ModuleType: ``root`` first, then each successfully imported
        submodule. Submodules that fail to import are skipped silently
        so one broken file never aborts the whole sweep.
    """
    yield root
    paths = getattr(root, "__path__", None)
    if paths is None:
        return
    for info in pkgutil.walk_packages(paths, prefix=f"{root.__name__}."):
        try:
            yield importlib.import_module(info.name)
        except Exception:  # pragma: no cover - defensive: a broken file
            continue


def _is_concrete_model(obj: object) -> bool:
    """Return ``True`` when ``obj`` is a concrete, mapped ``BaseModel``.

    A *mapped* class is exactly a concrete table — abstract bases
    (``BaseModel``, ``BaseUserModel`` and friends carry
    ``__abstract__ = True``) are never mapped, so SQLAlchemy returns no
    mapper for them. We key off the mapper rather than ``__abstract__``
    because that attribute is inherited through the MRO and would read
    ``True`` on concrete subclasses too.

    Args:
        obj (object): A candidate pulled from a module namespace.

    Returns:
        bool: ``True`` only for a class that subclasses ``BaseModel``
        (but is not ``BaseModel`` itself) and is mapped by SQLAlchemy.
    """
    if not _inspect.isclass(obj) or not issubclass(obj, BaseModel):
        return False
    if obj is BaseModel:
        return False
    return isinstance(sa_inspect(obj, raiseerr=False), Mapper)


def discover_models(
    source: str | ModuleType,
    *,
    exclude: Sequence[type[BaseModel] | str] = (),
) -> list[type[BaseModel]]:
    """Collect every concrete ``BaseModel`` subclass under ``source``.

    Args:
        source (str | ModuleType): Either a dotted module path (e.g.
            ``"src.db.models"``) or an already-imported module/package.
            When it is a package, its submodules are imported and
            swept too.
        exclude (Sequence[type[BaseModel] | str]): Models to skip —
            each entry may be the model **class**, its **class name**,
            or its **table name**. Useful to hide a model that should
            not appear in the admin (e.g. an audit-log table).

    Returns:
        list[type[BaseModel]]: Concrete mapped models, de-duplicated by
        table name and ordered by table name for a stable nav.

    Raises:
        ModuleNotFoundError: When ``source`` is a dotted path that
            cannot be imported.
    """
    root = importlib.import_module(source) if isinstance(source, str) else source
    excluded: set[object] = set(exclude)
    found: dict[str, type[BaseModel]] = {}
    for module in _iter_modules(root):
        for _name, obj in _inspect.getmembers(module, _inspect.isclass):
            if not _is_concrete_model(obj):
                continue
            tablename: str = obj.__tablename__
            if obj in excluded or obj.__name__ in excluded or tablename in excluded:
                continue
            found.setdefault(tablename, obj)
    return [found[name] for name in sorted(found)]


__all__: list[str] = [
    "discover_models",
]
