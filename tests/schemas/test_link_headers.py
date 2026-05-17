"""Tests for build_pagination_link_header."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from tempest_fastapi_sdk import build_pagination_link_header


def _parse_links(value: str) -> dict[str, dict[str, list[str]]]:
    """Parse the Link header into ``{rel: {param: [values]}}``."""
    result: dict[str, dict[str, list[str]]] = {}
    for chunk in value.split(", "):
        url_part, rel_part = chunk.split("; ", 1)
        rel = rel_part.split("=")[1].strip('"')
        url = url_part.strip("<>")
        result[rel] = parse_qs(urlparse(url).query)
    return result


class TestBuildPaginationLinkHeader:
    """Validate the RFC 8288 Link header builder."""

    def test_empty_when_no_pages(self) -> None:
        assert (
            build_pagination_link_header("/api/users", page=1, size=20, pages=0)
            == ""
        )

    def test_first_page_only_has_next_and_last(self) -> None:
        value = build_pagination_link_header(
            "/api/users",
            page=1,
            size=20,
            pages=5,
        )
        links = _parse_links(value)
        assert set(links) == {"next", "last"}
        assert links["next"]["page"] == ["2"]
        assert links["last"]["page"] == ["5"]

    def test_middle_page_has_all_four_rels(self) -> None:
        value = build_pagination_link_header(
            "/api/users",
            page=3,
            size=20,
            pages=5,
        )
        links = _parse_links(value)
        assert set(links) == {"first", "prev", "next", "last"}
        assert links["first"]["page"] == ["1"]
        assert links["prev"]["page"] == ["2"]
        assert links["next"]["page"] == ["4"]
        assert links["last"]["page"] == ["5"]

    def test_last_page_only_has_first_and_prev(self) -> None:
        value = build_pagination_link_header(
            "/api/users",
            page=5,
            size=20,
            pages=5,
        )
        links = _parse_links(value)
        assert set(links) == {"first", "prev"}
        assert links["prev"]["page"] == ["4"]

    def test_preserves_existing_query_params(self) -> None:
        value = build_pagination_link_header(
            "/api/users?name=ana&active=true",
            page=2,
            size=20,
            pages=5,
        )
        links = _parse_links(value)
        assert links["next"]["name"] == ["ana"]
        assert links["next"]["active"] == ["true"]
        assert links["next"]["page"] == ["3"]

    def test_extra_params_are_added(self) -> None:
        value = build_pagination_link_header(
            "/api/users",
            page=1,
            size=20,
            pages=3,
            extra_params={"sort": "name"},
        )
        links = _parse_links(value)
        assert links["next"]["sort"] == ["name"]
        assert links["last"]["sort"] == ["name"]

    def test_custom_param_names(self) -> None:
        value = build_pagination_link_header(
            "/api/users",
            page=1,
            size=20,
            pages=3,
            page_param="offset",
            size_param="limit",
        )
        links = _parse_links(value)
        assert links["next"]["offset"] == ["2"]
        assert links["next"]["limit"] == ["20"]

    def test_absolute_url_preserves_scheme_and_host(self) -> None:
        value = build_pagination_link_header(
            "https://api.example.com/api/users?name=ana",
            page=2,
            size=20,
            pages=5,
        )
        next_url = value.split(", ")[2].split(";")[0].strip("<>")
        parsed = urlparse(next_url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "api.example.com"
        assert parsed.path == "/api/users"
