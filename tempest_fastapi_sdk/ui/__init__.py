"""The UI layer: typed pages, components, forms and CSS — all in Python.

``ui`` is a layer of a service, sitting beside ``controllers``,
``services`` and ``schemas`` rather than inside them. It answers one
question — *what does this look like* — and never answers any other: it
does not open a database session, call an external API, or decide
business rules. It receives data that a controller already loaded and
returns a widget tree.

The layer, and the package, have the same five parts:

| Sub-package | Holds | Service counterpart |
| --- | --- | --- |
| ``ui.pages`` | one class per screen | ``src/ui/pages/`` |
| ``ui.components`` | reusable pieces | ``src/ui/components/`` |
| ``ui.layout`` | structural containers | ``src/ui/layout/`` |
| ``ui.forms`` | forms from Pydantic schemas | ``src/ui/forms/`` |
| ``ui.css`` | typed stylesheets and tokens | ``src/ui/styles/`` |

Rendering to HTML and the HTTP layer stay in
:mod:`tempest_fastapi_sdk.ssr`: build the tree here, return
:func:`~tempest_fastapi_sdk.ssr.html_response` from the route.

Example:
    ```python
    from fastapi import FastAPI
    from fastapi.responses import Response
    from tempest_core import Text, Widget

    from tempest_fastapi_sdk.ssr import html_response
    from tempest_fastapi_sdk.ui import app_stylesheet
    from tempest_fastapi_sdk.ui.components import Card, NavBar, NavItem
    from tempest_fastapi_sdk.ui.css import make_css_router
    from tempest_fastapi_sdk.ui.layout import Shell
    from tempest_fastapi_sdk.ui.pages import Page

    app: FastAPI = FastAPI()
    app.include_router(make_css_router(app_stylesheet()))


    class BasePage(Page):
        def shell(self, body: Widget) -> Widget:
            return Shell(
                children=[body],
                header=NavBar(items=[NavItem(label="Início", href="/")]),
            )


    class HomePage(BasePage):
        total: int

        def body(self) -> Widget:
            return Card(
                title="Vendas",
                children=[Text(content=f"{self.total} pedidos hoje")],
            )


    @app.get("/")
    async def home() -> Response:
        return html_response(
            HomePage(title="Início", total=12),
            title="Início",
            stylesheets=["/static/app.css"],
        )
    ```

The rendering backend (``tempestweb`` / ``tempest_core``) is imported
lazily, so importing this package never hard-requires the optional
``[ssr]`` extra; the dependency is touched when a widget is built.
"""

from tempest_fastapi_sdk.ui.stylesheet import app_stylesheet as app_stylesheet

__all__: list[str] = ["app_stylesheet"]
