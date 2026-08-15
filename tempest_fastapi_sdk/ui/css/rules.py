"""Typed stylesheets: CSS written in Python, checked by the type checker.

A :class:`StyleSheet` is an ordered list of :class:`Rule` and
:class:`Media` objects plus an optional
:class:`~tempest_fastapi_sdk.ui.css.ThemeTokens` block. Rendering it with
:meth:`StyleSheet.to_css` produces a real stylesheet, served by
:func:`~tempest_fastapi_sdk.ui.css.make_css_router` — which is what
separates this from ``tempest_core``'s inline ``Style``: selectors,
pseudo-classes and media queries only exist in a sheet, never inline.

Declarations come from two places, and the split is deliberate:

* ``style`` — a ``tempest_core`` ``Style``, converted by the very same
  ``style_to_css`` the client renderer uses, so a rule and an inline
  style for the same values emit identical declarations.
* ``declarations`` — a plain mapping for everything ``Style`` cannot
  express: token references (``var(--t-color-primary)``), ``display:
  grid``, ``cursor``, ``content``, vendor properties. ``Style`` rejects
  non-hex colours, so every token reference goes here.

Both may be given; ``declarations`` is emitted last and therefore wins on
conflicting properties.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

Layout = Literal["column", "row"]
"""Flex layout applied by widget type, mirroring ``Column`` / ``Row``."""

_LAYOUT_WIDGET: dict[str, str] = {"column": "Column", "row": "Row"}

_CLASS_PATTERN = re.compile(r"\.(-?[_a-zA-Z][\w-]*)")

_RESET = """*, *::before, *::after {
  box-sizing: border-box;
}
body {
  margin: 0;
}
img, svg, video {
  display: block;
  max-width: 100%;
}
button, input, select, textarea {
  font: inherit;
  color: inherit;
}"""
"""Minimal base reset. Opt out with ``StyleSheet(reset=False)``."""


def _style_declarations(style: Any, layout: Layout | None) -> str:
    """Convert a ``Style`` into a CSS declaration string.

    Args:
        style (Any): A ``tempest_core`` ``Style`` instance.
        layout (Layout | None): When set, the rule is treated as a flex
            container of that direction, exactly as ``Column`` / ``Row``
            are by the renderer (so ``gap`` / ``justify`` / ``align`` are
            not silently inert).

    Returns:
        str: Declarations joined by ``"; "``, or an empty string when the
        style holds no set field.

    Raises:
        ImportError: When the optional ``[ssr]`` extra is missing.
    """
    try:
        from tempestweb.html import style_to_css
    except ImportError as exc:  # pragma: no cover - only without [ssr]
        raise ImportError(
            "tempest_fastapi_sdk.ui.css requires the optional [ssr] extra. "
            "Install with: pip install tempest-fastapi-sdk[ssr]",
        ) from exc
    widget_type = _LAYOUT_WIDGET[layout] if layout is not None else None
    rendered: str = style_to_css(style.model_dump(), widget_type)
    return rendered


def cls(*names: str) -> dict[str, str]:
    """Build the ``class`` attribute mapping for a widget.

    Widgets take an open ``attrs`` mapping; this turns a list of class
    names into exactly the ``{"class": "..."}`` entry you would have
    written, with the names joined and blanks dropped.

    Args:
        *names (str): Class names to apply, without the leading dot.

    Returns:
        dict[str, str]: ``{"class": "card card--wide"}``. Empty when no
        non-blank name is given, so it is safe to splat unconditionally.

    Example:
        ```python
        from tempest_core import Column, Text

        from tempest_fastapi_sdk.ui.css import cls

        Column(tag="section", attrs=cls("card"), children=[Text(content="hi")])
        ```
    """
    kept = [name.strip() for name in names if name and name.strip()]
    if not kept:
        return {}
    return {"class": " ".join(kept)}


@dataclass(frozen=True, slots=True)
class Rule:
    """One CSS rule: a selector and the declarations it applies.

    Attributes:
        selector (str): Any CSS selector — ``".card"``,
            ``".card:hover"``, ``"form .field > label"``.
        style (Any | None): A ``tempest_core`` ``Style`` whose set fields
            become declarations.
        layout (Layout | None): Treat the rule as a flex container of
            that direction, adding ``display: flex`` and
            ``flex-direction`` the way ``Column`` / ``Row`` do.
        declarations (Mapping[str, str]): Raw CSS declarations, emitted
            after the style ones and therefore winning on conflicts.
    """

    selector: str
    style: Any | None = None
    layout: Layout | None = None
    declarations: Mapping[str, str] = field(default_factory=dict)

    def to_css(self, *, indent: str = "") -> str:
        """Render the rule as CSS.

        Args:
            indent (str): Indentation prepended to every line, used when
                the rule sits inside an at-rule block.

        Returns:
            str: The rule as ``selector { ... }``, or an empty string
            when it carries no declaration at all.
        """
        parts: list[str] = []
        if self.style is not None:
            rendered = _style_declarations(self.style, self.layout)
            parts.extend(item for item in rendered.split("; ") if item)
        elif self.layout is not None:
            parts.append("display: flex")
            parts.append(f"flex-direction: {self.layout}")
        parts.extend(f"{prop}: {value}" for prop, value in self.declarations.items())

        if not parts:
            return ""
        body = "".join(f"{indent}  {item};\n" for item in parts)
        return f"{indent}{self.selector} {{\n{body}{indent}}}"

    def class_names(self) -> set[str]:
        """Return the class names mentioned in the selector.

        Returns:
            set[str]: Every ``.name`` found in the selector, without the
            dot. Empty for element- or attribute-only selectors.
        """
        return set(_CLASS_PATTERN.findall(self.selector))


@dataclass(frozen=True, slots=True)
class Media:
    """A group of rules wrapped in an at-rule (usually a media query).

    Attributes:
        query (str): The at-rule condition, without ``@media`` —
            ``"(min-width: 768px)"``.
        rules (Sequence[Rule]): The rules the condition guards.
    """

    query: str
    rules: Sequence[Rule] = ()

    @classmethod
    def min_width(cls, pixels: float, rules: Sequence[Rule]) -> Media:
        """Build a mobile-first ``min-width`` query.

        Args:
            pixels (float): The lower bound, in CSS pixels.
            rules (Sequence[Rule]): Rules applied at or above the bound.

        Returns:
            Media: The wrapped rule group.
        """
        return cls(query=f"(min-width: {_px(pixels)})", rules=rules)

    @classmethod
    def max_width(cls, pixels: float, rules: Sequence[Rule]) -> Media:
        """Build a ``max-width`` query.

        Args:
            pixels (float): The upper bound, in CSS pixels.
            rules (Sequence[Rule]): Rules applied at or below the bound.

        Returns:
            Media: The wrapped rule group.
        """
        return cls(query=f"(max-width: {_px(pixels)})", rules=rules)

    @classmethod
    def dark(cls, rules: Sequence[Rule]) -> Media:
        """Build a ``prefers-color-scheme: dark`` query.

        Args:
            rules (Sequence[Rule]): Rules applied in dark mode.

        Returns:
            Media: The wrapped rule group.
        """
        return cls(query="(prefers-color-scheme: dark)", rules=rules)

    @classmethod
    def reduced_motion(cls, rules: Sequence[Rule]) -> Media:
        """Build a ``prefers-reduced-motion: reduce`` query.

        Args:
            rules (Sequence[Rule]): Rules applied when the reader asked
                for less motion.

        Returns:
            Media: The wrapped rule group.
        """
        return cls(query="(prefers-reduced-motion: reduce)", rules=rules)

    def to_css(self) -> str:
        """Render the at-rule and everything it guards.

        Returns:
            str: ``@media (…) { … }``, or an empty string when no nested
            rule produced a declaration.
        """
        inner = "\n".join(
            rendered for rule in self.rules if (rendered := rule.to_css(indent="  "))
        )
        if not inner:
            return ""
        return f"@media {self.query} {{\n{inner}\n}}"

    def class_names(self) -> set[str]:
        """Return the class names mentioned by the nested rules.

        Returns:
            set[str]: The union of every nested rule's class names.
        """
        names: set[str] = set()
        for rule in self.rules:
            names |= rule.class_names()
        return names


def _px(value: float) -> str:
    """Format a pixel length, dropping a trailing ``.0``.

    Args:
        value (float): The length in CSS pixels.

    Returns:
        str: ``"768px"`` rather than ``"768.0px"``.
    """
    as_int = int(value)
    return f"{as_int}px" if as_int == value else f"{value}px"


@dataclass(frozen=True, slots=True)
class StyleSheet:
    """A whole stylesheet, written in typed Python.

    Attributes:
        rules (Sequence[Rule | Media]): The rules, in cascade order.
        theme (ThemeTokens | None): Design tokens emitted as custom
            properties before every rule. ``None`` emits no token block.
        reset (bool): Whether to prepend the minimal base reset
            (``box-sizing``, zeroed body margin, block media, inherited
            control fonts).
        extra_css (str): Raw CSS appended at the end, for the rare thing
            no rule expresses (``@font-face``, keyframes).
    """

    rules: Sequence[Rule | Media] = ()
    theme: Any | None = None
    reset: bool = True
    extra_css: str = ""

    def to_css(self) -> str:
        """Render the whole sheet.

        Returns:
            str: Reset (when enabled), token block (when a theme is set),
            every rule in order, then ``extra_css``. Blocks are separated
            by a blank line; empty blocks are dropped.
        """
        blocks: list[str] = []
        if self.reset:
            blocks.append(_RESET)
        if self.theme is not None:
            blocks.append(self.theme.to_css())
        for rule in self.rules:
            rendered = rule.to_css()
            if rendered:
                blocks.append(rendered)
        if self.extra_css.strip():
            blocks.append(self.extra_css.strip())
        return "\n\n".join(blocks) + "\n" if blocks else ""

    def class_names(self) -> frozenset[str]:
        """Return every class name the sheet defines.

        Returns:
            frozenset[str]: The union of the class names of all rules,
            used by :meth:`cls` to reject typos.
        """
        names: set[str] = set()
        for rule in self.rules:
            names |= rule.class_names()
        return frozenset(names)

    def cls(self, *names: str) -> dict[str, str]:
        """Build a ``class`` attribute, rejecting names the sheet lacks.

        Same output as the module-level :func:`cls`, with a lookup
        against the sheet so a typo fails loudly at render time instead
        of silently producing an unstyled element.

        Args:
            *names (str): Class names to apply, without the leading dot.

        Returns:
            dict[str, str]: ``{"class": "card card--wide"}``.

        Raises:
            KeyError: When a name is not defined by any rule in the
                sheet.
        """
        known = self.class_names()
        for name in names:
            if name and name.strip() and name.strip() not in known:
                raise KeyError(
                    f"Class {name!r} is not defined by this stylesheet. "
                    f"Defined: {', '.join(sorted(known)) or '(none)'}.",
                )
        return cls(*names)

    def merge(self, other: StyleSheet) -> StyleSheet:
        """Concatenate two sheets into one.

        The receiver's rules come first, so ``other`` wins on equal
        specificity — the normal cascade. The receiver's theme and reset
        settings are kept unless it has none.

        Args:
            other (StyleSheet): The sheet appended after this one.

        Returns:
            StyleSheet: A new sheet holding both rule lists.
        """
        extra = "\n\n".join(part for part in (self.extra_css, other.extra_css) if part)
        return StyleSheet(
            rules=[*self.rules, *other.rules],
            theme=self.theme if self.theme is not None else other.theme,
            reset=self.reset or other.reset,
            extra_css=extra,
        )

    def etag(self) -> str:
        """Return a strong ETag for the rendered sheet.

        Returns:
            str: A quoted SHA-256 prefix of the CSS, stable for identical
            content so a conditional request can answer ``304``.
        """
        digest = hashlib.sha256(self.to_css().encode("utf-8")).hexdigest()
        return f'"{digest[:32]}"'


__all__: list[str] = [
    "Layout",
    "Media",
    "Rule",
    "StyleSheet",
    "cls",
]
