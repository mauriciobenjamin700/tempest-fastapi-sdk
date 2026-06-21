"""MkDocs hook: emit ``/llms.txt`` and ``/llms-full.txt`` for LLM consumers.

Implements the https://llmstxt.org convention from the **source** Markdown
(the PT-BR default-language files), not the rendered HTML. Reading source
sidesteps the ``mkdocs-static-i18n`` interaction that resets per-locale page
state (which breaks the off-the-shelf ``mkdocs-llmstxt`` plugin here) and
keeps the output as clean Markdown — exactly what a model wants.

Two files land at the site root:

* ``llms.txt`` — a curated index: title, summary, then one bullet per page
  grouped by section, each linking to its published URL.
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

# One short paragraph giving a model the whole mental model up front.
_DESCRIPTION = (
    "tempest-fastapi-sdk are the shared FastAPI + SQLAlchemy 2.0 (async) + "
    "Pydantic v2 building blocks used across Tempest services: BaseAppSettings, "
    "BaseModel, BaseRepository[Model], BaseService, BaseController, base and "
    "pagination schemas, the AppException hierarchy + handlers, "
    "AsyncDatabaseManager, AlembicHelper, utilities (PasswordUtils, JWTUtils, "
    "EmailUtils, UploadUtils, StoredFileServiceMixin, Brazilian document/phone "
    "helpers), JWT auth (UserAuthService + make_jwt_user_dependency), MinIO/S3 "
    "storage, a FastStream broker and TaskIQ tasks. Optional features ship as "
    "extras ([auth], [email], [upload], [minio], [cache], [queue], [tasks], "
    "[metrics], [webpush], [all]); Python >= 3.11."
)

# Ordered sections → the PT-BR (default-language) source files they contain.
_SECTIONS: dict[str, list[str]] = {
    "Overview": [
        "index.md",
        "installation.md",
        "architecture.md",
        "tutorial.md",
    ],
    "Recipes": [
        "recipes/index.md",
        "recipes/database.md",
        "recipes/multi-tenant.md",
        "recipes/audit-trail.md",
        "recipes/http.md",
        "recipes/http-client.md",
        "recipes/cache.md",
        "recipes/feature-flags.md",
        "recipes/realtime.md",
        "recipes/websocket.md",
        "recipes/queue-tasks.md",
        "recipes/outbox.md",
        "recipes/email.md",
        "recipes/webpush.md",
        "recipes/logging.md",
        "recipes/observability.md",
        "recipes/storage.md",
        "recipes/uploads.md",
        "recipes/stored-files.md",
        "recipes/downloads.md",
        "recipes/idempotency.md",
        "recipes/offline-sync.md",
        "recipes/auth-flow.md",
        "recipes/mfa.md",
        "recipes/sessions.md",
        "recipes/metrics.md",
        "recipes/admin.md",
        "recipes/testing.md",
        "recipes/deploy-safety.md",
        "recipes/cli.md",
        "recipes/security.md",
        "recipes/utilities.md",
        "recipes/br-helpers.md",
    ],
    "Learning (marketplace)": [
        "learning/index.md",
        "learning/marketplace/index.md",
        "learning/marketplace/domain.md",
        "learning/marketplace/business-rules.md",
        "learning/marketplace/flows.md",
        "learning/marketplace/api.md",
    ],
    "API reference": [
        "reference.md",
    ],
    "Project": [
        "roadmap.md",
        "migration.md",
        "contributing.md",
        "changelog.md",
    ],
}

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

    index = f"# {site_name}\n\n> {_DESCRIPTION}\n"
    full = f"# {site_name}\n\n> {_DESCRIPTION}\n"

    for section, files in _SECTIONS.items():
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
