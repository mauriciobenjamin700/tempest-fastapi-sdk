"""MkDocs hook: emit ``/llms.txt``, the LLM text bundles and one ``.md`` per page.

Implements the https://llmstxt.org convention from the **source** Markdown,
not the rendered HTML. Reading source sidesteps the ``mkdocs-static-i18n``
interaction that resets per-locale page state (which breaks the off-the-shelf
``mkdocs-llmstxt`` plugin here) and keeps the output as clean Markdown —
exactly what a model wants.

What lands in the built site:

* ``<page>.md`` next to the HTML of every page, in both locales: the source
  of ``docs/recipes/genai.md`` is served at ``/recipes/genai.md`` and its
  English mirror at ``/en/recipes/genai.md``. The path mirrors ``docs/``, so
  every relative ``.md`` link the page already has keeps pointing at the
  right file; the hook still rewrites them (and the site-path and asset
  links) to absolute URLs, so a model that fetched one page can follow them
  without knowing where it came from. The front matter is dropped,
  ``include-markdown`` is resolved, the ``reference/top`` marker is replaced
  by its index and every ``:::`` directive becomes a link to the rendered
  symbol.
* ``llms.txt`` — an index in English: title, summary, then one
  ``- [title](url): description`` bullet per page, grouped by the sections of
  the English ``nav``. The URL is the page's ``.md`` and the description is
  the ``description:`` of its front matter. The API reference links the
  default-locale pages, because the English ones are pointers to them.
* ``llms-core.txt`` — the pages a model needs to start using the SDK
  (:data:`CORE_PAGES`) plus every recipe as a ``title: description`` link,
  under :data:`CORE_MAX_BYTES`.
* ``llms-full.txt`` — every English page of the ``nav`` except the API
  reference, whose pages are ``:::`` directives that carry no text of their
  own; the reference stays reachable through ``llms.txt``.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import posixpath
import re
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from mkdocs.utils.meta import get_data

if TYPE_CHECKING:
    from mkdocs.config.defaults import MkDocsConfig

log: logging.Logger = logging.getLogger("mkdocs.hooks.llmstxt")
"""Under ``mkdocs.``, so a warning fails ``mkdocs build --strict``."""

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
    "Python >= 3.12."
)
"""One paragraph giving a model the whole mental model up front."""

LLMS_LOCALE: str = "en"
"""Locale of ``llms.txt``, ``llms-core.txt`` and ``llms-full.txt``.

