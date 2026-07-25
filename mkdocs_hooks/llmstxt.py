"""MkDocs hook: emit ``/llms.txt`` and ``/llms-full.txt`` for LLM consumers.

Implements the https://llmstxt.org convention from the **source** Markdown
(the PT-BR default-language files), not the rendered HTML. Reading source
sidesteps the ``mkdocs-static-i18n`` interaction that resets per-locale page
state (which breaks the off-the-shelf ``mkdocs-llmstxt`` plugin here) and
keeps the output as clean Markdown — exactly what a model wants.

Two files land at the site root:

* ``llms.txt`` — an index: title, summary, then one bullet per page grouped
  by section, each linking to its published URL. Sections and their pages are
  **derived from the MkDocs ``nav``**, so a page added to the site is listed
  here automatically. An earlier version hard-coded the list and drifted: 25 of
  73 nav pages — every feature shipped after it was written — had gone missing
  from the index.
* ``llms-full.txt`` — every listed page concatenated into a single block, so
  a model can ingest the whole project at once.

``include-markdown`` directives (e.g. the changelog) are resolved against the
referenced file; ``mkdocstrings`` (``:::``) directives in the API reference
are replaced by a short pointer to the rendered reference, since the raw
directive carries no content.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mkdocs.config.defaults import MkDocsConfig

_SUMMARY: str = (
    "tempest-fastapi-sdk holds the shared FastAPI + SQLAlchemy 2.0 (async) + "
    "Pydantic v2 building blocks used across Tempest services. Core: "
    "BaseAppSettings (+ composable settings mixins), BaseModel, "
    "BaseRepository[Model], BaseService, BaseController, base/pagination "
    "schemas, the AppException hierarchy + handlers (with an i18n message "
    "catalog and OpenAPI error documentation), AsyncDatabaseManager and "
    "AlembicHelper. Batteries: JWT/OAuth2 auth with a bundled signup/login/"
    "reset flow and TOTP MFA, a Django-style admin site, typed SSR pages, "
    "Server-Sent Events and WebSockets, Redis cache, FastStream queues and "
    "TaskIQ tasks (retries + dead letters), MinIO/S3 storage and uploads, Web "
    "Push, feature flags, audit trail, rate limiting and idempotency "
    "middleware, Prometheus metrics and OpenTelemetry tracing, self-hosted "
    "generative AI (local LLM, embeddings, RAG, speech), geolocation, "
    "Brazilian document/phone/PIX validation, and a `tempest` CLI that "
    "scaffolds services and generates typed clients from an OpenAPI spec. "
    "Python >= 3.11."
)
"""One paragraph giving a model the whole mental model up front."""


def _extras(pyproject: Path) -> list[str]:
    """Read the distribution's optional-dependency names.

    Derived rather than written out because the previous hard-coded list went
    stale: it still advertised ten extras long after the package shipped more
    than twenty, so a model reading ``llms.txt`` was told features did not
    exist.

    Args:
        pyproject (Path): Path to ``pyproject.toml``.

    Returns:
        list[str]: Extra names in declaration order, ``all`` last. Empty when
        the file cannot be read — the summary then simply omits the list
        rather than the build failing over a nicety.
    """
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - repo layout is fixed
        return []
    block = text.split("[project.optional-dependencies]", 1)
    if len(block) == 1:
        return []
    names: list[str] = []
    for line in block[1].splitlines():
        if line.startswith("["):
            break
        match = re.match(r"^([A-Za-z0-9_-]+)\s*=\s*\[", line)
        if match:
            names.append(match.group(1))
    return [n for n in names if n != "all"] + (["all"] if "all" in names else [])


_UNGROUPED_SECTION: str = "Overview"
"""Section used for top-level nav entries that are a bare page."""


def _nav_sections(nav: object) -> dict[str, list[str]]:
    """Derive ``{section title: [source paths]}`` from the MkDocs ``nav``.

    Walking the nav is what keeps this index honest: the previous hard-coded
    list silently stopped covering new pages, so anything shipped after it was
    written became invisible to LLM consumers even though it was on the site.

    Args:
        nav (object): ``config["nav"]`` as MkDocs parsed it from YAML — a list
            whose items are either a path string or a single-key mapping of
            title to path, or to a nested list.

    Returns:
        dict[str, list[str]]: Sections in nav order, each holding the
        default-language (PT-BR) source paths it groups. Nested sub-sections
        are flattened into their top-level parent, since a two-level index is
        easier for a model to skim than a deep tree.
    """
    sections: dict[str, list[str]] = {}

    def collect(entry: object, out: list[str]) -> None:
        """Append every ``.md`` path reachable from ``entry`` to ``out``."""
        if isinstance(entry, str):
            if entry.endswith(".md"):
                out.append(entry)
        elif isinstance(entry, dict):
            for value in entry.values():
                collect(value, out)
        elif isinstance(entry, list):
            for item in entry:
                collect(item, out)

    if not isinstance(nav, list):
        return sections

    for item in nav:
        if isinstance(item, str):
            sections.setdefault(_UNGROUPED_SECTION, []).extend(
                [item] if item.endswith(".md") else []
            )
            continue
        if not isinstance(item, dict):
            continue
        for title, value in item.items():
            pages: list[str] = []
            collect(value, pages)
            if not pages:
                continue
            # A top-level entry that is a single page gets grouped rather than
            # promoted: a dozen one-line "sections" is harder for a model to
            # skim than one Overview block.
            section = _UNGROUPED_SECTION if len(pages) == 1 else str(title)
            sections.setdefault(section, []).extend(pages)
    return sections


_INCLUDE_RE = re.compile(r'{%\s*include-markdown\s+"([^"]+)"\s*%}')
_MKDOCSTRINGS_RE = re.compile(r"^:::\s+\S+.*$", re.MULTILINE)


def _page_url(site_url: str, src: str) -> str:
    """Map a source path to its published URL (MkDocs ``use_directory_urls``)."""
    path = src[: -len(".md")]
    if path == "index":
        return f"{site_url}/"
    if path.endswith("/index"):
        path = path[: -len("/index")]
    return f"{site_url}/{path}/"


def _first_heading(text: str, fallback: str) -> str:
    """Return the first ``# `` heading, or ``fallback``."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _resolve(text: str, src_path: Path, reference_url: str) -> str:
    """Inline ``include-markdown`` targets and neutralize ``:::`` directives."""

    def _sub_include(match: re.Match[str]) -> str:
        target = (src_path.parent / match.group(1)).resolve()
        try:
            return target.read_text(encoding="utf-8")
        except OSError:
            return match.group(0)

    text = _INCLUDE_RE.sub(_sub_include, text)
    if _MKDOCSTRINGS_RE.search(text):
        text = _MKDOCSTRINGS_RE.sub(
            f"_(Auto-generated API reference — see {reference_url})_", text
        )
    return text


