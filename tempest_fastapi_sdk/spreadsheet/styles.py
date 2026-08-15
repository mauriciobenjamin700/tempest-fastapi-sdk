"""Visual theme for a generated worksheet, as plain data.

:class:`SheetStyle` carries colours as hex strings and sizes as integers —
never ``openpyxl`` objects. That is deliberate: it keeps this module
importable (and a project's theme definable, testable and comparable)
without ``openpyxl`` installed, so only the code that actually writes a
workbook needs the ``[spreadsheet]`` extra. The conversion to ``Font`` /
``PatternFill`` / ``Border`` happens inside
:class:`~tempest_fastapi_sdk.spreadsheet.writer.SheetWriter`, where the
engine is already imported.

Colours follow ``openpyxl``'s convention: ``RRGGBB`` or ``AARRGGBB``, no
leading ``#``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SheetStyle:
    """Colours, sizes and emphasis for the rows a writer produces.

    Attributes:
        title_size (int): Point size of the merged title block.
        header_background (str): Fill behind the column-header row.
        header_foreground (str): Text colour of the column-header row.
        header_size (int): Point size of header and body text.
        group_background (str): Fill behind a section/group row — a
            subheading inside the table rather than above it.
        total_background (str): Fill behind a total row, the line a reader
            looks for first.
        border_color (str): Colour of the thin grid drawn around cells.
        font_name (str | None): Typeface for every cell. ``None`` leaves the
            workbook default, which is what keeps the file rendering
            identically on a machine that lacks the font.
    """

    title_size: int = 12
    header_background: str = "1F3864"
    header_foreground: str = "FFFFFF"
    header_size: int = 10
    group_background: str = "D9E2F3"
    total_background: str = "FFF2CC"
    border_color: str = "BFBFBF"
    font_name: str | None = None


DEFAULT_SHEET_STYLE: SheetStyle = SheetStyle()
"""The theme used when a writer is built without one.

Dark navy header with white text, pale blue group rows, pale amber totals —
legible in print and in grayscale, which is how a document submitted to a
public body is often read.
"""

__all__: list[str] = [
    "DEFAULT_SHEET_STYLE",
    "SheetStyle",
]
