"""Design tokens emitted as CSS custom properties.

The design system itself is **not** reimplemented here: the palette,
spacing scale, shape scale, typography scale and motion scale all come
from ``tempest_core``'s :func:`tempest_core.default_tokens` (a
``TokenSet``), the same source the client renderer uses. This module is
the adapter that turns that token set into CSS custom properties so a
plain stylesheet — and any hand-written CSS a service adds — can reference
the exact same values.

Two blocks are emitted for colours: the ``light`` scheme on ``:root`` and
the ``dark`` scheme under both ``@media (prefers-color-scheme: dark)``
(guarded so an explicit light choice wins) and
``:root[data-theme="dark"]`` (so a toggle wins in both directions).

!!! warning "Colours in `Style` cannot be tokens"
    ``tempest_core``'s ``Style`` validates colours as hex literals —
    ``Style(color="var(--t-color-primary)")`` raises
    ``invalid hex color``, measured against ``tempest_core``'s validator.
    Reference a token from a :class:`~tempest_fastapi_sdk.ui.css.Rule`
    through its ``declarations`` mapping instead:
    ``Rule(".btn", declarations={"color": theme.color("primary")})``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_DEFAULT_PREFIX = "t"


def _default_token_set() -> Any:
    """Return ``tempest_core``'s default token set.

    Returns:
        Any: The ``TokenSet`` instance produced by
        :func:`tempest_core.default_tokens`.

    Raises:
        ImportError: When the optional ``[ssr]`` extra is missing.
    """
    try:
        from tempest_core import default_tokens
    except ImportError as exc:  # pragma: no cover - only without [ssr]
        raise ImportError(
            "tempest_fastapi_sdk.ui.css requires the optional [ssr] extra. "
            "Install with: pip install tempest-fastapi-sdk[ssr]",
        ) from exc
    return default_tokens()


def _rgba(color: dict[str, float]) -> str:
    """Format a token colour as a CSS ``rgba()`` string.

    The spelling matches what ``tempestweb``'s ``style_to_css`` emits for
    inline styles — same separators, and a whole alpha printed as ``1``
    rather than ``1.0`` — so a token value and an inline value for the
    same colour are byte-identical. ``tests/test_ui_css.py`` compares the
    two outputs directly.

    Args:
        color (dict[str, float]): A dumped colour with ``r``, ``g``, ``b``
            and ``a`` keys.

    Returns:
        str: ``rgba(88, 71, 133, 1)``-style CSS colour.
    """
    alpha = color["a"]
    alpha_text = str(int(alpha)) if float(alpha).is_integer() else str(alpha)
    return (
        f"rgba({int(color['r'])}, {int(color['g'])}, {int(color['b'])}, {alpha_text})"
    )


def _css_name(name: str) -> str:
    """Convert a token key to its CSS custom-property spelling.

    Args:
        name (str): Token key as dumped by ``tempest_core``
            (``on_primary``, ``body_medium``).

    Returns:
        str: The same name with underscores turned into hyphens.
    """
    return name.replace("_", "-")


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    """Design tokens rendered as CSS custom properties.

    Attributes:
        token_set (Any): The ``tempest_core`` ``TokenSet`` to emit.
            Defaults to :func:`tempest_core.default_tokens`, resolved
            lazily on first use so importing this module never requires
            the ``[ssr]`` extra.
        prefix (str): Custom-property prefix, without dashes. ``"t"``
            yields ``--t-color-primary``.
        dark_mode (bool): Whether to emit the dark colour scheme blocks.
    """

    token_set: Any = None
    prefix: str = _DEFAULT_PREFIX
    dark_mode: bool = True
    _resolved: list[Any] = field(default_factory=list, repr=False, compare=False)

    def _tokens(self) -> Any:
        """Return the token set, resolving the default on first use.

        Returns:
            Any: The ``TokenSet`` backing this theme.
        """
        if self.token_set is not None:
            return self.token_set
        if not self._resolved:
            self._resolved.append(_default_token_set())
        return self._resolved[0]

    def var(self, group: str, name: str) -> str:
        """Return a ``var(...)`` reference to one token.

        Args:
            group (str): Token group (``color``, ``space``, ``radius``,
                ``font-size``, ``line-height``, ``font-weight``,
                ``letter-spacing``, ``duration``, ``easing``).
            name (str): Token name within the group (``primary``,
                ``md``, ``body_medium``).

        Returns:
            str: ``var(--t-color-primary)``.
        """
        return f"var(--{self.prefix}-{group}-{_css_name(name)})"

    def color(self, role: str) -> str:
        """Return a ``var(...)`` reference to a colour role.

        Args:
            role (str): A colour role of the scheme (``primary``,
                ``on_surface``, ``error_container``, …).

        Returns:
            str: ``var(--t-color-primary)``.
        """
        return self.var("color", role)

    def space(self, name: str) -> str:
        """Return a ``var(...)`` reference to a spacing step.

        Args:
            name (str): Spacing step (``none``, ``xs``, ``sm``, ``md``,
                ``lg``, ``xl``, ``xxl``).

        Returns:
            str: ``var(--t-space-md)``.
        """
        return self.var("space", name)

    def radius(self, name: str) -> str:
        """Return a ``var(...)`` reference to a shape radius.

        Args:
            name (str): Shape step (``none``, ``xs``, ``sm``, ``md``,
                ``lg``, ``xl``, ``full``).

        Returns:
            str: ``var(--t-radius-md)``.
        """
        return self.var("radius", name)

    def font_size(self, name: str) -> str:
        """Return a ``var(...)`` reference to a typography size.

        Args:
            name (str): Typography token (``body_medium``,
                ``headline_large``, ``label_small``, …).

        Returns:
            str: ``var(--t-font-size-body-medium)``.
        """
        return self.var("font-size", name)

    def breakpoint(self, name: str) -> float:
        """Return a breakpoint width in pixels.

        Breakpoints are returned as numbers rather than custom
        properties: CSS media queries cannot read ``var()``.

        Args:
            name (str): Breakpoint name (``sm``, ``md``, ``lg``, ``xl``).

        Returns:
            float: The breakpoint width in pixels.

        Raises:
            KeyError: When the breakpoint name is unknown.
        """
        breakpoints: dict[str, float] = self._tokens().breakpoints.model_dump()
        if name not in breakpoints:
            raise KeyError(
                f"Unknown breakpoint {name!r}. "
                f"Available: {', '.join(sorted(breakpoints))}.",
            )
        return float(breakpoints[name])

    def _scalar_declarations(self) -> list[str]:
        """Build the non-colour custom properties.

        Returns:
            list[str]: ``--t-space-md: 16px``-style declarations, in
            token-set order (spacing, shape, typography, motion).
        """
        dumped: dict[str, Any] = self._tokens().model_dump(mode="json")
        prefix = self.prefix
        out: list[str] = []

        for name, value in dumped["spacing"].items():
            out.append(f"--{prefix}-space-{_css_name(name)}: {value}px")
        for name, value in dumped["shape"].items():
            out.append(f"--{prefix}-radius-{_css_name(name)}: {value}px")
        for name, scale in dumped["typography"].items():
            css_name = _css_name(name)
            out.append(f"--{prefix}-font-size-{css_name}: {scale['font_size']}px")
            out.append(f"--{prefix}-line-height-{css_name}: {scale['line_height']}px")
            out.append(f"--{prefix}-font-weight-{css_name}: {scale['font_weight']}")
            out.append(
                f"--{prefix}-letter-spacing-{css_name}: {scale['letter_spacing']}px",
            )
        motion: dict[str, Any] = dumped["motion"]
        for name in ("duration_short", "duration_medium", "duration_long"):
            suffix = _css_name(name.removeprefix("duration_"))
            out.append(f"--{prefix}-duration-{suffix}: {motion[name]}ms")
        for name in ("easing_standard", "easing_emphasized"):
            suffix = _css_name(name.removeprefix("easing_"))
            out.append(f"--{prefix}-easing-{suffix}: {motion[name]}")
        return out

    def _color_declarations(self, scheme: str) -> list[str]:
        """Build the colour custom properties of one scheme.

        Args:
            scheme (str): ``"light"`` or ``"dark"``.

        Returns:
            list[str]: ``--t-color-primary: rgba(…)``-style declarations.
        """
        dumped: dict[str, Any] = self._tokens().model_dump(mode="json")
        roles: dict[str, dict[str, float]] = dumped["schemes"][scheme]
        return [
            f"--{self.prefix}-color-{_css_name(role)}: {_rgba(color)}"
            for role, color in roles.items()
        ]

    def to_css(self) -> str:
        """Render every token as CSS custom properties.

        Returns:
            str: A ``:root`` block with the light scheme and the scalar
            scales, followed (when ``dark_mode`` is on) by the dark
            scheme under ``prefers-color-scheme`` and under
            ``[data-theme="dark"]``.
        """
        light = self._color_declarations("light") + self._scalar_declarations()
        blocks = [_block(":root", light)]

        if self.dark_mode:
            dark = self._color_declarations("dark")
            blocks.append(
                "@media (prefers-color-scheme: dark) {\n"
                + _block(':root:not([data-theme="light"])', dark, indent="  ")
                + "\n}",
            )
            blocks.append(_block(':root[data-theme="dark"]', dark))

        return "\n".join(blocks)


def _block(selector: str, declarations: list[str], *, indent: str = "") -> str:
    """Format a CSS block from a selector and its declarations.

    Args:
        selector (str): The block selector.
        declarations (list[str]): Declarations without trailing
            semicolons.
        indent (str): Indentation prepended to every line, used when the
            block is nested inside an at-rule.

    Returns:
        str: The formatted CSS block.
    """
    body = "".join(f"{indent}  {line};\n" for line in declarations)
    return f"{indent}{selector} {{\n{body}{indent}}}"


__all__: list[str] = ["ThemeTokens"]
