"""RFC 5988 / RFC 8288 ``Link`` header builder for paginated responses."""

from __future__ import annotations

from urllib.parse import urlencode, urlparse, urlunparse


def build_pagination_link_header(
    base_url: str,
    *,
    page: int,
    page_size: int,
    pages: int,
    extra_params: dict[str, str] | None = None,
    page_param: str = "page",
    size_param: str = "page_size",
) -> str:
    """Build a ``Link`` header for offset-based pagination.

    Includes the ``first`` / ``prev`` / ``next`` / ``last`` rel values
    expected by GitHub-style clients. ``prev`` / ``next`` are omitted
    when they don't exist (first / last page).

    Args:
        base_url (str): Absolute or relative URL of the collection
            endpoint (e.g. ``"https://api.example.com/api/users"`` or
            ``"/api/users"``). Existing query parameters are preserved.
        page (int): Current page number (1-based).
        page_size (int): Page size. Field name matches
            :class:`BasePaginationFilterSchema.page_size` and the
            ``BaseRepository.paginate`` keyword argument.
        pages (int): Total number of pages.
        extra_params (dict[str, str] | None): Extra query parameters
            that must be preserved on every link (filters, sort,
            etc.). Existing values on ``base_url`` win.
        page_param (str): Query parameter name for the page index.
            Defaults to ``"page"``.
        size_param (str): Query parameter name for the page size.
            Defaults to ``"page_size"``.

    Returns:
        str: A ``Link`` header value ready to assign to
        ``response.headers["Link"]``. Empty string when there's
        nothing to link to (single page, ``pages <= 0``).
    """
    if pages <= 0:
        return ""

    parsed = urlparse(base_url)
    existing_params: dict[str, str] = {}
    if parsed.query:
        for pair in parsed.query.split("&"):
            if "=" in pair:
                key, value = pair.split("=", 1)
                existing_params[key] = value
            else:
                existing_params[pair] = ""

    base_params: dict[str, str] = {**(extra_params or {}), **existing_params}

    def _url_for(target_page: int) -> str:
        params = {
            **base_params,
            page_param: str(target_page),
            size_param: str(page_size),
        }
        query = urlencode(params, doseq=True)
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                query,
                parsed.fragment,
            ),
        )

    parts: list[str] = []
    if page > 1:
        parts.append(f'<{_url_for(1)}>; rel="first"')
        parts.append(f'<{_url_for(page - 1)}>; rel="prev"')
    if page < pages:
        parts.append(f'<{_url_for(page + 1)}>; rel="next"')
        parts.append(f'<{_url_for(pages)}>; rel="last"')

    return ", ".join(parts)


__all__: list[str] = [
    "build_pagination_link_header",
]
