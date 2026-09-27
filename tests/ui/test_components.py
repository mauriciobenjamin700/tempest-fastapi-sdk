"""Tests for the bundled UI components."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

import pytest
from pydantic import BaseModel, Field
from tempest_core import Text, Widget
from tempest_core.widgets import Stack
from tempestweb.html import render_to_html

from tempest_fastapi_sdk.schemas import BasePaginationSchema
from tempest_fastapi_sdk.ui.components import (
    Alert,
    Card,
    ComponentClasses,
    DataTable,
    EmptyState,
    NavBar,
    NavItem,
    Pagination,
    TableColumn,
    component_stylesheet,
    pagination_for,
)


class Status(StrEnum):
    """Status rendered in a table cell."""

    OPEN = "open"


class UserSchema(BaseModel):
    """Row schema used by the table tests."""

    name: str = Field(title="Nome")
    active: bool
    joined: date | None = None


def test_card_renders_heading_body_and_footer() -> None:
    html = render_to_html(
        Card(
            title="Vendas",
            children=[Text(content="12")],
            footer=Text(content="hoje", tag="small"),
        ),
    )
    assert html.startswith('<section class="tui-card">')
    assert '<h3 class="tui-card__title">Vendas</h3>' in html
    assert '<div class="tui-card__body"><span>12</span></div>' in html
    assert '<div class="tui-card__footer"><small>hoje</small></div>' in html


def test_card_without_title_or_footer() -> None:
    html = render_to_html(Card(children=[Text(content="x")]))
    assert "tui-card__title" not in html
    assert "tui-card__footer" not in html


def test_card_heading_level_is_configurable() -> None:
    html = render_to_html(Card(title="T", heading_tag="h2"))
    assert "<h2 " in html


def test_alert_variant_drives_class_and_role() -> None:
    info = render_to_html(Alert(message="ok"))
    error = render_to_html(Alert(message="falhou", variant="error", title="Erro"))
    assert 'class="tui-alert tui-alert--info" role="status"' in info
    assert 'class="tui-alert tui-alert--error" role="alert"' in error
    assert "<strong>Erro</strong>" in error


def test_table_derives_columns_and_headers_from_the_schema() -> None:
    html = render_to_html(
        DataTable(
            rows=[UserSchema(name="Ana", active=True, joined=date(2024, 3, 1))],
            row_schema=UserSchema,
        ),
    )
    assert '<th scope="col">Nome</th>' in html
    assert '<th scope="col">Active</th>' in html
    assert "<td>Ana</td>" in html
    assert "<td>Sim</td>" in html
    assert "<td>2024-03-01</td>" in html


def test_table_falls_back_to_the_first_row() -> None:
    html = render_to_html(DataTable(rows=[{"nome": "Ana", "idade": 30}]))
    assert '<th scope="col">Nome</th>' in html
    assert '<th scope="col">Idade</th>' in html


def test_table_empty_row_spans_every_column() -> None:
    html = render_to_html(DataTable(row_schema=UserSchema, empty_text="Nada aqui"))
    assert '<td colspan="3" class="tui-table__empty">Nada aqui</td>' in html


def test_table_column_order_and_header_overrides() -> None:
    html = render_to_html(
        DataTable(
            rows=[UserSchema(name="Ana", active=False)],
            columns=["active", "name"],
            headers={"active": "Ativo?"},
        ),
    )
    assert html.index("Ativo?") < html.index("Name")
    assert "<td>Não</td>" in html


def test_table_cell_formatting() -> None:
    table = DataTable()
    assert table.cell_text(None) == "—"
    assert table.cell_text(True) == "Sim"
    assert table.cell_text(Status.OPEN) == "open"
    assert table.cell_text([1, 2]) == "1, 2"
    assert table.cell_text(UserSchema(name="Ana", active=True)) == "Ana, Sim, —"


def test_table_escapes_cell_content() -> None:
    html = render_to_html(DataTable(rows=[{"x": "<b>hi</b>"}]))
    assert "<b>hi</b>" not in html
    assert "&lt;b&gt;hi&lt;/b&gt;" in html


class FileSchema(BaseModel):
    """Row schema for the custom-cell tests."""

    id: int
    name: str
    size: int


def _file_actions(row: FileSchema) -> Widget:
    """Build a cell holding a link and a form, the panel's per-row actions.

    Args:
        row (FileSchema): The record the cell belongs to.

    Returns:
        Widget: The link followed by a ``POST`` form with one button.
    """
    return [
        Text(content=row.name, tag="a", attrs={"href": f"/files/{row.id}"}),
        Stack(
            tag="form",
            attrs={"method": "post", "action": f"/files/{row.id}/delete"},
            children=[Text(content="Remover", tag="button", attrs={"type": "submit"})],
        ),
    ]


def test_table_cell_renderer_puts_link_and_form_in_one_cell() -> None:
    html = render_to_html(
        DataTable(
            rows=[FileSchema(id=7, name="a.txt", size=120)],
            row_schema=FileSchema,
            columns=[TableColumn("name", render=_file_actions, header="Ações"), "size"],
        ),
    )
    assert (
        "<td>"
        '<a href="/files/7">a.txt</a>'
        '<form method="post" action="/files/7/delete">'
        '<button type="submit">Remover</button>'
        "</form>"
        "</td>"
        "<td>120</td>"
    ) in html
    assert '<th scope="col">Ações</th><th scope="col">Size</th>' in html


def test_table_renderer_widgets_are_escaped_by_the_renderer() -> None:
    """A widget returned by a renderer is rendered, never inserted as HTML."""

    def evil(row: dict[str, str]) -> Widget:
        return Text(content=row["name"], tag="a", attrs={"href": "/x"})

    html = render_to_html(
        DataTable(
            rows=[{"name": "<script>x</script>"}],
            columns=[TableColumn("name", render=evil)],
        ),
    )
    assert "<script>" not in html
    assert '<a href="/x">&lt;script&gt;x&lt;/script&gt;</a>' in html


def test_table_renderer_returning_text_is_escaped() -> None:
    html = render_to_html(
        DataTable(
            rows=[{"name": "ana"}],
            columns=[TableColumn("name", render=lambda row: f"<i>{row['name']}</i>")],
        ),
    )
    assert "<td>&lt;i&gt;ana&lt;/i&gt;</td>" in html


def test_table_renderer_returning_none_uses_none_text() -> None:
    html = render_to_html(
        DataTable(
            rows=[{"name": "ana"}],
            columns=[TableColumn("name", render=lambda row: None)],
        ),
    )
    assert "<td>—</td>" in html


def test_table_column_alignment_and_class_reach_header_and_cells() -> None:
    html = render_to_html(
        DataTable(
            rows=[{"name": "a", "size": 3}],
            columns=["name", TableColumn("size", align="right", class_name="col-opt")],
        ),
    )
    assert '<th scope="col">Name</th>' in html
    assert '<th scope="col" class="tui-table__cell--right col-opt">Size</th>' in html
    assert '<td>a</td><td class="tui-table__cell--right col-opt">3</td>' in html


def test_table_alignment_class_follows_component_classes() -> None:
    html = render_to_html(
        DataTable(
            rows=[{"n": 1}],
            columns=[TableColumn("n", align="center")],
            classes=ComponentClasses(table_align="cell-"),
        ),
    )
    assert '<td class="cell-center">1</td>' in html


def test_table_column_specs_and_names_mix_both_forms() -> None:
    table = DataTable(columns=["a", TableColumn("b", align="left")])
    assert table.resolved_columns() == ["a", "b"]
    assert [spec.align for spec in table.column_specs()] == [None, "left"]


def test_table_column_rejects_an_unknown_alignment() -> None:
    with pytest.raises(ValueError, match="right"):
        DataTable(columns=[TableColumn("a", align="middle")])  # type: ignore[arg-type]


def test_table_sits_inside_a_focusable_scroll_wrapper() -> None:
    html = render_to_html(DataTable(rows=[{"a": 1}]))
    assert html.startswith('<div class="tui-table-scroll" tabindex="0">')
    assert html.endswith("</table></div>")


def test_table_scroll_wrapper_is_a_region_labelled_by_the_caption() -> None:
    html = render_to_html(DataTable(rows=[{"a": 1}], caption="Arquivos"))
    assert html.startswith(
        '<div class="tui-table-scroll" tabindex="0" role="region" '
        'aria-label="Arquivos">'
    )


def test_table_scroll_wrapper_scrolls_horizontally() -> None:
    css = component_stylesheet().to_css()
    rule = css.split(".tui-table-scroll {", 1)[1].split("}", 1)[0]
    assert "overflow-x: auto" in rule
    assert "min-width: 0" in rule


def test_table_scroll_wrapper_follows_custom_classes() -> None:
    html = render_to_html(
        DataTable(rows=[{"a": 1}], classes=ComponentClasses(table_scroll="wrap"))
    )
    assert html.startswith('<div class="wrap"')


def test_pagination_links_carry_the_page_query() -> None:
    html = render_to_html(Pagination(page=2, pages=4, url="/users"))
    assert 'href="/users?page=1"' in html
    assert 'href="/users?page=3"' in html
    assert 'aria-current="page"' in html


def test_pagination_keeps_extra_query_parameters() -> None:
    html = render_to_html(
        Pagination(page=1, pages=3, url="/users", extra_query={"q": "ana"}),
    )
    assert "q=ana&amp;page=2" in html or "q=ana&page=2" in html


def test_pagination_disables_the_ends() -> None:
    first = render_to_html(Pagination(page=1, pages=3, url="/u"))
    last = render_to_html(Pagination(page=3, pages=3, url="/u"))
    assert 'aria-disabled="true"' in first
    assert first.count('aria-disabled="true"') == 1
    assert 'aria-disabled="true"' in last


def test_pagination_window_limits_the_links() -> None:
    control = Pagination(page=5, pages=20, url="/u", window=1)
    assert control.visible_pages() == [4, 5, 6]


def test_single_page_renders_no_links() -> None:
    html = render_to_html(Pagination(page=1, pages=1, url="/u"))
    assert html == '<nav class="tui-pagination" aria-label="Paginação"></nav>'


def test_pagination_for_reads_the_envelope() -> None:
    envelope: BasePaginationSchema[str] = BasePaginationSchema[str](
        items=["a"],
        total=30,
        page=2,
        page_size=10,
        pages=3,
    )
    control = pagination_for(envelope, url="/users", extra_query={"q": "x"})
    assert (control.page, control.pages) == (2, 3)
    assert control.href(3) == "/users?q=x&page=3"


def test_empty_state_renders_title_description_and_action() -> None:
    html = render_to_html(
        EmptyState(
            title="Nenhum pedido",
            description="Eles aparecem aqui.",
            action=Text(content="Criar", tag="a", attrs={"href": "/new"}),
        ),
    )
    assert '<h2 class="tui-empty__title">Nenhum pedido</h2>' in html
    assert '<p class="tui-empty__text">Eles aparecem aqui.</p>' in html
    assert '<a href="/new">Criar</a>' in html


def test_nav_marks_the_active_entry() -> None:
    html = render_to_html(
        NavBar(
            items=[
                NavItem(label="Início", href="/"),
                NavItem(label="Users", href="/u"),
            ],
            active_href="/u",
        ),
    )
    assert '<nav class="tui-nav" aria-label="Navegação principal">' in html
    assert 'class="tui-nav__link tui-nav__link--active" aria-current="page"' in html
    assert html.count("aria-current") == 1


def test_components_accept_custom_class_names() -> None:
    classes = ComponentClasses(card="box", card_body="box__body")
    html = render_to_html(Card(title="T", classes=classes))
    assert 'class="box"' in html
    assert 'class="box__body"' in html


def test_component_stylesheet_covers_every_class_it_targets() -> None:
    sheet = component_stylesheet()
    defined = sheet.class_names()
    for name in (
        "tui-card",
        "tui-alert",
        "tui-table",
        "tui-table-scroll",
        "tui-pagination",
        "tui-nav",
    ):
        assert name in defined


def test_component_stylesheet_follows_custom_classes() -> None:
    sheet = component_stylesheet(classes=ComponentClasses(card="box"))
    assert "box" in sheet.class_names()
    assert ".box {" in sheet.to_css()


def test_component_stylesheet_brings_no_reset_or_tokens() -> None:
    css = component_stylesheet().to_css()
    assert "box-sizing" not in css
    assert ":root" not in css


def test_pagination_for_rejects_a_foreign_object() -> None:
    with pytest.raises(AttributeError):
        pagination_for(object(), url="/u")
