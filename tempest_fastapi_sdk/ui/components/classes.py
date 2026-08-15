"""CSS class names used by the bundled components.

Every component takes its class names from one :class:`ComponentClasses`
instance, so a service can retarget the whole set at its own design
system by passing a different instance — no component needs subclassing
for that.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ComponentClasses:
    """Class names applied by :mod:`tempest_fastapi_sdk.ui.components`.

    The defaults match the rules built by
    :func:`~tempest_fastapi_sdk.ui.components.component_stylesheet`.

    Attributes:
        card (str): The card wrapper.
        card_title (str): The card heading.
        card_body (str): The card content area.
        card_footer (str): The card footer.
        alert (str): The alert wrapper.
        alert_variant (str): Prefix of the per-variant alert modifier;
            ``"tui-alert--"`` yields ``tui-alert--error``.
        table (str): The data table.
        table_empty (str): The row shown when a table has no rows.
        pagination (str): The pagination wrapper.
        pagination_link (str): A page link.
        pagination_current (str): The current-page marker.
        pagination_disabled (str): A disabled previous/next link.
        empty_state (str): The empty-state wrapper.
        empty_state_title (str): The empty-state heading.
        empty_state_text (str): The empty-state description.
        nav (str): The navigation bar.
        nav_link (str): A navigation link.
        nav_active (str): The active navigation link.
        shell (str): The page shell wrapper.
        shell_header (str): The shell header.
        shell_main (str): The shell main region.
        shell_footer (str): The shell footer.
        grid (str): The grid container.
    """

    card: str = "tui-card"
    card_title: str = "tui-card__title"
    card_body: str = "tui-card__body"
    card_footer: str = "tui-card__footer"
    alert: str = "tui-alert"
    alert_variant: str = "tui-alert--"
    table: str = "tui-table"
    table_empty: str = "tui-table__empty"
    pagination: str = "tui-pagination"
    pagination_link: str = "tui-pagination__link"
    pagination_current: str = "tui-pagination__link--current"
    pagination_disabled: str = "tui-pagination__link--disabled"
    empty_state: str = "tui-empty"
    empty_state_title: str = "tui-empty__title"
    empty_state_text: str = "tui-empty__text"
    nav: str = "tui-nav"
    nav_link: str = "tui-nav__link"
    nav_active: str = "tui-nav__link--active"
    shell: str = "tui-shell"
    shell_header: str = "tui-shell__header"
    shell_main: str = "tui-shell__main"
    shell_footer: str = "tui-shell__footer"
    grid: str = "tui-grid"


DEFAULT_CLASSES: ComponentClasses = ComponentClasses()
"""The class set every component falls back to."""


__all__: list[str] = ["DEFAULT_CLASSES", "ComponentClasses"]
