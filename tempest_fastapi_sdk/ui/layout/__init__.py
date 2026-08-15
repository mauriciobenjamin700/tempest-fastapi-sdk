"""Layout: the structural containers a page arranges its content in.

``tempest_core`` already covers flex layout with ``Column`` / ``Row`` /
``Spacer``, and this package does not duplicate it. What lives here is
what those cannot express: the semantic page frame
(:class:`Shell` — header, main, footer landmarks) and a CSS grid
(:class:`Grid`).

Example:
    ```python
    from tempest_core import Text

    from tempest_fastapi_sdk.ui.layout import Grid, Shell

    shell = Shell(children=[Grid(children=[Text(content="a")], columns=2)])
    ```
"""

from tempest_fastapi_sdk.ui.layout.grid import Grid as Grid
from tempest_fastapi_sdk.ui.layout.shell import Shell as Shell

__all__: list[str] = ["Grid", "Shell"]
