# Slides, answer keys and Markdown to PDF

A service that teaches — or that asks a language model to put a lesson
together — ends up holding text like this:

```text
Lesson 1: Logic gates
What a gate is
A logic gate maps binary inputs to a single binary output.
```

And it has to deliver that as a presentation, as a PDF to print, as a
presenter handout. The usual path is one parser per format, each with its own
idea of what a bullet is, and the PowerPoint ends up saying one thing and the
PDF another.

`tempest_fastapi_sdk.slides` reads the text **once**, into a typed object, and
every format comes out of that same object: HTML, PDF, PPTX, DOCX and
Markdown. The same holds for answer keys (numbered questions and answers) and
for free-form Markdown turned into PDF, with the stylesheet you choose — ABNT
included.

!!! info "Required extra"
    ```bash
    uv add "tempest-fastapi-sdk[slides]"
    ```
    Brings in `weasyprint`, `jinja2`, `python-pptx`, `python-docx` and
    `markdown`. The PDF uses the same engine as
    [PDF generation](pdf.md), so it **needs the same system libraries** —
    read [Deployment](pdf.md#deployment-the-system-libraries). The parser and
    the schemas import without the extra: each engine loads on first use.

## Your first deck

```python
# scripts/lesson.py

import asyncio
from pathlib import Path

from tempest_fastapi_sdk.slides import SlideDeck, SlideRenderer, parse_slides

SOURCE: str = """Lesson 1: Logic gates
What a gate is
A logic gate maps binary inputs to a single binary output.
Notes: ask the class for a real-world example first.
The gates you will meet
- NOT gate: inverts its only input, so 0 becomes 1.
- AND gate: outputs 1 only when every input is 1.
- OR gate: outputs 1 when at least one input is 1.
Conclusion
Every digital circuit is built from a handful of gates.
"""


async def main() -> None:
    """Parse the lesson and write it as HTML and PowerPoint."""
    deck: SlideDeck = parse_slides(SOURCE)
    renderer = SlideRenderer()
    Path("lesson.html").write_text(renderer.html(deck), encoding="utf-8")
    Path("lesson.pptx").write_bytes(await renderer.pptx(deck))
    for slide in deck.slides:
        print(slide.slide_number, slide.kind, slide.title)


if __name__ == "__main__":
    asyncio.run(main())
```

Run it and you get:

```text
1 lesson_title Logic gates
2 content What a gate is
3 bullets The gates you will meet
4 conclusion Conclusion
```

Four slides, and every line of the text landed somewhere:

- **`Lesson 1: Logic gates`** opens a lesson and becomes its cover
  (`lesson_title`). The deck title, when you pass no `deck_title=`, is the
  first lesson's.
- **A short line (4 to 90 characters), capitalized, with no closing
  punctuation** (`What a gate is`) opens a section; a single word needs at
  least 8 characters, so a stray `Yes` does not open one. The section title is the slide title.
- **A paragraph** under a section becomes a content slide (`content`).
- **`Term: description`** becomes a bullet, and the bullets of a section share
  one slide (`bullets`). The leading dash is optional.
- **`Notes:`** is a speaker note, attached to the paragraph or bullet right
  above it.
- **A section whose title contains `conclusion`** becomes a conclusion slide.

`parse_slides` is synchronous: it is pure CPU over a string. The renderers
that produce binaries (`pdf`, `pptx`, `docx`) are `async` and run in a worker
thread, so a long deck does not block the event loop.

!!! tip "What does not become a bullet"
    A sentence with a colon in the middle would pass as a bullet if the rule
    were just "has a `:`". So the term must start with a capital, have at most
    8 words and 80 characters, carry no `.,!?;` and no unbalanced
    parenthesis, and contain no sentence verb (` is `, ` are `, ` means `, …);
    the description needs at least 20 characters. `The result is: 42 after
    the second pass.` stays a paragraph — without the full stop, the same
    line on its own becomes a section title.

### Slides that do not overflow

A section with a lot of content becomes more than one slide, instead of one
slide in a tiny font:

- **More than 6 bullets** open another slide with the same title. Measured
  with 8 bullets in a section: one slide with 6 and another with 2.
- **Paragraphs** group up to 700 characters per slide. A paragraph is never
  cut: one longer than the limit sits alone on its own slide.

## The vocabulary: text in your service's language

The markers the parser reads and the labels the renderers print are not
fixed. They live in a `SlideVocabulary`, English by default. A service that
writes Portuguese passes its own:

```python
from tempest_fastapi_sdk.slides import SlideDeck, SlideVocabulary, parse_slides

PT_BR: SlideVocabulary = SlideVocabulary(
    lesson_pattern=r"^Aula\s+(\d+)\s*:\s*(.+?)\s*$",
    notes_pattern=r"^(?:Notas|Nota)\s*:\s*(.+)$",
    conclusion_marker="conclus",
    sentence_verbs=(" é ", " são ", " foi ", " foram ", " será ", " tem ", " significa "),
    default_lesson_title="Conteúdo",
    lesson_label="Aula {index}",
    conclusion_label="Conclusão",
    handout_subtitle="Roteiro do apresentador",
    handout_cover_note="Slide de capa.",
    notes_heading="Notas do apresentador",
    language="pt-BR",
)

TEXT: str = """Aula 1: Portas lógicas
O que é uma porta
Uma porta lógica recebe entradas binárias e devolve uma única saída binária.
Notas: peça um exemplo do dia a dia antes de seguir.
As portas básicas
- Porta NOT: inverte a única entrada, então 0 vira 1.
- Porta AND: devolve 1 só quando todas as entradas valem 1.
Conclusão
Todo circuito digital é montado a partir de poucas portas.
"""

deck: SlideDeck = parse_slides(TEXT, vocabulary=PT_BR)
for slide in deck.slides:
    print(slide.slide_number, slide.kind, slide.title, slide.subtitle)
```

```text
1 lesson_title Portas lógicas Aula 1
2 content O que é uma porta None
3 bullets As portas básicas None
4 conclusion Conclusão None
```

Two parts, with different jobs:

- **Markers** (`lesson_pattern`, `notes_pattern`, `conclusion_marker`,
  `sentence_verbs`, `ignored_lines`) decide how the text is **read**. Both
  patterns are regular expressions, matched case-insensitively;
  `lesson_pattern` needs two groups (number and title), `notes_pattern` one
  (the note text). `conclusion_marker="conclus"` matches `Conclusão` and
  `Conclusões`.
- **Labels** (`lesson_label`, `conclusion_label`, `handout_subtitle`,
  `notes_heading`, `slide_label`, `language`, …) decide what is **printed**.
  `{index}` in `lesson_label` becomes the lesson number; `language` goes into
  `<html lang>`.

Without the vocabulary, the same Portuguese text comes out wrong — and with no
error at all, because nothing in it is invalid, it just is not what you meant.
Measured with the English default over the `TEXT` above:

```text
[('lesson_title', 'Content'), ('content', 'O que é uma porta'),
 ('bullets', 'O que é uma porta'), ('bullets', 'As portas básicas'),
 ('content', 'Conclusão')]
```

`Aula 1:` opened no lesson (the cover is the implicit `Content` one), the
`Notas:` line became a bullet and the conclusion became an ordinary slide.

!!! warning "Pass the same vocabulary to the renderer"
    ```python
    from tempest_fastapi_sdk.slides import SlideRenderer, SlideVocabulary

    PT_BR: SlideVocabulary = SlideVocabulary(
        lesson_pattern=r"^Aula\s+(\d+)\s*:\s*(.+?)\s*$",
        lesson_label="Aula {index}",
        conclusion_label="Conclusão",
    )
    renderer: SlideRenderer = SlideRenderer(vocabulary=PT_BR)
    ```
    The parser uses the markers; the renderer, the labels. A
    `SlideRenderer()` without a vocabulary prints `Lesson 1` and `Conclusion`
    on a deck read in Portuguese. Build the vocabulary once, as a service
    constant, and hand it to both.

An invalid pattern fails at **construction**, far from the first user text,
and the message names the field:

```text
ValueError: lesson_pattern needs two groups: number and title
ValueError: lesson_pattern is not a valid regular expression: missing ), unterminated subpattern at position 0
```

## Every format

One `SlideDeck`, five outputs:

| Format | Call | Speaker notes | Theme |
| --- | --- | --- | --- |
| HTML | `renderer.html(deck, theme=...)` — sync, `str` | no | yes |
| PDF | `await renderer.pdf(deck, theme=...)` — `bytes` | no | yes |
| PPTX | `await renderer.pptx(deck, theme=...)` — `bytes` | in the notes pane | yes |
| DOCX | `await renderer.docx(deck)` — `bytes` | a block per page | no |
| Markdown | `renderer.markdown(deck)` — sync, `str` | a `Note:` block | no |

```python
# scripts/formats.py

import asyncio
from pathlib import Path

from tempest_fastapi_sdk.slides import SlideDeck, SlideRenderer, SlideThemeEnum, parse_slides


async def main() -> None:
    """Write one deck in every format."""
    deck: SlideDeck = parse_slides(Path("lesson.txt").read_text(encoding="utf-8"))
    renderer = SlideRenderer(max_concurrent=2)
    Path("lesson.html").write_text(renderer.html(deck), encoding="utf-8")
    Path("lesson.md").write_text(renderer.markdown(deck), encoding="utf-8")
    Path("lesson.pdf").write_bytes(await renderer.pdf(deck, theme=SlideThemeEnum.DARK))
    Path("lesson.pptx").write_bytes(await renderer.pptx(deck, theme=SlideThemeEnum.DARK))
    Path("handout.docx").write_bytes(await renderer.docx(deck))


if __name__ == "__main__":
    asyncio.run(main())
```

What each one delivers, measured on the four-slide deck from the start:

- **HTML** is a single document, CSS inlined and nothing fetched — it opens
  offline and fits an `<iframe srcdoc>` preview. Every value is escaped.
- **PDF** is that same HTML laid out: one page per slide, 960 × 540 pt
  (1280 × 720 px). The four slides gave four pages.
- **PPTX** comes out 16:9 (13.33 × 7.5 inches), one slide per slide, with the
  speaker note in the notes pane — slide 2 carried
  `ask the class for a real-world example first.`, the others none.
- **DOCX** is the presenter handout: a cover with the title and
  `Presenter handout` (the `handout_subtitle`), then one page per slide with
  the lesson label, the title, the content and a `Speaker notes` block —
  holding the note, or blank lines to write on by hand. The footer reads
  `Logic gates  ·  Slide 1 / 4`.
- **Markdown** is reveal.js flavoured, slides separated by `---`:

```text
<!-- Deck: Logic gates -->
<!-- Total slides: 4 -->

<small>Lesson 1</small>

# Logic gates

---

<small>Lesson 1</small>

## What a gate is

A logic gate maps binary inputs to a single binary output.

Note:
ask the class for a real-world example first.
```

`SlideThemeEnum.DEFAULT` is dark text on white with blue accents;
`SlideThemeEnum.DARK`, light text on a slate background. `max_concurrent` caps
how many PDF, PPTX and DOCX renders run at once on that renderer — each is CPU
in a worker thread, and without a ceiling a burst of requests takes every
core.

## Answer keys

An answer key is another text format — title, parts, numbered questions and
answers — with its own parser and renderer:

```python
# scripts/answer_key.py

import asyncio
from pathlib import Path

from tempest_fastapi_sdk.slides import (
    AnswerKey,
    AnswerKeyRenderer,
    AnswerKeyVocabulary,
    parse_answer_key,
)

PT_BR: AnswerKeyVocabulary = AnswerKeyVocabulary(
    title_prefixes=("título:",),
    subtitle_prefixes=("subtítulo:",),
    answer_pattern=r"^(?:R|Resposta)\s*[:.]\s*(.*)$",
    default_title="Gabarito",
    answer_label="Resposta:",
    footer="Gabarito de uso do professor",
    language="pt-BR",
)

TEXT: str = """Título: Portas lógicas — gabarito
Subtítulo: Turma 3B, 2026

Parte 1: Conceitos
1. O que faz uma porta NOT?
R: Inverte a entrada: 0 vira 1 e 1 vira 0.
2. Quando a porta AND devolve 1?
Resposta:
Só quando <b>todas</b> as entradas valem 1.

Parte 2: Aplicação
1) Qual porta implementa "pelo menos um"?
R: A porta OR.
"""


async def main() -> None:
    """Parse the answer key and write it as Markdown, PDF and DOCX."""
    answer_key: AnswerKey = parse_answer_key(TEXT, vocabulary=PT_BR)
    renderer = AnswerKeyRenderer(vocabulary=PT_BR)
    print(answer_key.title, "|", answer_key.subtitle, "|", answer_key.total_items)
    Path("answer_key.md").write_text(renderer.markdown(answer_key), encoding="utf-8")
    Path("answer_key.pdf").write_bytes(await renderer.pdf(answer_key))
    Path("answer_key.docx").write_bytes(await renderer.docx(answer_key))


if __name__ == "__main__":
    asyncio.run(main())
```

```text
Portas lógicas — gabarito | Turma 3B, 2026 | 3
```

And `answer_key.md`:

```text
# Portas lógicas — gabarito

_Turma 3B, 2026_

## Parte 1: Conceitos

**1. O que faz uma porta NOT?**

> Resposta: Inverte a entrada: 0 vira 1 e 1 vira 0.

**2. Quando a porta AND devolve 1?**

> Resposta: Só quando todas as entradas valem 1.

## Parte 2: Aplicação

**1) Qual porta implementa "pelo menos um"?**

> Resposta: A porta OR.
```

The reading rules:

- **`1.` or `1)`** opens a question, and the numbering stays in its text.
- **The answer marker** (`answer_pattern`) opens the answer, which runs until
  the next question or part. The answer can start on the same line
  (`R: A porta OR.`) or on the next one (`Resposta:` alone) — several lines
  are joined with spaces.
- **A capitalized line that does not end like a sentence** opens a part — but
  only outside a question or after a blank line, so the second line of a
  wrapped question stays in the question.
- `title=` and `subtitle=` on the call override the lines in the text.

`AnswerKeyRenderer` has `html`, `markdown` (sync), `pdf` (A4) and `docx`
(`async`). `footer` prints one line at the end of the HTML and the PDF;
`None`, the default, prints none.

!!! note "HTML tags in the text are stripped, in every format"
    Answer keys written by a model sometimes arrive with `<b>`. The schema
    keeps the text as it came (`'Só quando <b>todas</b> as entradas valem
    1.'`), and every renderer strips the tag before writing — look at the
    Markdown above. The HTML then still escapes whatever is left. An answer
    `<script>alert(1)</script>yes` comes out `alert(1)yes`, as text, in all
    four formats.

## Markdown to PDF

The third job has no parser: free-form Markdown — a report, a thesis, a
document a model wrote — laid out as PDF. The stylesheet is a parameter:

```python
# scripts/thesis.py

import asyncio
from pathlib import Path

from tempest_fastapi_sdk.slides import ABNT_MARKDOWN_CSS, MarkdownPdf, MarkdownPdfRenderer

TEXT: str = """# Introdução

Este trabalho descreve as portas lógicas.

| Porta | Saída |
| --- | --- |
| NOT | inverte |
"""


async def main() -> None:
    """Render a Markdown document with the ABNT stylesheet."""
    renderer = MarkdownPdfRenderer(stylesheet=ABNT_MARKDOWN_CSS)
    document: MarkdownPdf = await renderer.render(TEXT, title="TCC")
    Path(f"{document.title}.pdf").write_bytes(document.content)


if __name__ == "__main__":
    asyncio.run(main())
```

`render` returns a `MarkdownPdf` with `content` (the bytes) and `title` —
yours, or `default_title` (`"Document"`) when you pass none. Three possible
stylesheets:

- **`DEFAULT_MARKDOWN_CSS`** (the default): A4, sans-serif, page numbers.
- **`ABNT_MARKDOWN_CSS`**: the ABNT NBR 14724 layout for Brazilian academic
  work — A4, 3 cm top and left margins and 2 cm bottom and right, Times New
  Roman 12 pt, 1.5 line spacing, justified text, 1.25 cm first-line indent,
  first-level headings in upper case. Measured: `# Introdução` comes out
  `INTRODUÇÃO` in the PDF text with this sheet, and `Introdução` with the
  default.
- **Yours**: any CSS the service maintains, passed as `stylesheet=`.

The Markdown enables tables, fenced code blocks, footnotes, a table of
contents (`toc`) and `attr_list`. Code is coloured when Pygments is installed
and left plain when it is not.

!!! danger "Raw text fetches nothing"
    Markdown from a user is a URL from a user: `![x](file:///etc/passwd)`
    would read the server's disk, and `![x](http://169.254.169.254/...)` would
    make a request from inside your network. `render` goes through the same
    [asset policy](pdf.md#security-what-a-template-may-load) as PDF
    generation, with nothing allowed: both raise `AssetRefused` (422,
    `PDF_ASSET_REFUSED`) and no PDF comes out. `data:` still passes, because
    it carries its own bytes — measured with an SVG image as
    `data:image/svg+xml;base64,...`.

### Markdown with images: the `.zip` archive

A document with an image has to bring the image along. `render_archive` takes
a `.zip` holding the Markdown and the files it references:

```python
# scripts/zipped_report.py

import asyncio
import io
import zipfile
from pathlib import Path

from tempest_fastapi_sdk.slides import MarkdownPdf, MarkdownPdfRenderer

SQUARE: str = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    '<rect width="10" height="10"/></svg>'
)


def build_archive() -> bytes:
    """Zip one Markdown file and the image it references."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report/report.md", "# With an image\n\n![square](img/q.svg)\n")
        archive.writestr("report/img/q.svg", SQUARE)
    return buffer.getvalue()


async def main() -> None:
    """Render a zipped Markdown document with its image."""
    document: MarkdownPdf = await MarkdownPdfRenderer().render_archive(build_archive())
    print(document.title)
    Path(f"{document.title}.pdf").write_bytes(document.content)


if __name__ == "__main__":
    asyncio.run(main())
```

It prints `report`: without `title=`, the title is the Markdown file's name
without its extension. How the file is chosen and what it may read:

- **The first `.md` in path order** is the document. With `a.md` and `b.md`
  in the same zip, `a.md` wins.
- **Relative paths resolve from the Markdown's directory** — `img/q.svg`
  above is `report/img/q.svg`.
- **Nothing outside the extracted zip can be read.**
  `![x](../../../../etc/passwd)` raises `AssetRefused`: that render's asset
  policy allows only the extraction directory.

And the archive is checked **before** a single byte reaches the disk, from
the zip's central directory. Each refusal is a `MarkdownArchiveError` (422,
`MARKDOWN_ARCHIVE_INVALID`) with `details["reason"]`:

| `reason` | When |
| --- | --- |
| `not_a_zip` | the bytes are not a zip |
| `unsafe_member` | an entry would land outside the extraction directory (`../outside.md`, `/abs.md`) |
| `too_many_members` | more entries than `max_archive_members` (default `DEFAULT_MAX_ARCHIVE_MEMBERS`, 500) |
| `too_large` | the declared uncompressed size exceeds `max_archive_bytes` (default `DEFAULT_MAX_ARCHIVE_BYTES`, 50 MiB) |
| `no_markdown` | no `.md` in the zip |
| `not_utf8` | the Markdown is not UTF-8 |

`too_large` is what holds a zip bomb back: measured, a 221-byte zip declaring
100,000 uncompressed bytes is refused with `max_archive_bytes=50_000`,
without extracting. The refused entry's name never goes into the error — it
is text chosen by whoever sent the file.

### Mermaid diagrams

WeasyPrint runs no JavaScript, so a `` ```mermaid `` block would reach the PDF
as code. With `render_mermaid=True`, each block is drawn in headless Chromium
before layout:

```bash
uv add "tempest-fastapi-sdk[slides,slides-mermaid]"
uv run playwright install chromium
```

```python
# scripts/flow.py

import asyncio
from pathlib import Path

from tempest_fastapi_sdk.slides import MarkdownPdf, MarkdownPdfRenderer

FENCE: str = "`" * 3
TEXT: str = f"# Flow\n\n{FENCE}mermaid\ngraph TD\n  Order-->Payment\n{FENCE}\n"


async def main() -> None:
    """Render a document whose Mermaid block becomes a drawing."""
    renderer = MarkdownPdfRenderer(
        mermaid_timeout=15.0,
        mermaid_error_message="This diagram could not be drawn.",
    )
    document: MarkdownPdf = await renderer.render(TEXT, render_mermaid=True)
    Path("flow.pdf").write_bytes(document.content)


if __name__ == "__main__":
    asyncio.run(main())
```

Measured with Chromium installed: the valid diagram becomes inline SVG (its
node labels show up in the PDF text), and an invalid block in the same
document becomes a box with the message and the escaped source. One browser
draws every block of the document.

A failing diagram never takes the document down. Without Playwright, without
Chromium (measured with Playwright installed and the browser missing), with
invalid syntax or past the deadline (measured with `mermaid_timeout=0.001`),
the block becomes the `.mermaid-error` box and the PDF still comes out. Both
stylesheets the SDK ships style that box and `.mermaid-diagram`; a sheet of
your own that starts from either keeps diagrams inside the page.

!!! warning "The Mermaid script comes from the internet"
    The Chromium page loads `mermaid@10.9.8` from jsDelivr on every render.
    That request is the browser's, **outside** the PDF's asset policy — the
    server needs egress to `cdn.jsdelivr.net`. Measured by pointing the
    script at an address that does not answer: the diagram becomes an error
    box. The version is pinned, so a new Mermaid release does not change your
    PDF without notice.

!!! info "Why it is a separate extra, and out of `[all]`"
    Playwright asks for a browser download (`playwright install chromium`)
    that a service rendering plain decks should not carry. That is why
    `[slides-mermaid]` is neither in `[slides]` nor in `[all]`.

## Errors

They are all `AppException`s: let them propagate from the route and the
client gets the SDK envelope, with `code`. With
`register_exception_handlers(app, catalog=default_message_catalog())`, the
sentence follows `Accept-Language` — entries exist in pt-BR and en-US.

| Exception | Status | `code` | When |
| --- | --- | --- | --- |
| `SlideSourceEmptyError` | 422 | `SLIDES_SOURCE_EMPTY` | empty or whitespace text (parser, `render`, empty Markdown in a zip) |
| `SlideSourceTooLargeError` | 413 | `SLIDES_SOURCE_TOO_LARGE` | text longer than `max_chars` (default `DEFAULT_MAX_SOURCE_CHARS`, 200,000); `details={"limit": n}` |
| `MarkdownArchiveError` | 422 | `MARKDOWN_ARCHIVE_INVALID` | archive refused; `details={"reason": ...}`, table above |
| `AssetRefused` | 422 | `PDF_ASSET_REFUSED` | the Markdown referenced something not allowed; `details={"refused_count": n}` |

`max_chars=None` on `parse_slides`, `parse_answer_key` or the
`MarkdownPdfRenderer` disables the limit. None of these errors echoes the
refused text. Elsewhere in the SDK, `AssetRefused` lists the refused URLs
under `details["refused"]`; raised from Markdown, it carries only
`details={"refused_count": n}` — the URL is the user's own text echoed back
and, inside a zip, would disclose the server's temporary directory path.
WeasyPrint stops at the first refusal, so `n` is 1 in practice: measured with
two refused references in one document, `{"refused_count": 1}`.

!!! warning "Without the extra it is an `ImportError`, not an envelope"
    The parser and the schemas import with no dependency, but the first
    render without `[slides]` installed raises `ImportError` — not an
    `AppException` — and the route answers 500. The message names the extra,
    the same for HTML, PDF, PPTX, DOCX and Markdown to PDF:
    `Rendering slides needs python-pptx. Install the extra: pip install
    "tempest-fastapi-sdk[slides]"` (with `jinja2`, `weasyprint`,
    `python-docx` or `markdown` in place of the missing package). It is an
    installation defect, not an input one: install the extra rather than
    handling the error in the route.

## Serving over HTTP

```python
# src/api/app.py

from fastapi import FastAPI, Response
from pydantic import Field

from tempest_fastapi_sdk.api import register_exception_handlers
from tempest_fastapi_sdk.exceptions import default_message_catalog
from tempest_fastapi_sdk.pdf import PDF_MEDIA_TYPE
from tempest_fastapi_sdk.schemas import BaseSchema
from tempest_fastapi_sdk.slides import (
    ABNT_MARKDOWN_CSS,
    MarkdownPdf,
    MarkdownPdfRenderer,
    SlideDeck,
    SlideRenderer,
    SlideThemeEnum,
    SlideVocabulary,
    parse_slides,
)
from tempest_fastapi_sdk.utils import PPTX_MEDIA_TYPE, build_content_disposition

PT_BR: SlideVocabulary = SlideVocabulary(
    lesson_pattern=r"^Aula\s+(\d+)\s*:\s*(.+?)\s*$",
    notes_pattern=r"^(?:Notas|Nota)\s*:\s*(.+)$",
    conclusion_marker="conclus",
    lesson_label="Aula {index}",
    conclusion_label="Conclusão",
    language="pt-BR",
)

slides: SlideRenderer = SlideRenderer(vocabulary=PT_BR)
markdown_pdf: MarkdownPdfRenderer = MarkdownPdfRenderer(stylesheet=ABNT_MARKDOWN_CSS)


class SlideSourceSchema(BaseSchema):
    """The structured text of a lesson."""

    text: str = Field(title="Text", description="Source in the lesson/section/bullet format.")
    theme: SlideThemeEnum = Field(default=SlideThemeEnum.DEFAULT, title="Theme")


class MarkdownSourceSchema(BaseSchema):
    """A Markdown document."""

    text: str = Field(title="Text", description="The document's Markdown.")
    title: str | None = Field(default=None, title="Title")


app = FastAPI()
register_exception_handlers(app, catalog=default_message_catalog())


@app.post("/api/slides/pptx")
async def slides_pptx(payload: SlideSourceSchema) -> Response:
    """Return the lesson as a PowerPoint presentation."""
    deck: SlideDeck = parse_slides(payload.text, vocabulary=PT_BR)
    content: bytes = await slides.pptx(deck, theme=payload.theme)
    return Response(
        content=content,
        media_type=PPTX_MEDIA_TYPE,
        headers={"Content-Disposition": build_content_disposition(f"{deck.title}.pptx")},
    )


@app.post("/api/markdown/pdf")
async def markdown_to_pdf(payload: MarkdownSourceSchema) -> Response:
    """Return the Markdown laid out as an ABNT PDF."""
    document: MarkdownPdf = await markdown_pdf.render(payload.text, title=payload.title)
    return Response(
        content=document.content,
        media_type=PDF_MEDIA_TYPE,
        headers={"Content-Disposition": build_content_disposition(f"{document.title}.pdf")},
    )
```

Measured with `TestClient`:

```text
POST /api/slides/pptx {"text": "Aula 1: Portas lógicas\n..."}
200  application/vnd.openxmlformats-officedocument.presentationml.presentation
     attachment; filename="Portas lgicas.pptx"; filename*=UTF-8''Portas%20l%C3%B3gicas.pptx

POST /api/slides/pptx {"text": "   "}   (Accept-Language: en-US)
422  {"detail": "The source text is empty", "code": "SLIDES_SOURCE_EMPTY", "details": {}}

POST /api/slides/pptx {"text": "   "}   (Accept-Language: pt-BR)
422  {"detail": "O texto de origem está vazio", "code": "SLIDES_SOURCE_EMPTY", "details": {}}

POST /api/markdown/pdf {"text": "# Introdução\n\nTexto.", "title": "TCC"}
200  application/pdf
     attachment; filename="TCC.pdf"; filename*=UTF-8''TCC.pdf
```

Piece by piece:

- **The renderers are built once**, at module level: they hold the
  vocabulary, the stylesheet and the `max_concurrent` semaphore, which only
  limits anything when it is shared across requests.
- **`PPTX_MEDIA_TYPE` / `DOCX_MEDIA_TYPE`** come from
  `tempest_fastapi_sdk.utils`, and `PDF_MEDIA_TYPE` from
  `tempest_fastapi_sdk.pdf`. A `python:slim` image has no `/etc/mime.types`,
  and guessing through `mimetypes` serves the `.pptx` as
  `application/octet-stream` in production only.
- **`build_content_disposition`** builds the header from the title, which is
  user text: it drops control characters, so a line break cannot split the
  response, and sends the UTF-8 name in `filename*`.
- **No `try`/`except`.** The parser's error already is the right response.

!!! warning "The route adds no auth or limit on its own"
    Producing PPTX and PDF is CPU, and the client holds the connection until
    it finishes. Put your service's authentication and rate limit on these
    routes — a document route without them is the one that ends up public.

??? info "Coming from Leviathan's `slide-generator`"
    The module was ported from that service. What changed on the way:

    - **Escaping in the HTML.** The answer-key template marked question and
      answer `| safe`, and a `<script>` in the text ran inside whatever page
      embedded the preview. Here everything is escaped, and tags are stripped
      first.
    - **Asset policy.** Markdown to PDF used WeasyPrint's default fetcher,
      which read `file://` from the host and requested any `http://`. Now raw
      text fetches nothing and an archive reads only what it carried.
    - **Archive bounds.** Entry count and uncompressed size are checked
      before extracting.
    - **ABNT margins.** The original had `margin: 3cm 2cm` — 3 cm at the top
      *and bottom*, 2 cm on the sides. NBR 14724 asks for 3 cm top and left,
      2 cm bottom and right, which is what `ABNT_MARKDOWN_CSS` applies.
    - **Pinned Mermaid.** The original loaded `mermaid@10` (a floating
      version) and opened one browser per diagram; here it is
      `mermaid@10.9.8` and one browser per document.
    - **Vocabulary.** The markers were pt-BR only and hard-coded; now they are
      `SlideVocabulary` / `AnswerKeyVocabulary`, with English defaults.

## Recap

- `parse_slides` reads the text into a `SlideDeck`; `SlideRenderer` writes
  HTML and Markdown (sync) and PDF, PPTX and DOCX (`async`, in a worker
  thread, with `max_concurrent`).
- Markers and labels live in `SlideVocabulary` — English by default.
  Portuguese text asks for your own, passed to the parser **and** the
  renderer.
- A large section becomes more than one slide: 6 bullets or 700 characters of
  paragraph per slide, never cutting a paragraph.
- `parse_answer_key` + `AnswerKeyRenderer` do the same for answer keys; HTML
  tags in the text are stripped in every format.
- `MarkdownPdfRenderer` lays Markdown out with the sheet you pass —
  `DEFAULT_MARKDOWN_CSS`, `ABNT_MARKDOWN_CSS` or your own.
- Raw text fetches no URL; an archive reads only what it carried and is
  checked before extraction.
- Mermaid needs `[slides-mermaid]` + `playwright install chromium`; a failing
  diagram becomes an error box, and the PDF still comes out.
- Errors are `AppException`s with a `code` — let them propagate.

Next: [PDF generation](pdf.md) for typed documents (receipt, quote, report),
or [Spreadsheets (.xlsx)](spreadsheets.md) when the recipient needs to work
with the numbers.
