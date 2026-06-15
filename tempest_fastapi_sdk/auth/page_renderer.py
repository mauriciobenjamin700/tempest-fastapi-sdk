"""Standalone HTML page renderer for the backend-only auth flow.

Used by :func:`tempest_fastapi_sdk.make_auth_router` when
``AuthSettings.AUTH_BACKEND_LINKS=True`` to render activation /
password-reset success/error pages directly from the backend —
no SPA round trip required.

Lazy-imports Jinja2 so the SDK keeps working when the
``[email]`` extra is not installed; the import error is raised
only when a backend page is actually requested.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def render_auth_page(
    template_name: str,
    context: dict[str, Any],
    *,
    template_dir: str | Path | None = None,
    locale: str | None = None,
) -> str:
    """Render a Jinja2 HTML page for the backend-only auth flow.

    Resolves templates in this order (first hit wins):

    1. ``template_dir/<locale>/<name>`` — project override, this locale.
    2. ``template_dir/<name>`` — project override, legacy flat layout.
    3. ``<sdk>/auth/templates/<locale>/<name>`` — SDK bundled, this locale.

    When ``locale`` is ``None`` the locale subdirectories are skipped
    (flat layout only). Autoescape is enabled for HTML / XML.

    Args:
        template_name (str): Filename of the template, e.g.
            ``"activation_success.html"``.
        context (dict[str, Any]): Variables exposed to the
            Jinja2 template.
        template_dir (str | Path | None): Optional project
            template directory that overrides the bundled
            templates. Defaults to ``None`` — only the SDK
            bundled templates are used.
        locale (str | None): Canonical locale (e.g. ``"pt-BR"`` /
            ``"en-US"``) selecting the per-locale template
            subdirectory. ``None`` uses the flat layout.

    Returns:
        str: The rendered HTML.

    Raises:
        ImportError: When Jinja2 is not installed
            (``[email]`` extra missing).
        jinja2.TemplateNotFound: When the template cannot be
            located in ``template_dir`` nor in the bundled
            directory.
    """
    try:
        from jinja2 import (
            ChoiceLoader,
            Environment,
            FileSystemLoader,
            select_autoescape,
        )
    except ImportError as exc:
        raise ImportError(
            "Backend-only auth pages require Jinja2. Install with "
            "`pip install tempest-fastapi-sdk[email]`."
        ) from exc

    from tempest_fastapi_sdk.auth.locale import DEFAULT_AUTH_LOCALE

    search_paths: list[Path] = []
    bundled_dir = Path(__file__).resolve().parent / "templates"
    # Bundled pages live under per-locale subdirs; a locale-less call
    # falls back to the default locale subdir.
    bundled_locale = locale or DEFAULT_AUTH_LOCALE
    if template_dir is not None:
        if locale is not None:
            search_paths.append(Path(template_dir) / locale)
        search_paths.append(Path(template_dir))
    if (bundled_dir / bundled_locale).is_dir():
        search_paths.append(bundled_dir / bundled_locale)
    if bundled_dir.is_dir():
        search_paths.append(bundled_dir)
    if not search_paths:
        raise RuntimeError(
            "render_auth_page could not locate any template directory. "
            "Pass ``template_dir`` or ensure the SDK is installed correctly."
        )

    env = Environment(
        loader=ChoiceLoader([FileSystemLoader(str(p)) for p in search_paths]),
        autoescape=select_autoescape(["html", "htm", "xml"]),
        enable_async=False,
    )
    template = env.get_template(template_name)
    rendered: str = template.render(**context)
    return rendered


__all__: list[str] = ["render_auth_page"]
