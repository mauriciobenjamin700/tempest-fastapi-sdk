"""Typed builders for the open HTML attribute maps used on SSR widgets.

Every ``tempest_core`` widget takes ``attrs: dict[str, str]`` — the escape
hatch to raw HTML attributes (``hx-*``, ``aria-*``, ``data-*``, …). The
attribute space is open, so the base type stays a string map; these helpers
add **typed, discoverable call sites** on top of it without hiding anything.

Each returns a plain ``dict[str, str]`` you spread into ``attrs=``:

```python
from tempest_core import Button
from tempest_fastapi_sdk.ssr import htmx

Button(
    label="Save",
    attrs=htmx(post="/tasks", target="#tasks", swap="beforeend"),
)
# attrs == {"hx-post": "/tasks", "hx-target": "#tasks", "hx-swap": "beforeend"}
```

No magic: the return value is exactly the dict you would have written by
hand, so what you get is obvious, mergeable and inspectable
(``{**htmx(...), "id": "row-1"}``).
"""

from __future__ import annotations

import json as _json
from typing import Any


def _bool(value: bool) -> str:
    """Render a boolean as the HTML string ``"true"`` / ``"false"``."""
    return "true" if value else "false"


def _json_or_str(value: str | dict[str, Any]) -> str:
    """Return a raw string as-is, or JSON-encode a mapping."""
    return value if isinstance(value, str) else _json.dumps(value)


