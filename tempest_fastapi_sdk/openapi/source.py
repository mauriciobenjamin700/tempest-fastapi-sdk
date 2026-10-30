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

import ast
import itertools
import re
import textwrap

from tempest_fastapi_sdk.openapi.loader import SpecError

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


def string_chunks(text: str, budget: int) -> list[str]:
    """Split text so each piece renders as a literal within ``budget``.

    Args:
        text (str): The decoded text to split.
        budget (int): Line budget available to one rendered literal,
            quotes included.

    Returns:
        list[str]: Pieces whose concatenation is ``text`` exactly —
        whitespace is carried, never collapsed, so joining the emitted
        literals reproduces the specification's wording character for
        character. Cuts land after a space where one is reachable.

    The split is computed on the **decoded** text and each piece is
    re-rendered by the caller, because cutting the escaped literal could
    land between a backslash and the character it escapes.

    One piece is a legitimate result. ``ruff format`` un-parenthesizes a
    lone literal only when the collapsed line then fits — and a collapsed
    line that fits is one the caller never split. Forcing a second piece to
    guard against that made ``ruff format`` **join them back**, which is the
    same failure from the other side.
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
    return chunks


_DOCSTRING_QUOTE_RUN: re.Pattern[str] = re.compile(r'"(?=")|(?<=")"|"$')
"""A double quote that touches another one, or that ends the text.

Those are the only quotes that can take part in a ``\"\"\"`` sequence once
the text sits between two docstring delimiters. A lone quote mid-sentence
stays as it is, so ordinary prose (``the "reference" field``) renders
unchanged.
"""

_DOCSTRING_WHITESPACE: frozenset[str] = frozenset("\t\n\r\x0b\x0c")
"""Control characters left raw, because :func:`wrap` folds them into spaces."""


def docstring_text(text: str) -> str:
    r"""Neutralize specification prose so it is inert inside a docstring.

    Args:
        text (str): Prose taken from the specification — a description, a
            summary, a title — about to be placed between ``\"\"\"``
            delimiters.

    Returns:
        str: Source text whose value, once Python parses the docstring, is
        ``text`` again. Every backslash is doubled, every double quote that
        touches another one (or ends the text) is escaped, and every
        non-whitespace control character becomes a ``\xNN`` escape.

    The docstring is source, not data: a description carrying ``\"\"\"``
    used to close the literal and turn the rest of the sentence into Python
    statements, which ran when the generated module was imported. Escaping
    the backslash is what keeps that closed — a trailing ``\`` left raw
    would escape the closing delimiter instead — and is also why the emitted
    docstrings never need the ``r`` prefix: ``\#`` in a specification
    renders as ``\\#``, which is not an invalid escape.
    """
    escaped = text.replace("\\", "\\\\")
    escaped = _DOCSTRING_QUOTE_RUN.sub('\\\\"', escaped)
    return "".join(
        f"\\x{ord(char):02x}"
        if (char < " " and char not in _DOCSTRING_WHITESPACE) or char == "\x7f"
        else char
        for char in escaped
    )


def docstring_title(text: str) -> str:
    """Render a one-line name (``info.title``) for interpolation in a docstring.

    Args:
        text (str): The name as the specification wrote it.

    Returns:
        str: :func:`docstring_text` of it with every run of whitespace
        collapsed to one space, because the caller places it inside a line
        of fixed source rather than wrapping it.
    """
    return " ".join(docstring_text(text).split())


def wrap(
    text: str,
    indent: str,
    first_prefix: str = "",
    *,
    hanging: bool = True,
    budget: int = MAX_LINE,
    escape: bool = True,
) -> list[str]:
    """Wrap prose to the line budget.

    Args:
        text (str): The prose to wrap. Passed through
            :func:`docstring_text` first unless ``escape`` is false, so the
            width is measured on the source that is actually emitted.
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
        budget (int): Width to wrap to. Defaults to :data:`MAX_LINE`; a
            caller passes less when the formatter will append something to
            the last line after this function returns.
        escape (bool): Whether ``text`` is docstring content. Every caller
            but :func:`unsupported_comment` places the result inside a
            docstring; a ``#`` comment cannot be closed early, because the
            wrap folds every line break into a space.

    Returns:
        list[str]: Wrapped source lines. A single unbreakable token longer
        than the budget is emitted whole rather than mangled.
    """
    body = f"{first_prefix}{docstring_text(text) if escape else text}"
    wrapped = textwrap.wrap(
        body,
        width=budget,
        initial_indent=indent,
        subsequent_indent=f"{indent}    " if hanging else indent,
        break_long_words=False,
        break_on_hyphens=False,
    )
    return wrapped or [f"{indent}{body}"]


UNSUPPORTED_MARKER: str = "# openapi: unsupported"
"""Prefix of the comment marking a construct the generator could not model.

