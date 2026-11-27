"""MkDocs hook: render ``reference/top.md`` as an index of the root re-exports.

The top-level page used to run ``::: tempest_fastapi_sdk`` and render every
one of the 800+ names the package root re-exports, each with its full
docstring. Every one of them is also documented on a module page, so the
page was a second copy of the reference: 22 819 038 bytes of HTML in the
0.374.0 build, the largest page of the site (#878).

This hook replaces the :data:`MARKER` comment on that page with one list per
module page: each name is an ``autorefs`` link to the anchor where that page
documents it, and a deprecated alias links to the name it aliases. The list
is derived from ``tempest_fastapi_sdk.__all__`` at build time, so a new root
export shows up here without editing the page; a
name whose anchor no page renders makes ``autorefs`` warn, which fails
``mkdocs build --strict``.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mkdocs.config.defaults import MkDocsConfig
    from mkdocs.structure.files import Files
    from mkdocs.structure.pages import Page

PAGE: str = "reference/top.md"
"""Source path of the page the hook fills."""

MARKER: str = "<!-- reference-top-index -->"
"""Comment on :data:`PAGE` that the generated index replaces."""

ROOT_PACKAGE: str = "tempest_fastapi_sdk"
"""Package whose root re-exports the page lists."""

_TITLE_RE: re.Pattern[str] = re.compile(r"^# (.+)$", re.MULTILINE)
_MODULE_RE: re.Pattern[str] = re.compile(r"`(tempest_fastapi_sdk[\w.]*)`")


def module_pages(docs_dir: Path) -> dict[str, str]:
    """Map each documented module to its reference page.

    Args:
        docs_dir (Path): The MkDocs ``docs_dir``.

    Returns:
        dict[str, str]: Dotted module path to the page's path relative to
        ``docs/reference/`` (``"admin-data-api/db.md"``), read from the
        title every per-module page opens with (``# `module```, or several
        modules separated by commas).
    """
    found: dict[str, str] = {}
    reference = docs_dir / "reference"
    for page in sorted(reference.glob("*/*.md")):
        if page.name.endswith(".en.md"):
            continue
        title = _TITLE_RE.search(page.read_text(encoding="utf-8"))
        if title is None:
            continue
        for module in _MODULE_RE.findall(title.group(1)):
            found[module] = page.relative_to(reference).as_posix()
    return found


def owning_module(value: object, package: str, pages: dict[str, str]) -> str:
    """Return the documented module a root export belongs to.

    Args:
        value (object): The exported object.
        package (str): Package the root loads it from
            (``tempest_fastapi_sdk._LAZY_ATTRIBUTES``).
        pages (dict[str, str]): Output of :func:`module_pages`.

    Returns:
        str: The longest key of ``pages`` that prefixes the module defining
        the object (its ``__module__``; a constant has none, so ``package``
        stands in). ``package`` itself when no page matches.
    """
    defined = getattr(value, "__module__", None)
    if not isinstance(defined, str) or not defined.startswith(ROOT_PACKAGE):
        defined = package
    matches = [
        module
        for module in pages
        if defined == module or defined.startswith(module + ".")
    ]
    return max(matches, key=len) if matches else package


def alias_target(name: str, package: str, exports: dict[str, object]) -> str | None:
    """Return the root export ``name`` is a second name for, if any.

    The deprecated pre-0.76 aliases (``CPF = CPFField``) are left out of the
    reference on purpose, so their link points at the name they alias. They
    are ``Annotated`` types, which carry no ``__name__``, and identity does
    not tell them apart either: ``typing`` caches ``Annotated[int, Ge(0)]``,
    so two separately written fields can be the same object. What marks an
    alias is the source line ``NAME = OTHER``, read from the package's files.

    Args:
        name (str): The exported name.
        package (str): Package the root loads it from.
        exports (dict[str, object]): Every root export, name to object.

    Returns:
        str | None: ``OTHER`` when a module of ``package`` binds ``name`` to
        another root export bound to the same object; ``None`` otherwise.
    """
    spec = importlib.util.find_spec(package)
    if spec is None or spec.origin is None:
        return None
    origin = Path(spec.origin)
    files = (
        sorted(origin.parent.rglob("*.py"))
        if origin.name == "__init__.py"
        else [origin]
    )
    pattern = re.compile(rf"^{re.escape(name)}(?:: [^=]+)? = (\w+)$", re.MULTILINE)
    for file in files:
        for other in pattern.findall(file.read_text(encoding="utf-8")):
            if other in exports and exports[other] is exports[name]:
                return str(other)
    return None


def identifier(package: str, name: str) -> str:
    """Return the ``autorefs`` identifier of a name re-exported by ``package``.

    ``package.name`` is the identifier ``mkdocstrings`` registers for the
    re-export, except when a submodule of ``package`` has the same name as
    the function it defines (``tempest_fastapi_sdk.authz.requires``): the
    static analysis resolves that path to the module, so the link has to
    name the function inside it.

    Args:
        package (str): Package the root loads the name from.
        name (str): The exported name.

    Returns:
        str: The identifier to link to.
    """
    path = f"{package}.{name}"
    if _is_package(package) and importlib.util.find_spec(path) is not None:
        return f"{path}.{name}"
    return path


def _is_package(module: str) -> bool:
    """Tell whether ``module`` is a package, whose submodules can be looked up.

    Args:
        module (str): Dotted module path.

    Returns:
        bool: ``True`` when the module has a ``__path__``.
    """
    spec = importlib.util.find_spec(module)
    return spec is not None and spec.submodule_search_locations is not None


def render_index(
    lazy: dict[str, str], exported: list[str], pages: dict[str, str]
) -> str:
    """Build the Markdown that replaces :data:`MARKER`.

    Args:
        lazy (dict[str, str]): Root export name to the package it is loaded
            from (``tempest_fastapi_sdk._LAZY_ATTRIBUTES``).
        exported (list[str]): ``tempest_fastapi_sdk.__all__``.
        pages (dict[str, str]): Output of :func:`module_pages`.

    Returns:
        str: One ``##`` section per module page, sorted by module, each
        linking that page and listing its names as ``autorefs`` links,
        sorted case-insensitively. An alias links to the name it aliases
        (see :func:`alias_target`). Names outside ``lazy``
        (``__version__``) are left out.
    """
    exports: dict[str, object] = {
        name: getattr(importlib.import_module(lazy[name]), name)
        for name in exported
        if name in lazy
    }
    groups: dict[str, list[str]] = defaultdict(list)
    for name, value in exports.items():
        package = lazy[name]
        target = alias_target(name, package, exports)
        label = f"`{name}` (= `{target}`)" if target else f"`{name}`"
        link = identifier(package, target or name)
        groups[owning_module(value, package, pages)].append(f"[{label}][{link}]")
    parts: list[str] = []
    for module in sorted(groups):
        entries = sorted(groups[module], key=str.lower)
        short = module.removeprefix(f"{ROOT_PACKAGE}.")
        page = pages.get(module)
        where = f"[`{short}`]({page})" if page else f"`{short}`"
        links = " · ".join(entries)
        parts.append(
            f"## `{short}`\n\n{len(entries)} nomes, documentados em {where}.\n\n"
            f"{links}\n"
        )
    return "\n".join(parts)


def on_page_markdown(
    markdown: str, page: Page, config: MkDocsConfig, files: Files
) -> str:
    """Fill the top-level index on :data:`PAGE`.

    Args:
        markdown (str): The page's Markdown source.
        page (Page): The page being rendered.
        config (MkDocsConfig): The MkDocs config.
        files (Files): Every file of the build (unused).

    Returns:
        str: ``markdown`` unchanged for every other page; for :data:`PAGE`,
        with :data:`MARKER` replaced by the generated index.
    """
    if page.file.src_uri != PAGE or MARKER not in markdown:
        return markdown
    root = importlib.import_module(ROOT_PACKAGE)
    index = render_index(
        dict(root._LAZY_ATTRIBUTES),
        list(root.__all__),
        module_pages(Path(config["docs_dir"])),
    )
    return markdown.replace(MARKER, index)