def htmx(
    *,
    get: str | None = None,
    post: str | None = None,
    put: str | None = None,
    patch: str | None = None,
    delete: str | None = None,
    target: str | None = None,
    swap: str | None = None,
    trigger: str | None = None,
    select: str | None = None,
    include: str | None = None,
    indicator: str | None = None,
    confirm: str | None = None,
    prompt: str | None = None,
    push_url: bool | str | None = None,
    swap_oob: bool | str | None = None,
    boost: bool | None = None,
    disable: bool | None = None,
    encoding: str | None = None,
    ext: str | None = None,
    sync: str | None = None,
    params: str | None = None,
    vals: str | dict[str, Any] | None = None,
    headers: str | dict[str, Any] | None = None,
    on: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the ``hx-*`` attribute map for an HTMX-driven widget.

    Only the arguments you pass appear in the result. Exactly one of
    ``get``/``post``/``put``/``patch``/``delete`` is the usual choice (the
    request the element fires). ``vals`` / ``headers`` accept a mapping and
    are JSON-encoded for you; ``on`` maps an event spec to inline JS and
    becomes ``hx-on:<event>`` (e.g. ``on={":after-request": "this.reset()"}``
    → ``hx-on::after-request``).

    Args:
        get (str | None): URL for ``hx-get``.
        post (str | None): URL for ``hx-post``.
        put (str | None): URL for ``hx-put``.
        patch (str | None): URL for ``hx-patch``.
        delete (str | None): URL for ``hx-delete``.
        target (str | None): CSS selector for ``hx-target``.
        swap (str | None): Swap strategy (``outerHTML``, ``beforeend``, …).
        trigger (str | None): Trigger spec for ``hx-trigger``.
        select (str | None): Response subset selector for ``hx-select``.
        include (str | None): Extra elements to include (``hx-include``).
        indicator (str | None): Loading-indicator selector (``hx-indicator``).
        confirm (str | None): ``confirm()`` message (``hx-confirm``).
        prompt (str | None): ``prompt()`` message (``hx-prompt``).
        push_url (bool | str | None): ``hx-push-url`` (``True`` or a URL).
        swap_oob (bool | str | None): ``hx-swap-oob`` (``True`` or a strategy).
        boost (bool | None): ``hx-boost``.
        disable (bool | None): ``hx-disable``.
        encoding (str | None): ``hx-encoding`` (e.g. ``multipart/form-data``).
        ext (str | None): Extensions for ``hx-ext``.
        sync (str | None): ``hx-sync`` spec.
        params (str | None): ``hx-params`` spec.
        vals (str | dict[str, Any] | None): ``hx-vals`` — a mapping is
            JSON-encoded.
        headers (str | dict[str, Any] | None): ``hx-headers`` — a mapping is
            JSON-encoded.
        on (dict[str, str] | None): Event spec → inline JS, each emitted as
            ``hx-on:<event>``.

    Returns:
        dict[str, str]: The ``hx-*`` attributes, ready to spread into
        ``attrs=``.
    """
    attrs: dict[str, str] = {}
    if get is not None:
        attrs["hx-get"] = get
    if post is not None:
        attrs["hx-post"] = post
    if put is not None:
        attrs["hx-put"] = put
    if patch is not None:
        attrs["hx-patch"] = patch
    if delete is not None:
        attrs["hx-delete"] = delete
    if target is not None:
        attrs["hx-target"] = target
    if swap is not None:
        attrs["hx-swap"] = swap
    if trigger is not None:
        attrs["hx-trigger"] = trigger
    if select is not None:
        attrs["hx-select"] = select
    if include is not None:
        attrs["hx-include"] = include
    if indicator is not None:
        attrs["hx-indicator"] = indicator
    if confirm is not None:
        attrs["hx-confirm"] = confirm
    if prompt is not None:
        attrs["hx-prompt"] = prompt
    if push_url is not None:
        attrs["hx-push-url"] = (
            _bool(push_url) if isinstance(push_url, bool) else push_url
        )
    if swap_oob is not None:
        attrs["hx-swap-oob"] = (
            _bool(swap_oob) if isinstance(swap_oob, bool) else swap_oob
        )
    if boost is not None:
        attrs["hx-boost"] = _bool(boost)
    if disable is not None:
        attrs["hx-disable"] = _bool(disable)
    if encoding is not None:
        attrs["hx-encoding"] = encoding
    if ext is not None:
        attrs["hx-ext"] = ext
    if sync is not None:
        attrs["hx-sync"] = sync
    if params is not None:
        attrs["hx-params"] = params
    if vals is not None:
        attrs["hx-vals"] = _json_or_str(vals)
    if headers is not None:
        attrs["hx-headers"] = _json_or_str(headers)
    for event, script in (on or {}).items():
        attrs[f"hx-on:{event}"] = script
    return attrs


def aria(
    *,
    label: str | None = None,
    labelledby: str | None = None,
    describedby: str | None = None,
    hidden: bool | None = None,
    expanded: bool | None = None,
    selected: bool | None = None,
    checked: bool | None = None,
    disabled: bool | None = None,
    pressed: bool | None = None,
    busy: bool | None = None,
    live: str | None = None,
    current: str | None = None,
    controls: str | None = None,
    haspopup: str | None = None,
    role: str | None = None,
) -> dict[str, str]:
    """Build ARIA / role attributes for an accessible widget.

    Only the arguments you pass appear in the result. ``role`` is emitted as
    the bare ``role`` attribute; every other argument becomes ``aria-<name>``.
    Booleans render as ``"true"`` / ``"false"``.

    Args:
        label (str | None): ``aria-label``.
        labelledby (str | None): ``aria-labelledby``.
        describedby (str | None): ``aria-describedby``.
        hidden (bool | None): ``aria-hidden``.
        expanded (bool | None): ``aria-expanded``.
        selected (bool | None): ``aria-selected``.
        checked (bool | None): ``aria-checked``.
        disabled (bool | None): ``aria-disabled``.
        pressed (bool | None): ``aria-pressed``.
        busy (bool | None): ``aria-busy``.
        live (str | None): ``aria-live`` (``polite`` / ``assertive`` / ``off``).
        current (str | None): ``aria-current`` (``page`` / ``step`` / ``true`` …).
        controls (str | None): ``aria-controls``.
        haspopup (str | None): ``aria-haspopup``.
        role (str | None): The ``role`` attribute (not ``aria-`` prefixed).

    Returns:
        dict[str, str]: The ARIA/role attributes, ready to spread into
        ``attrs=``.
    """
    attrs: dict[str, str] = {}
    if label is not None:
        attrs["aria-label"] = label
    if labelledby is not None:
        attrs["aria-labelledby"] = labelledby
    if describedby is not None:
        attrs["aria-describedby"] = describedby
    if hidden is not None:
        attrs["aria-hidden"] = _bool(hidden)
    if expanded is not None:
        attrs["aria-expanded"] = _bool(expanded)
    if selected is not None:
        attrs["aria-selected"] = _bool(selected)
    if checked is not None:
        attrs["aria-checked"] = _bool(checked)
    if disabled is not None:
        attrs["aria-disabled"] = _bool(disabled)
    if pressed is not None:
        attrs["aria-pressed"] = _bool(pressed)
    if busy is not None:
        attrs["aria-busy"] = _bool(busy)
    if live is not None:
        attrs["aria-live"] = live
    if current is not None:
        attrs["aria-current"] = current
    if controls is not None:
        attrs["aria-controls"] = controls
    if haspopup is not None:
        attrs["aria-haspopup"] = haspopup
    if role is not None:
        attrs["role"] = role
    return attrs


def data(**items: str | int | float | bool) -> dict[str, str]:
    """Build ``data-*`` attributes from keyword arguments.

    Underscores in a key become hyphens (``user_id`` → ``data-user-id``).
    Values are stringified; booleans render as ``"true"`` / ``"false"``.

    Args:
        **items (str | int | float | bool): The data attributes.

    Returns:
        dict[str, str]: The ``data-*`` attributes, ready to spread into
        ``attrs=``.
    """
    attrs: dict[str, str] = {}
    for key, value in items.items():
        name = "data-" + key.replace("_", "-")
        attrs[name] = _bool(value) if isinstance(value, bool) else str(value)
    return attrs


__all__: list[str] = [
    "aria",
    "data",
    "htmx",
]
