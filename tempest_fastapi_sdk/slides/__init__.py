"""Slide decks, answer keys and Markdown documents from plain text.

Three jobs, one extra:

* **Decks.** :func:`parse_slides` reads structured text (lesson headers,
  short headings, ``Term: description`` bullets, notes lines) into a
  :class:`SlideDeck`; :class:`SlideRenderer` writes it as HTML, PDF,
  PPTX (speaker notes in the notes pane), a DOCX presenter handout, or
  reveal.js Markdown.
* **Answer keys.** :func:`parse_answer_key` reads numbered questions and
  their answers into an :class:`AnswerKey`; :class:`AnswerKeyRenderer`
  writes HTML, PDF, DOCX or Markdown.
* **Markdown to PDF.** :class:`MarkdownPdfRenderer` lays out raw Markdown,
  or a zip of Markdown plus images, with a stylesheet you pass —
  :data:`DEFAULT_MARKDOWN_CSS`, :data:`ABNT_MARKDOWN_CSS` or your own.

The words are not fixed. Markers the parsers read and labels the renderers
print live in :class:`SlideVocabulary` and :class:`AnswerKeyVocabulary`,
English by default; a service writing Portuguese passes its own and gets
``Aula 1:`` parsed and ``Conclusão`` printed::

    from tempest_fastapi_sdk.slides import (
        SlideRenderer,
        SlideVocabulary,
        parse_slides,
    )

    PT_BR = SlideVocabulary(
        lesson_pattern=r"^Aula\\s+(\\d+)\\s*:\\s*(.+?)\\s*$",
        conclusion_marker="conclus",
        lesson_label="Aula {index}",
        conclusion_label="Conclusão",
    )
    deck = parse_slides(text, vocabulary=PT_BR)
    pptx = await SlideRenderer(vocabulary=PT_BR).pptx(deck)

Rendering needs the ``[slides]`` extra (``weasyprint``, ``jinja2``,
``python-pptx``, ``python-docx``, ``markdown``) and, for PDF, the Pango
system libraries :mod:`tempest_fastapi_sdk.pdf` documents. Drawing
```` ```mermaid ```` blocks needs ``[slides-mermaid]`` plus
``playwright install chromium``. Parsing and the schemas need neither:
every engine is imported at first use.

Ported from the ``slide-generator`` service of the Leviathan project.

Re-exports use the PEP 484 ``from x import Y as Y`` explicit re-export
form combined with ``__all__`` so every type-checker accepts
``from tempest_fastapi_sdk.slides import SlideRenderer`` without a
diagnostic.
"""

from tempest_fastapi_sdk.slides.exceptions import (
    MarkdownArchiveError as MarkdownArchiveError,
)
from tempest_fastapi_sdk.slides.exceptions import (
    SlideSourceEmptyError as SlideSourceEmptyError,
)
from tempest_fastapi_sdk.slides.exceptions import (
    SlideSourceTooLargeError as SlideSourceTooLargeError,
)
from tempest_fastapi_sdk.slides.markdown_pdf import (
    DEFAULT_MAX_ARCHIVE_BYTES as DEFAULT_MAX_ARCHIVE_BYTES,
)
from tempest_fastapi_sdk.slides.markdown_pdf import (
    DEFAULT_MAX_ARCHIVE_MEMBERS as DEFAULT_MAX_ARCHIVE_MEMBERS,
)
from tempest_fastapi_sdk.slides.markdown_pdf import MarkdownPdf as MarkdownPdf
from tempest_fastapi_sdk.slides.markdown_pdf import (
    MarkdownPdfRenderer as MarkdownPdfRenderer,
)
from tempest_fastapi_sdk.slides.parser import (
    DEFAULT_MAX_SOURCE_CHARS as DEFAULT_MAX_SOURCE_CHARS,
)
from tempest_fastapi_sdk.slides.parser import parse_answer_key as parse_answer_key
from tempest_fastapi_sdk.slides.parser import parse_slides as parse_slides
from tempest_fastapi_sdk.slides.renderer import AnswerKeyRenderer as AnswerKeyRenderer
from tempest_fastapi_sdk.slides.renderer import SlideRenderer as SlideRenderer
from tempest_fastapi_sdk.slides.schemas import AnswerKey as AnswerKey
from tempest_fastapi_sdk.slides.schemas import AnswerKeyItem as AnswerKeyItem
from tempest_fastapi_sdk.slides.schemas import AnswerKeySection as AnswerKeySection
from tempest_fastapi_sdk.slides.schemas import Slide as Slide
from tempest_fastapi_sdk.slides.schemas import SlideBullet as SlideBullet
from tempest_fastapi_sdk.slides.schemas import SlideDeck as SlideDeck
from tempest_fastapi_sdk.slides.schemas import SlideKind as SlideKind
from tempest_fastapi_sdk.slides.schemas import SlideTheme as SlideTheme
from tempest_fastapi_sdk.slides.styles import ABNT_MARKDOWN_CSS as ABNT_MARKDOWN_CSS
from tempest_fastapi_sdk.slides.styles import (
    DEFAULT_MARKDOWN_CSS as DEFAULT_MARKDOWN_CSS,
)
from tempest_fastapi_sdk.slides.vocabulary import (
    DEFAULT_ANSWER_KEY_VOCABULARY as DEFAULT_ANSWER_KEY_VOCABULARY,
)
from tempest_fastapi_sdk.slides.vocabulary import (
    DEFAULT_SENTENCE_VERBS as DEFAULT_SENTENCE_VERBS,
)
from tempest_fastapi_sdk.slides.vocabulary import (
    DEFAULT_SLIDE_VOCABULARY as DEFAULT_SLIDE_VOCABULARY,
)
from tempest_fastapi_sdk.slides.vocabulary import (
    AnswerKeyVocabulary as AnswerKeyVocabulary,
)
from tempest_fastapi_sdk.slides.vocabulary import SlideVocabulary as SlideVocabulary

__all__: list[str] = [
    "ABNT_MARKDOWN_CSS",
    "DEFAULT_ANSWER_KEY_VOCABULARY",
    "DEFAULT_MARKDOWN_CSS",
    "DEFAULT_MAX_ARCHIVE_BYTES",
    "DEFAULT_MAX_ARCHIVE_MEMBERS",
    "DEFAULT_MAX_SOURCE_CHARS",
    "DEFAULT_SENTENCE_VERBS",
    "DEFAULT_SLIDE_VOCABULARY",
    "AnswerKey",
    "AnswerKeyItem",
    "AnswerKeyRenderer",
    "AnswerKeySection",
    "AnswerKeyVocabulary",
    "MarkdownArchiveError",
    "MarkdownPdf",
    "MarkdownPdfRenderer",
    "Slide",
    "SlideBullet",
    "SlideDeck",
    "SlideKind",
    "SlideRenderer",
    "SlideSourceEmptyError",
    "SlideSourceTooLargeError",
    "SlideTheme",
    "SlideVocabulary",
    "parse_answer_key",
    "parse_slides",
]