English because the readers are models and tools, which handle English best
and which the summary above already addressed; the Portuguese pages are one
``/en/`` away from every link.
"""

DEFAULT_LOCALE: str = "pt"
"""Locale built at the site root (no URL prefix)."""

CORE_PAGES: tuple[str, ...] = (
    "index.md",
    "installation.md",
    "architecture.md",
    "recommended-architecture.md",
    "service-standard.md",
    "tutorial.md",
    "recipes/index.md",
)
"""Pages inlined in ``llms-core.txt``, in reading order."""

CORE_MAX_BYTES: int = 400_000
"""Ceiling of ``llms-core.txt``: about 100k tokens at four bytes per token."""

CORE_RECIPES_SECTION: str = "Recipes"
"""``llms.txt`` section whose links ``llms-core.txt`` lists after the pages."""

REFERENCE_PREFIXES: tuple[str, ...] = ("reference.md", "reference/")
"""Source paths that make up the API reference."""

_UNGROUPED_SECTION: str = "Overview"
"""Section used for top-level nav entries that are a bare page."""

_INCLUDE_RE: re.Pattern[str] = re.compile(r'{%\s*include-markdown\s+"([^"]+)"\s*%}')
_DIRECTIVE_RE: re.Pattern[str] = re.compile(r"^:::\s+(\S+)")
_FENCE_RE: re.Pattern[str] = re.compile(r"^\s*(```|~~~)")
_LINK_RE: re.Pattern[str] = re.compile(r"(\]\()([^)\s]+)(\))")
_ASSET_RE: re.Pattern[str] = re.compile(r"\.(png|jpe?g|gif|svg|webp|ico)$")
_AUTOREF_RE: re.Pattern[str] = re.compile(r"\[(`([^`]+)`[^\]]*)\]\[[^\]]+\]")
_HEADING_ID_RE: re.Pattern[str] = re.compile(
    r'<h[1-6] id="([^"]+)" class="doc doc-heading"'
)
_HTML_SYMBOL_RE: re.Pattern[str] = re.compile(
    r'<a class="autorefs autorefs-internal"[^>]*href="([^"]+)"><code>([^<]+)</code>'
)


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


def _collect(entry: object, out: list[str]) -> None:
    """Append every ``.md`` path reachable from a nav entry to ``out``.

    Args:
        entry (object): A nav entry — a path string, a mapping or a list.
        out (list[str]): Accumulator, appended in place.
    """
    if isinstance(entry, str):
        if entry.endswith(".md"):
            out.append(entry)
    elif isinstance(entry, dict):
        for value in entry.values():
            _collect(value, out)
    elif isinstance(entry, list):
        for item in entry:
            _collect(item, out)


def _nav_sections(nav: object) -> dict[str, list[str]]:
    """Derive ``{section title: [source paths]}`` from a MkDocs ``nav``.

    Walking the nav is what keeps this index honest: an earlier hard-coded
    list silently stopped covering new pages (25 of 73 were missing), so
    anything shipped after it was written became invisible to LLM consumers
    even though it was on the site.

    Every titled top-level entry becomes its own section, under its nav
    label and in nav order, even when it holds a single page. Pooling the
    single-page entries into one block put ``Tutorial``, ``Roadmap``,
    ``Contributing`` and ``Changelog`` under ``Overview``, ahead of
    ``Installation``, so ``llms.txt`` no longer read in the order the nav
    teaches. Only a bare path at the top level, which has no label, goes to
    :data:`_UNGROUPED_SECTION`.

    Args:
        nav (object): A nav as MkDocs parsed it from YAML — a list whose
            items are either a path string or a single-key mapping of title
            to path, or to a nested list.

    Returns:
        dict[str, list[str]]: Sections in nav order, each holding the
        default-locale source paths it groups. Nested sub-sections are
        flattened into their top-level parent.
    """
    sections: dict[str, list[str]] = {}
    if not isinstance(nav, list):
        return sections
    for item in nav:
        if isinstance(item, str):
            if item.endswith(".md"):
                sections.setdefault(_UNGROUPED_SECTION, []).append(item)
            continue
        if not isinstance(item, dict):
            continue
        for title, value in item.items():
            pages: list[str] = []
            _collect(value, pages)
            if pages:
                sections.setdefault(str(title), []).extend(pages)
    return sections


def _locale_nav(config: MkDocsConfig, locale: str) -> object:
    """Return the nav declared for ``locale`` by the ``i18n`` plugin.

    The plugin rebuilds the site once per locale and swaps ``config["nav"]``
    in between, so reading ``config["nav"]`` would depend on which build the
    hook runs in. The per-locale nav is fixed configuration.

    Args:
        config (MkDocsConfig): The MkDocs config.
        locale (str): Locale code (``"en"``).

    Returns:
        object: That locale's nav, or ``config["nav"]`` when the plugin is
        absent or the locale declares none.
    """
    plugin = config.plugins.get("i18n")
    languages = getattr(getattr(plugin, "config", None), "languages", None) or []
    for language in languages:
        if getattr(language, "locale", None) == locale and language.nav:
            return language.nav
    return config["nav"]


def is_reference(src: str) -> bool:
    """Tell whether a source path belongs to the API reference.

    Args:
        src (str): Default-locale source path relative to ``docs/``.

    Returns:
        bool: ``True`` for ``reference.md`` and anything under ``reference/``.
    """
    return src.startswith(REFERENCE_PREFIXES)


def locale_source(docs_dir: Path, src: str, locale: str) -> Path:
    """Return the file that holds ``src`` in ``locale``.

    Args:
        docs_dir (Path): The MkDocs ``docs_dir``.
        src (str): Default-locale source path (``recipes/genai.md``).
        locale (str): Locale code.

    Returns:
        Path: ``<page>.<locale>.md`` when it exists, else the default-locale
        file — the same fallback ``mkdocs-static-i18n`` applies.
    """
    if locale != DEFAULT_LOCALE:
        translated = docs_dir / f"{src[: -len('.md')]}.{locale}.md"
        if translated.is_file():
            return translated
    return docs_dir / src


def md_url(site_url: str, src: str, locale: str) -> str:
    """Return the URL the ``.md`` of a page is served at.

    Args:
        site_url (str): ``site_url`` without the trailing slash.
        src (str): Default-locale source path.
        locale (str): Locale code.

    Returns:
        str: ``<site_url>/<src>`` for the default locale and
        ``<site_url>/<locale>/<src>`` for the others.
    """
    prefix = "" if locale == DEFAULT_LOCALE else f"{locale}/"
    return f"{site_url}/{prefix}{src}"


def html_url(site_url: str, src: str, locale: str) -> str:
    """Return the URL of a page's HTML (MkDocs ``use_directory_urls``).

    Args:
        site_url (str): ``site_url`` without the trailing slash.
        src (str): Default-locale source path.
        locale (str): Locale code.

    Returns:
        str: The directory URL, with the locale prefix outside the default.
    """
    prefix = "" if locale == DEFAULT_LOCALE else f"{locale}/"
    path = src[: -len(".md")]
    if path == "index":
        return f"{site_url}/{prefix}"
    path = path.removesuffix("/index")
    return f"{site_url}/{prefix}{path}/"


def first_heading(text: str, fallback: str) -> str:
    """Return the first ``# `` heading of a Markdown body.

    Args:
        text (str): Markdown without front matter.
        fallback (str): Returned when no heading exists.

    Returns:
        str: The heading text, or ``fallback``.
    """
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _resolve_link(target: str, src: str, locale: str, site_url: str) -> str:
    """Turn one relative link target into an absolute URL.

    Args:
        target (str): The ``(...)`` part of a Markdown link.
        src (str): Default-locale source path of the page holding the link.
        locale (str): Locale of the page.
        site_url (str): ``site_url`` without the trailing slash.

    Returns:
        str: The absolute URL; ``target`` unchanged when it is already
        absolute, an in-page anchor, or none of the kinds below. A ``.md``
        target is resolved against the source directory and served as
        ``.md``; a target ending in ``/`` is a site path written relative to
        the page's HTML URL; an image is resolved against the source
        directory at the site root, where MkDocs copies it.
    """
    if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith(("#", "/")):
        return target
    path, _, anchor = target.partition("#")
    suffix = f"#{anchor}" if anchor else ""
    source_dir = posixpath.dirname(src)
    if path.endswith(".md"):
        resolved = posixpath.normpath(posixpath.join(source_dir, path))
        if resolved.endswith(f".{locale}.md"):
            resolved = resolved[: -len(f".{locale}.md")] + ".md"
        return md_url(site_url, resolved, locale) + suffix
    if path.endswith("/"):
        base = html_url(site_url, src, locale)
        relative = base.removeprefix(f"{site_url}/")
        joined = posixpath.normpath(posixpath.join(relative, path))
        joined = "" if joined == "." else f"{joined}/"
        return f"{site_url}/{joined}{suffix}"
    if _ASSET_RE.search(path):
        resolved = posixpath.normpath(posixpath.join(source_dir, path))
        return f"{site_url}/{resolved}{suffix}"
    return target


def _rewrite_links(text: str, src: str, locale: str, site_url: str) -> str:
    """Make every relative link of a page absolute, outside code fences.

    Args:
        text (str): Markdown body.
        src (str): Default-locale source path.
        locale (str): Locale of the page.
        site_url (str): ``site_url`` without the trailing slash.

    Returns:
        str: ``text`` with the targets rewritten by :func:`_resolve_link`.
    """
    out: list[str] = []
    fenced = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            fenced = not fenced
        elif not fenced:
            line = _LINK_RE.sub(
                lambda m: (
                    m.group(1)
                    + _resolve_link(m.group(2), src, locale, site_url)
                    + m.group(3)
                ),
                line,
            )
        out.append(line)
    return "\n".join(out) + "\n"


def rendered_symbols(html_file: Path) -> list[str]:
    """List the symbols ``mkdocstrings`` rendered on a built page.

    Args:
        html_file (Path): The page's ``index.html``.

    Returns:
        list[str]: The ``id`` of every ``doc-heading`` (module, class,
        function, method, attribute), in page order. Empty when the file does
        not exist.
    """
    if not html_file.is_file():
        return []
    return _HEADING_ID_RE.findall(html_file.read_text(encoding="utf-8"))


def _replace_directives(text: str, page_html: str, symbols: list[str]) -> str:
    """Replace the ``:::`` directives of a page with links to its symbols.

    A directive carries no text of its own, and its options are build
    configuration.

    Args:
        text (str): Markdown body.
        page_html (str): Absolute URL of the rendered page.
        symbols (list[str]): Output of :func:`rendered_symbols` for the page.

    Returns:
        str: ``text`` with the first ``::: identifier`` block (the directive
        plus the indented option lines after it) turned into one bullet per
        rendered symbol, linking its anchor, and the other blocks removed.
        Without ``symbols`` (page not built), every block becomes one bullet
        for its own identifier.
    """
    out: list[str] = []
    in_options = False
    listed = False
    for line in text.splitlines():
        match = _DIRECTIVE_RE.match(line)
        if match:
            in_options = True
            if not symbols:
                ident = match.group(1)
                out.append(f"- [`{ident}`]({page_html}#{ident})")
            elif not listed:
                out.extend(f"- [`{name}`]({page_html}#{name})" for name in symbols)
                listed = True
            continue
        if in_options and (line.startswith((" ", "\t")) and line.strip()):
            continue
        in_options = False
        out.append(line)
    return "\n".join(out) + "\n"


def _load_reference_top() -> ModuleType:
    """Load the sibling ``reference_top`` hook as a plain module.

    MkDocs loads each hook from its file path, so the hooks are not an
    importable package.

    Returns:
        ModuleType: The ``reference_top`` module.

    Raises:
        ImportError: If the file cannot be loaded.
    """
    path = Path(__file__).with_name("reference_top.py")
    spec = importlib.util.spec_from_file_location("_llmstxt_reference_top", path)
    if spec is None or spec.loader is None:  # pragma: no cover - file is fixed
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _top_index(docs_dir: Path, site_dir: Path, site_url: str) -> str:
    """Render the ``reference/top`` index with links that resolve outside MkDocs.

    The page's own hook emits ``autorefs`` links (``[label][identifier]``),
    which only MkDocs resolves. The anchors come from the built HTML of the
    page instead: ``autorefs`` already resolved each name to the anchor of
    the definition, which is not the re-export identifier.

    Args:
        docs_dir (Path): The MkDocs ``docs_dir``.
        site_dir (Path): The built site.
        site_url (str): ``site_url`` without the trailing slash.

    Returns:
        str: The index, each name linking the absolute URL of its anchor.
        Empty when the page has not been built.
    """
    top_dir = site_dir / "reference" / "top"
    html_file = top_dir / "index.html"
    if not html_file.is_file():
        return ""
    hrefs: dict[str, str] = {}
    for href, name in _HTML_SYMBOL_RE.findall(html_file.read_text(encoding="utf-8")):
        path, _, anchor = href.partition("#")
        page = posixpath.normpath(posixpath.join("reference/top", path))
        hrefs.setdefault(name, f"{site_url}/{page}/#{anchor}")
    module = _load_reference_top()
    root = importlib.import_module(module.ROOT_PACKAGE)
    index: str = module.render_index(
        dict(root._LAZY_ATTRIBUTES),
        list(root.__all__),
        module.module_pages(docs_dir),
    )

    def link(match: re.Match[str]) -> str:
        """Point one ``autorefs`` link at its anchor's absolute URL.

        Args:
            match (re.Match[str]): A ``[`name`...][identifier]`` match.

        Returns:
            str: A plain Markdown link, or the label alone when the built
            page has no anchor for the name.
        """
        target = hrefs.get(match.group(2))
        if target is None:
            return match.group(1)
        return f"[{match.group(1)}]({target})"

    return _AUTOREF_RE.sub(link, index)


def page_markdown(
    raw: str,
    src: str,
    locale: str,
    site_url: str,
    docs_dir: Path,
    top_index: str,
    symbols: list[str],
) -> tuple[str, dict[str, object]]:
    """Turn a source page into the Markdown served next to its HTML.

    Args:
        raw (str): The source file, front matter included.
        src (str): Default-locale source path.
        locale (str): Locale of ``raw``.
        site_url (str): ``site_url`` without the trailing slash.
        docs_dir (Path): The MkDocs ``docs_dir``.
        top_index (str): Output of :func:`_top_index`, put in place of the
            ``reference/top`` marker.
        symbols (list[str]): Output of :func:`rendered_symbols` for the page,
            listed in place of its ``:::`` directives.

    Returns:
        tuple[str, dict[str, object]]: The Markdown body and the page's front
        matter.
    """
    body, meta = get_data(raw)
    source_path = docs_dir / src

    def include(match: re.Match[str]) -> str:
        """Inline one ``include-markdown`` target.

        Args:
            match (re.Match[str]): The directive.

        Returns:
            str: The target file, or the directive when it cannot be read.
        """
        target = (source_path.parent / match.group(1)).resolve()
        try:
            return target.read_text(encoding="utf-8")
        except OSError:
            return match.group(0)

    body = _INCLUDE_RE.sub(include, body)
    if "<!-- reference-top-index -->" in body:
        body = body.replace("<!-- reference-top-index -->", top_index)
    body = _replace_directives(body, html_url(site_url, src, DEFAULT_LOCALE), symbols)
    return _rewrite_links(body, src, locale, site_url), meta


def _source_pages(config: MkDocsConfig, docs_dir: Path) -> list[str]:
    """List every default-locale page of the build.

    Args:
        config (MkDocsConfig): The MkDocs config.
        docs_dir (Path): The MkDocs ``docs_dir``.

    Returns:
        list[str]: Source paths relative to ``docs_dir``, without the
        translated ``.<locale>.md`` files and without what ``exclude_docs``
        leaves out of the site.
    """
    excluded = config["exclude_docs"]
    pages: list[str] = []
    for path in sorted(docs_dir.rglob("*.md")):
        src = path.relative_to(docs_dir).as_posix()
        if re.search(r"\.[a-z]{2}(_[A-Z]{2})?\.md$", src):
            continue
        if excluded is not None and excluded.match_file(src):
            continue
        pages.append(src)
    return pages


def _bullet(title: str, url: str, description: str) -> str:
    """Format one ``llms.txt`` link.

    Args:
        title (str): Page title.
        url (str): URL of the page's ``.md``.
        description (str): One-line description.

    Returns:
        str: ``- [title](url): description``.
    """
    return f"- [{title}]({url}): {description}\n"


def on_post_build(config: MkDocsConfig) -> None:
    """Write the per-page ``.md`` files and the three ``llms`` files.

    ``mkdocs-static-i18n`` runs the build once per locale, so this hook runs
    twice; both runs write the same bytes.

    Args:
        config (MkDocsConfig): The MkDocs config.
    """
    docs_dir = Path(config["docs_dir"])
    site_dir = Path(config["site_dir"])
    site_name = config["site_name"]
    site_url = (config["site_url"] or "").rstrip("/")
    top_index = _top_index(docs_dir, site_dir, site_url)

    plugin = config.plugins.get("i18n")
    languages = getattr(getattr(plugin, "config", None), "languages", None) or []
    locales = [lang.locale for lang in languages if lang.build] or [DEFAULT_LOCALE]

    rendered: dict[tuple[str, str], tuple[str, str, str]] = {}
    for locale in locales:
        for src in _source_pages(config, docs_dir):
            file = locale_source(docs_dir, src, locale)
            symbols: list[str] = []
            if locale == DEFAULT_LOCALE and is_reference(src):
                built = html_url(site_url, src, locale).removeprefix(site_url)
                symbols = rendered_symbols(site_dir / built.strip("/") / "index.html")
            body, meta = page_markdown(
                file.read_text(encoding="utf-8"),
                src,
                locale,
                site_url,
                docs_dir,
                top_index,
                symbols,
            )
            prefix = Path() if locale == DEFAULT_LOCALE else Path(locale)
            target = site_dir / prefix / src
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
            description = meta.get("description")
            if not isinstance(description, str) or not description.strip():
                log.warning("%s has no 'description:' in its front matter", file)
                description = ""
            rendered[(locale, src)] = (
                first_heading(body, src),
                " ".join(description.split()),
                body,
            )

    extras = _extras(Path(config["config_file_path"]).parent / "pyproject.toml")
    summary = _SUMMARY
    if extras:
        listed = ", ".join(f"[{name}]" for name in extras)
        summary = f"{summary} Optional features ship as extras: {listed}."
    head = f"# {site_name}\n\n> {summary}\n"
    index = head + (
        "\nEvery link below is the Markdown source of a page, in English; the "
        "Portuguese original is the same path without `/en/`. "
        f"[llms-core.txt]({site_url}/llms-core.txt) inlines the pages needed "
        "to start (installation, architecture, service standard, tutorial, "
        f"recipe index); [llms-full.txt]({site_url}/llms-full.txt) inlines "
        "every page except the API reference.\n"
    )
    full = head
    sections = _nav_sections(_locale_nav(config, LLMS_LOCALE))
    llms_locale = LLMS_LOCALE if LLMS_LOCALE in locales else DEFAULT_LOCALE
    recipe_links = ""
    for section, files in sections.items():
        index += f"\n## {section}\n\n"
        for src in files:
            entry = rendered.get((llms_locale, src))
            if entry is None:
                continue
            title, description, body = entry
            if is_reference(src):
                url = md_url(site_url, src, DEFAULT_LOCALE)
            else:
                url = md_url(site_url, src, llms_locale)
                full += f"\n\n---\n\nSource: {url}\n\n{body.strip()}\n"
            bullet = _bullet(title, url, description)
            index += bullet
            if section == CORE_RECIPES_SECTION:
                recipe_links += bullet

    core = head
    for src in CORE_PAGES:
        entry = rendered.get((llms_locale, src))
        if entry is None:
            continue
        url = md_url(site_url, src, llms_locale)
        core += f"\n\n---\n\nSource: {url}\n\n{entry[2].strip()}\n"
    core += f"\n\n---\n\n# Recipe index\n\n{recipe_links}"
    if len(core.encode("utf-8")) > CORE_MAX_BYTES:
        log.warning(
            "llms-core.txt has %d bytes, above CORE_MAX_BYTES=%d",
            len(core.encode("utf-8")),
            CORE_MAX_BYTES,
        )

    (site_dir / "llms.txt").write_text(index, encoding="utf-8")
    (site_dir / "llms-core.txt").write_text(core, encoding="utf-8")
    (site_dir / "llms-full.txt").write_text(full, encoding="utf-8")