Greppable on purpose. A reader who opens the module months later and finds an
``Any`` has no other way to learn why: the command's summary is terminal output
that scrolled away, and regenerating to read it again needs the specification
that produced it.
"""


def unsupported_comment(notes: tuple[str, ...], indent: str) -> list[str]:
    """Render the ``# openapi: unsupported`` comments for one output line.

    Args:
        notes (tuple[str, ...]): Reasons collected while parsing the field,
            parameter or operation this comment sits above.
        indent (str): Leading whitespace of the line being marked.

    Returns:
        list[str]: One wrapped comment block per note, empty when there are
        none. Comments go **above** the line rather than trailing it, so a
        long reason wraps instead of overrunning the budget.

    Wrapping is ``hanging=True`` because each continuation line is re-emitted
    with a ``"#   "`` prefix — four characters, the same width the hanging
    indent reserves. Wrapping flat instead let a long note overrun the budget
    by exactly those four characters, which surfaces as ``E501`` in the
    consumer's own lint run, on a line nobody wrote by hand.
    """
    lines: list[str] = []
    for note in notes:
        wrapped = wrap(
            note, indent, f"{UNSUPPORTED_MARKER} — ", hanging=True, escape=False
        )
        lines.append(wrapped[0])
        lines.extend(f"{indent}#   {line.strip()}" for line in wrapped[1:])
    return lines


_FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "__builtins__",
        "__import__",
        "breakpoint",
        "compile",
        "eval",
        "exec",
        "globals",
        "locals",
        "open",
        "vars",
    }
)
"""Names no emitter ever writes, and the first thing injected code reaches for."""


def _is_docstring(node: ast.stmt) -> bool:
    """Report whether a statement is a bare string literal.

    Args:
        node (ast.stmt): The statement to inspect.

    Returns:
        bool: ``True`` for an expression statement holding one ``str``
        constant — a docstring, or an attribute docstring under an
        assignment.
    """
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _expression_statement_is_expected(node: ast.Expr) -> bool:
    """Report whether an expression statement has a shape the emitters write.

    Args:
        node (ast.Expr): The expression statement.

    Returns:
        bool: ``True`` for a docstring, and for a call, ``await`` or
        ``yield`` whose callee is reached through names rather than through
        a literal — ``response.raise_for_status()``,
        ``await self._socket.close()``. Prose that escaped a docstring
        surfaces as an operator joining two string literals
        (``\"\"\"\"\"\"+expr+\"\"\"``) or as a method called on one, and
        both are refused here.
    """
    if _is_docstring(node):
        return True
    value: ast.expr = node.value
    if isinstance(value, (ast.Await, ast.Yield)) and value.value is not None:
        value = value.value
    if not isinstance(value, ast.Call):
        return False
    callee: ast.expr = value.func
    while isinstance(callee, (ast.Attribute, ast.Call, ast.Subscript)):
        callee = callee.func if isinstance(callee, ast.Call) else callee.value
    return isinstance(callee, ast.Name)


def check_generated_source(source: str, filename: str) -> None:
    """Refuse an emitted module that holds statements no emitter writes.

    Args:
        source (str): The module source, as about to be written.
        filename (str): Name reported in the error.

    Raises:
        SpecError: When the source does not parse; when a module, class or
            function opens with an expression statement that is not a lone
            string literal; when an expression statement has any other
            shape than a docstring or a call through names; when two
            statements share a line; when a name such as ``__import__`` or
            ``eval`` appears; or when an f-string interpolates anything but
            a name or a ``_path_param(name)`` call.

    Defense in depth behind :func:`docstring_text` and
    :func:`string_literal`: every specification string is already escaped
    on the way in, and this pass is what turns a gap in that escaping into
    a refused generation instead of a module that runs the specification
    author's code when the consumer imports it.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise SpecError(
            f"The generated {filename} does not parse ({exc.msg}, line "
            f"{exc.lineno}). The specification text was not rendered safely."
        ) from exc

    def refuse(node: ast.AST, reason: str) -> SpecError:
        """Build the error naming what was found and where.

        Args:
            node (ast.AST): The offending node.
            reason (str): What the node is, for the message.

        Returns:
            SpecError: The error to raise.
        """
        line = getattr(node, "lineno", "?")
        return SpecError(
            f"The generated {filename} holds {reason} at line {line}, which "
            "no emitter writes. Refusing to write it: the specification text "
            "would run as code."
        )

    for node in ast.walk(tree):
        if (
            isinstance(
                node,
                ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
            )
            and node.body
        ):
            first = node.body[0]
            if isinstance(first, ast.Expr) and not _is_docstring(first):
                raise refuse(first, "a docstring that is not a lone literal")
        for field_name in ("body", "orelse", "finalbody"):
            block = getattr(node, field_name, None)
            if not isinstance(block, list):
                continue
            statements = [item for item in block if isinstance(item, ast.stmt)]
            for previous, current in itertools.pairwise(statements):
                end = previous.end_lineno or previous.lineno
                if current.lineno <= end:
                    raise refuse(current, "two statements on one line")
        if isinstance(node, ast.Expr) and not _expression_statement_is_expected(node):
            raise refuse(node, "an unexpected expression statement")
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in _FORBIDDEN_NAMES
        ):
            raise refuse(node, f"a reference to `{node.id}`")
        if isinstance(node, ast.FormattedValue):
            value = node.value
            is_path_param = (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "_path_param"
                and len(value.args) == 1
                and not value.keywords
                and isinstance(value.args[0], ast.Name)
            )
            if not (isinstance(value, ast.Name) or is_path_param):
                raise refuse(node, "an f-string interpolating an expression")


__all__: list[str] = [
    "MAX_LINE",
    "UNSUPPORTED_MARKER",
    "check_generated_source",
    "docstring_text",
    "docstring_title",
    "string_chunks",
    "string_literal",
    "unsupported_comment",
    "wrap",
]
