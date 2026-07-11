"""Tests for the typed SSR attribute builders (htmx / aria / data)."""

from __future__ import annotations

from tempest_fastapi_sdk.ssr import aria, data, htmx


class TestHtmx:
    def test_emits_only_provided(self) -> None:
        assert htmx(post="/tasks", target="#tasks", swap="beforeend") == {
            "hx-post": "/tasks",
            "hx-target": "#tasks",
            "hx-swap": "beforeend",
        }

    def test_empty_when_nothing_passed(self) -> None:
        assert htmx() == {}

    def test_all_verbs(self) -> None:
        assert htmx(get="/g")["hx-get"] == "/g"
        assert htmx(put="/p")["hx-put"] == "/p"
        assert htmx(patch="/pa")["hx-patch"] == "/pa"
        assert htmx(delete="/d")["hx-delete"] == "/d"

    def test_booleans_render_true_false(self) -> None:
        assert htmx(boost=True)["hx-boost"] == "true"
        assert htmx(disable=False)["hx-disable"] == "false"

    def test_push_url_bool_or_str(self) -> None:
        assert htmx(push_url=True)["hx-push-url"] == "true"
        assert htmx(push_url="/next")["hx-push-url"] == "/next"

    def test_vals_dict_is_json_encoded(self) -> None:
        assert htmx(vals={"a": 1})["hx-vals"] == '{"a": 1}'

    def test_vals_str_passthrough(self) -> None:
        assert htmx(vals='{"raw": true}')["hx-vals"] == '{"raw": true}'

    def test_headers_dict_is_json_encoded(self) -> None:
        assert htmx(headers={"X-CSRF": "abc"})["hx-headers"] == '{"X-CSRF": "abc"}'

    def test_on_events_prefixed(self) -> None:
        result = htmx(on={":after-request": "this.reset()", "click": "go()"})
        assert result["hx-on::after-request"] == "this.reset()"
        assert result["hx-on:click"] == "go()"


class TestAria:
    def test_label_and_role(self) -> None:
        assert aria(label="Close", role="button") == {
            "aria-label": "Close",
            "role": "button",
        }

    def test_booleans(self) -> None:
        assert aria(hidden=True)["aria-hidden"] == "true"
        assert aria(expanded=False)["aria-expanded"] == "false"

    def test_string_valued(self) -> None:
        assert aria(live="polite", current="page") == {
            "aria-live": "polite",
            "aria-current": "page",
        }

    def test_empty(self) -> None:
        assert aria() == {}


class TestData:
    def test_underscores_become_hyphens(self) -> None:
        assert data(user_id="42") == {"data-user-id": "42"}

    def test_stringifies_values(self) -> None:
        assert data(count=3, ratio=1.5) == {"data-count": "3", "data-ratio": "1.5"}

    def test_bool_true_false(self) -> None:
        assert data(active=True, open=False) == {
            "data-active": "true",
            "data-open": "false",
        }

    def test_empty(self) -> None:
        assert data() == {}


def test_helpers_merge_into_attrs() -> None:
    # The idiomatic call site: spread several builders + plain keys.
    attrs = {**htmx(post="/x", target="#t"), **aria(label="Save"), "id": "row-1"}
    assert attrs == {
        "hx-post": "/x",
        "hx-target": "#t",
        "aria-label": "Save",
        "id": "row-1",
    }


def test_builders_produce_valid_widget_attrs() -> None:
    # The output must render on a real widget without surprises.
    from tempest_core import Button
    from tempestweb.html import render_to_html

    html = render_to_html(
        Button(label="Save", attrs={**htmx(post="/save", swap="outerHTML")})
    )
    assert 'hx-post="/save"' in html
    assert 'hx-swap="outerHTML"' in html
