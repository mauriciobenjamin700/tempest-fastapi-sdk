"""Primitives both emitters need to turn specification text into source.

The generated package has to satisfy the consumer's own quality gate, not
only import: ``ruff check`` and ``ruff format --check`` run over it in their
repository, with their settings. That makes two properties load-bearing and
neither is obvious.

``ruff format`` **never breaks a string**. A description long enough to
overrun the line budget survives the format pass untouched and fails the
consumer's ``E501``, so the emitter has to split it into adjacent literals
itself. And a run of adjacent literals is only preserved while the pieces do
not fit on one line — a single literal wrapped in parentheses is joined
straight back, which is why every split here yields at least two pieces.

``ruff format`` also **normalizes quotes**: double by default, single when
that means fewer escapes. Emitting a double-quoted literal for text carrying
double quotes is therefore not merely a style choice — it makes the
generated file fail ``ruff format --check`` on the consumer's first run.
"""

from __future__ import annotations

import textwrap

MAX_LINE: int = 88
"""Line budget matching the project's ruff configuration."""

_ESCAPES: dict[str, str] = {
    "\\": "\\\\",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}
"""Characters no quoting style can carry raw inside a literal."""


def string_literal(value: str) -> str:
    r"""Render a string as a Python literal ``ruff format`` leaves alone.

    Args:
        value (str): Text taken from the specification — a description, a
            title, a wire alias, an enum value, a path template.

    Returns:
        str: Source text. Double-quoted per the project's style, except
        when the text carries more ``"`` than ``'`` — then single quotes
        are used, because that is what ``ruff format`` normalizes to and a
        generated file that disagrees fails the consumer's
        ``ruff format --check``. Every character that would end the literal
        or change what it means is escaped.

    ``repr`` cannot render this on its own: it delimits with single quotes,
    switching to double ones only for text that already contains a single
    quote. The shortcut that used to double-quote the value interpolated it
    **raw**, and its guard (``"'" in repr(value)``) matched the delimiter
    rather than an apostrophe in the text, so every quote-free string took
    that path. A description carried over from a YAML block scalar emitted
    an unterminated string — ``schemas.py`` did not import — and one
    containing a backslash escape (``\b``, ``\x41``) changed value in
    silence.
    """
    quote = "'" if value.count('"') > value.count("'") else '"'
    parts: list[str] = []
    for char in value:
        escaped = _ESCAPES.get(char)
        if escaped is not None:
            parts.append(escaped)
        elif char == quote:
            parts.append(f"\\{char}")
        elif char < " " or char == "\x7f":
            parts.append(f"\\x{ord(char):02x}")
        else:
            parts.append(char)
    return quote + "".join(parts) + quote


def string_chunks(text: str, budget: int, *, minimum: int = 1) -> list[str]:
    """Split text so each piece renders as a literal within ``budget``.

    Args:
        text (str): The decoded text to split.
        budget (int): Line budget available to one rendered literal,
            quotes included.
        minimum (int): Fewest pieces to return. Callers that emit the
            pieces as a parenthesized run pass ``2``: ``ruff format``
            removes the parentheses around a lone literal and puts the
            long line back, so a split into one piece is not a split.

    Returns:
        list[str]: Pieces whose concatenation is ``text`` exactly —
        whitespace is carried, never collapsed, so joining the emitted
        literals reproduces the specification's wording character for
        character. Cuts land after a space where one is reachable.

    The split is computed on the **decoded** text and each piece is
    re-rendered by the caller, because cutting the escaped literal could
    land between a backslash and the character it escapes.
    """
    chunks: list[str] = []
    remaining = text
    while len(string_literal(remaining)) > budget:
        cut = min(len(remaining) - 1, max(1, budget - 2))
        while cut > 1 and len(string_literal(remaining[:cut])) > budget:
            cut -= 1
        space = remaining.rfind(" ", 1, cut)
        if space > 0:
            cut = space + 1
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    chunks.append(remaining)

    while len(chunks) < minimum and len(chunks[-1]) > 1:
        last = chunks.pop()
        middle = len(last) // 2
        chunks.extend([last[:middle], last[middle:]])
    return chunks


def docstring_delimiter(*texts: str) -> str:
    r"""Return the opening delimiter a docstring built from ``texts`` needs.

    Args:
        *texts (str): Every piece of prose the docstring will carry, either
            raw or already rendered as source lines.

    Returns:
        str: ``'\"\"\"'``, or ``'r\"\"\"'`` when any text carries a
        backslash.

    A specification is free to put one in its prose — OpenPix documents the
    characters needing URI encoding as ``(%, \#, /)`` — and ``\#`` is not a
    Python escape, so the emitted docstring raised ``W605`` and, from 3.12,
    a ``SyntaxWarning``. It survived review because the generator's own
    ``ruff --fix`` pass adds the prefix afterwards, which hides the defect
    from everyone except a caller who passes ``--no-format``.

    The decision is taken **before** wrapping rather than by patching the
    rendered first line, because the ``r`` costs a column and the opening
    line is the one already closest to the budget.
    """
    return 'r"""' if any("\\" in text for text in texts) else '"""'


def wrap(
    text: str,
    indent: str,
    first_prefix: str = "",
    *,
    hanging: bool = True,
) -> list[str]:
    """Wrap prose to the line budget.

    Args:
        text (str): The prose to wrap.
        indent (str): Indentation for the first line.
        first_prefix (str): Text prefixed to the first line (an opening
            ``\"\"\"``, or an ``Attributes:`` entry's ``"name (type): "``).
        hanging (bool): Whether continuation lines indent one level past
            ``indent``. True for a Google-style entry, whose wrapped text
            hangs under its ``name (type): `` label. **False for a
            docstring summary or paragraph** — hanging one there pushes the
            continuation deeper than the ``Attributes:`` heading that
            follows it, which reads as though the sentence belonged to
            something else.

    Returns:
        list[str]: Wrapped source lines. A single unbreakable token longer
        than the budget is emitted whole rather than mangled.
    """
    body = f"{first_prefix}{text}"
    wrapped = textwrap.wrap(
        body,
        width=MAX_LINE,
        initial_indent=indent,
        subsequent_indent=f"{indent}    " if hanging else indent,
        break_long_words=False,
        break_on_hyphens=False,
    )
    return wrapped or [f"{indent}{body}"]


__all__: list[str] = [
    "MAX_LINE",
    "docstring_delimiter",
    "string_chunks",
    "string_literal",
    "wrap",
]
