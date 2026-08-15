"""Components: the reusable pieces pages are built from.

Each one is a typed ``tempest_core`` ``Component`` that renders plain,
accessible HTML through class names rather than inline styles — so the
whole look lives in one :class:`~tempest_fastapi_sdk.ui.css.StyleSheet`
and a service can retarget it by passing its own
:class:`ComponentClasses`.

:func:`component_stylesheet` builds the matching rules from design
tokens, so the bundled look works out of the box in light and dark.

Example:
    ```python
    from tempest_fastapi_sdk.ui.components import (
        Alert,
        Card,
        DataTable,
        EmptyState,
        NavBar,
        NavItem,
        Pagination,
        component_stylesheet,
    )

    nav = NavBar(items=[NavItem(label="Início", href="/")], active_href="/")
    card = Card(title="Resumo", children=[Alert(message="Tudo certo.")])
    table = DataTable(rows=[{"nome": "Ana"}])
    pages = Pagination(page=1, pages=3, url="/users")
    empty = EmptyState(title="Nada por aqui")
    sheet = component_stylesheet()
    ```
"""

from tempest_fastapi_sdk.ui.components.alert import Alert as Alert
from tempest_fastapi_sdk.ui.components.alert import AlertVariant as AlertVariant
from tempest_fastapi_sdk.ui.components.card import Card as Card
from tempest_fastapi_sdk.ui.components.classes import (
    DEFAULT_CLASSES as DEFAULT_CLASSES,
)
from tempest_fastapi_sdk.ui.components.classes import (
    ComponentClasses as ComponentClasses,
)
from tempest_fastapi_sdk.ui.components.empty import EmptyState as EmptyState
from tempest_fastapi_sdk.ui.components.nav import NavBar as NavBar
from tempest_fastapi_sdk.ui.components.nav import NavItem as NavItem
from tempest_fastapi_sdk.ui.components.pagination import Pagination as Pagination
from tempest_fastapi_sdk.ui.components.pagination import (
    pagination_for as pagination_for,
)
from tempest_fastapi_sdk.ui.components.styles import (
    component_stylesheet as component_stylesheet,
)
from tempest_fastapi_sdk.ui.components.table import DataTable as DataTable

__all__: list[str] = [
    "DEFAULT_CLASSES",
    "Alert",
    "AlertVariant",
    "Card",
    "ComponentClasses",
    "DataTable",
    "EmptyState",
    "NavBar",
    "NavItem",
    "Pagination",
    "component_stylesheet",
    "pagination_for",
]