def on_post_build(config: MkDocsConfig) -> None:
    """Write ``llms.txt`` and ``llms-full.txt`` into the built site."""
    docs_dir = Path(config["docs_dir"])
    site_dir = Path(config["site_dir"])
    site_name = config["site_name"]
    site_url = (config["site_url"] or "").rstrip("/")
    reference_url = f"{site_url}/reference/"

    extras = _extras(Path(config["config_file_path"]).parent / "pyproject.toml")
    summary = _SUMMARY
    if extras:
        rendered = ", ".join(f"[{name}]" for name in extras)
        summary = f"{summary} Optional features ship as extras: {rendered}."
    index = f"# {site_name}\n\n> {summary}\n"
    full = f"# {site_name}\n\n> {summary}\n"

    for section, files in _nav_sections(config["nav"]).items():
        index += f"\n## {section}\n\n"
        for src in files:
            src_path = docs_dir / src
            if not src_path.is_file():
                continue
            raw = src_path.read_text(encoding="utf-8")
            title = _first_heading(raw, src)
            url = _page_url(site_url, src)
            index += f"- [{title}]({url})\n"
            body = _resolve(raw, src_path, reference_url)
            full += f"\n\n---\n\n# {title}\n\nSource: {url}\n\n{body.strip()}\n"

    (site_dir / "llms.txt").write_text(index, encoding="utf-8")
    (site_dir / "llms-full.txt").write_text(full, encoding="utf-8")
