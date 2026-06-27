"""Typed theming for the admin panel.

The bundled stylesheet (``static/admin.css``) is driven entirely by CSS
custom properties declared on ``:root``. :class:`AdminTheme` lets a
project override those properties — plus the logo, favicon, font and
footer — through **typed, documented parameters** instead of forking the
stylesheet. The values are injected as a ``<style>`` block in the page
``<head>`` (after ``admin.css``, so they win), which means:

* no CSS file to maintain on the project side,
* every knob is type-checked and discoverable in the IDE,
* the default look is unchanged when no theme is passed.

Example::

    from tempest_fastapi_sdk.admin import AdminSite, AdminTheme

    site = AdminSite(
        title="Servus Admin",
        theme=AdminTheme(
            accent="#7c3aed",
            header_bg="#1e1b4b",
            logo_url="/static/logo.svg",
            font_family="'Inter', sans-serif",
            radius="10px",
            footer_text="Servus | 2026",
        ),
    )

For anything the parameters do not cover, point :attr:`custom_css_url`
at your own stylesheet — it is linked last, so it overrides everything,
including this theme.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

# Characters that would let a theme value break out of the injected
# ``<style>`` block or an HTML attribute. Theme values are developer-set
# (not end-user input), but rejecting these keeps a careless value from
# silently producing broken — or injectable — markup.
_FORBIDDEN_CHARS: tuple[str, ...] = ("<", ">", "{", "}", '"')


@dataclass(frozen=True)
class AdminTheme:
    """Typed appearance overrides for the admin panel.

    Every field maps to a CSS custom property on ``:root`` (or to a
    piece of chrome like the logo / footer). All fields have sensible
    defaults that reproduce the stock look, so ``AdminTheme()`` is a
    no-op and you only set what you want to change.

    Attributes:
        accent (str): Primary accent color — links, primary buttons,
            active sidebar item. Any CSS color (``"#7c3aed"``,
            ``"rebeccapurple"``, ``"rgb(124 58 237)"``).
        accent_hover (str): Hover/active shade of :attr:`accent`.
        danger (str): Color for destructive actions and error messages.
        header_bg (str): Background of the top header band.
        sidebar_bg (str | None): Background of the left sidebar. Falls
            back to :attr:`header_bg` when ``None`` so the chrome reads
            as one surface.
        page_bg (str | None): Main content background. ``None`` uses the
            mode default (light grey, or near-black when
            :attr:`dark_mode` is on).
        radius (str): Border-radius applied to buttons, inputs, cards
            and tables (``"6px"``, ``"0.75rem"``, ``"0"`` for square).
        font_family (str | None): CSS ``font-family`` for the whole
            panel. ``None`` keeps the system font stack.
        logo_url (str | None): URL of an image shown in the header
            instead of the brand text. ``None`` shows the text brand.
        logo_alt (str): ``alt`` text for the logo image.
        favicon_url (str | None): URL of the browser-tab favicon.
            ``None`` lets the browser use its default.
        footer_text (str): Text shown in the page footer.
        dark_mode (bool): When ``True``, the content surfaces (page
            background, text, table rows, inputs, borders) switch to a
            dark palette. The header/sidebar are already dark, so they
            are unaffected; :attr:`accent` and the other colors still
            apply.
        custom_css_url (str | None): URL of an extra stylesheet linked
            **after** the theme, so it overrides everything. The escape
            hatch for anything the typed fields do not cover.
    """

    accent: str = "#2563eb"
    accent_hover: str = "#1d4ed8"
    danger: str = "#b91c1c"
    header_bg: str = "#0f172a"
    sidebar_bg: str | None = None
    page_bg: str | None = None
    radius: str = "6px"
    font_family: str | None = None
    logo_url: str | None = None
    logo_alt: str = "Logo"
    favicon_url: str | None = None
    footer_text: str = "Powered by tempest-fastapi-sdk"
    dark_mode: bool = False
    custom_css_url: str | None = None

    def __post_init__(self) -> None:
        """Validate that no string field can break out of the markup.

        Raises:
            ValueError: When any string field contains a character from
                :data:`_FORBIDDEN_CHARS` (``< > { } "``), which would
                corrupt the injected ``<style>`` block or an HTML
                attribute.
        """
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, str):
                bad = [c for c in _FORBIDDEN_CHARS if c in value]
                if bad:
                    raise ValueError(
                        f"AdminTheme.{field.name} contains forbidden "
                        f"character(s) {bad!r}; these would break the "
                        f"injected <style>/HTML. Got: {value!r}"
                    )

    def css_variables(self) -> dict[str, str]:
        """Return the ``:root`` custom properties this theme sets.

        Returns:
            dict[str, str]: Mapping of CSS variable name to value. Only
            the always-present variables are included here; mode- and
            font-dependent rules are emitted by :meth:`to_css`.
        """
        variables: dict[str, str] = {
            "--tempest-accent": self.accent,
            "--tempest-accent-hover": self.accent_hover,
            "--tempest-danger": self.danger,
            "--tempest-bg": self.header_bg,
            "--tempest-bg-soft": self.sidebar_bg or self.header_bg,
            "--tempest-radius": self.radius,
        }
        if self.font_family is not None:
            variables["--tempest-font"] = self.font_family
        if self.page_bg is not None:
            variables["--tempest-page-bg"] = self.page_bg
        return variables

    def to_css(self) -> str:
        """Render the full ``<style>`` body for this theme.

        Combines the :meth:`css_variables` ``:root`` block with the
        optional dark-mode surface overrides. The result is injected
        verbatim inside a ``<style>`` element in the page ``<head>``.

        Returns:
            str: CSS text. Empty-safe — a default ``AdminTheme()`` still
            returns a valid (no-op-equivalent) block.
        """
        root_lines = "\n".join(
            f"  {name}: {value};" for name, value in self.css_variables().items()
        )
        blocks: list[str] = [f":root {{\n{root_lines}\n}}"]

        if self.dark_mode and self.page_bg is None:
            # Dark surfaces for the content area. The header/sidebar are
            # already dark via --tempest-bg. Skipped when the project
            # pinned its own page_bg (explicit value wins).
            blocks.append(
                ":root {\n"
                "  --tempest-page-bg: #0b1120;\n"
                "  --tempest-bg-row: #1e293b;\n"
                "  --tempest-bg-row-alt: #172033;\n"
                "  --tempest-fg: #e2e8f0;\n"
                "  --tempest-fg-soft: #94a3b8;\n"
                "}\n"
                "body { color: var(--tempest-fg); }\n"
                "input, select, textarea {\n"
                "  background: #1e293b;\n"
                "  color: var(--tempest-fg);\n"
                "  border-color: #334155;\n"
                "}"
            )

        if self.font_family is not None:
            blocks.append("body { font-family: var(--tempest-font); }")

        return "\n".join(blocks)
