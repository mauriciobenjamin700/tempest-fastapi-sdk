# Referência: Documentos e arquivos

PDF, planilhas, slides, exportação e apagamento de dados (LGPD), registro de artefatos e a prévia de compartilhamento.

[« Índice da referência](../reference.md)

---

## PDF

### `tempest_fastapi_sdk.pdf`

::: tempest_fastapi_sdk.pdf.renderer.PdfRenderer
::: tempest_fastapi_sdk.pdf.renderer.TemplateNotFound
::: tempest_fastapi_sdk.pdf.renderer.bundled_document_names
::: tempest_fastapi_sdk.pdf.renderer.document_schema
::: tempest_fastapi_sdk.pdf.renderer.BUNDLED_TEMPLATE_DIR
::: tempest_fastapi_sdk.pdf.renderer.DEFAULT_MAX_CONCURRENT_RENDERS
::: tempest_fastapi_sdk.pdf.assets.AssetPolicy
::: tempest_fastapi_sdk.pdf.assets.AssetRefused
::: tempest_fastapi_sdk.pdf.assets.build_url_fetcher
::: tempest_fastapi_sdk.pdf.assets.DEFAULT_MAX_ASSET_BYTES
::: tempest_fastapi_sdk.pdf.assets.DEFAULT_REMOTE_TIMEOUT
::: tempest_fastapi_sdk.pdf.documents.PdfDocument
::: tempest_fastapi_sdk.pdf.documents.ReceiptDocument
::: tempest_fastapi_sdk.pdf.documents.QuoteDocument
::: tempest_fastapi_sdk.pdf.documents.ReportDocument
::: tempest_fastapi_sdk.pdf.documents.ContractDocument
::: tempest_fastapi_sdk.pdf.documents.VoucherDocument
::: tempest_fastapi_sdk.pdf.documents.Party
::: tempest_fastapi_sdk.pdf.documents.Branding
::: tempest_fastapi_sdk.pdf.documents.LineItem
::: tempest_fastapi_sdk.pdf.documents.Clause
::: tempest_fastapi_sdk.pdf.documents.Signatory
::: tempest_fastapi_sdk.pdf.documents.ReportColumn
::: tempest_fastapi_sdk.pdf.documents.BUNDLED_DOCUMENTS
::: tempest_fastapi_sdk.pdf.qr.qr_data_uri
::: tempest_fastapi_sdk.pdf.qr.QrErrorLevel
::: tempest_fastapi_sdk.pdf.qr.QR_ERROR_CORRECTION
::: tempest_fastapi_sdk.pdf.qr.QR_SCALE
::: tempest_fastapi_sdk.pdf.qr.QR_BORDER
::: tempest_fastapi_sdk.pdf.formatting.format_cents
::: tempest_fastapi_sdk.pdf.formatting.format_date
::: tempest_fastapi_sdk.pdf.formatting.format_date_long
::: tempest_fastapi_sdk.pdf.formatting.format_document
::: tempest_fastapi_sdk.pdf.formatting.format_quantity
::: tempest_fastapi_sdk.pdf.formatting.valor_por_extenso
::: tempest_fastapi_sdk.pdf.formatting.MAX_EXTENSO_CENTS
::: tempest_fastapi_sdk.pdf.formatting.MONTHS_PT_BR
::: tempest_fastapi_sdk.pdf.router.make_pdf_router
::: tempest_fastapi_sdk.pdf.router.safe_filename
::: tempest_fastapi_sdk.pdf.router.DocumentRequestSchema
::: tempest_fastapi_sdk.pdf.router.DocumentListSchema
::: tempest_fastapi_sdk.pdf.router.PDF_MEDIA_TYPE
::: tempest_fastapi_sdk.pdf.reader.extract_pdf_pages
::: tempest_fastapi_sdk.pdf.reader.extract_pdf_text
::: tempest_fastapi_sdk.pdf.reader.PageText
::: tempest_fastapi_sdk.pdf.reader.DEFAULT_PAGE_MARKER
::: tempest_fastapi_sdk.pdf.reader.DEFAULT_TRUNCATION_NOTICE
::: tempest_fastapi_sdk.pdf.reader.DEFAULT_PARTIAL_PAGE_NOTICE
::: tempest_fastapi_sdk.pdf.pages.read_pdf_pages
::: tempest_fastapi_sdk.pdf.pages.PdfExtractorEnum
::: tempest_fastapi_sdk.pdf.pages.PdfPage
::: tempest_fastapi_sdk.pdf.pages.PdfPagesResult
::: tempest_fastapi_sdk.pdf.pages.PdfDecryptError
::: tempest_fastapi_sdk.pdf.pages.PdfExtractError

---

## Planilhas

### `tempest_fastapi_sdk.spreadsheet`

::: tempest_fastapi_sdk.spreadsheet.writer.SheetWriter
::: tempest_fastapi_sdk.spreadsheet.writer.Column
::: tempest_fastapi_sdk.spreadsheet.writer.CellValue
::: tempest_fastapi_sdk.spreadsheet.writer.Formula
::: tempest_fastapi_sdk.spreadsheet.writer.new_workbook
::: tempest_fastapi_sdk.spreadsheet.writer.workbook_to_bytes
::: tempest_fastapi_sdk.spreadsheet.styles.SheetStyle
::: tempest_fastapi_sdk.spreadsheet.styles.DEFAULT_SHEET_STYLE
::: tempest_fastapi_sdk.spreadsheet.formats.BR_CURRENCY_FORMAT
::: tempest_fastapi_sdk.spreadsheet.formats.BR_CURRENCY_FORMAT_NO_SYMBOL
::: tempest_fastapi_sdk.spreadsheet.formats.BR_QUANTITY_FORMAT
::: tempest_fastapi_sdk.spreadsheet.formats.BR_INTEGER_FORMAT
::: tempest_fastapi_sdk.spreadsheet.formats.BR_PERCENT_FORMAT
::: tempest_fastapi_sdk.spreadsheet.formats.BR_DATE_FORMAT
::: tempest_fastapi_sdk.spreadsheet.formats.BR_DATETIME_FORMAT
::: tempest_fastapi_sdk.spreadsheet.formats.TEXT_FORMAT
::: tempest_fastapi_sdk.spreadsheet.google.read_google_sheet
::: tempest_fastapi_sdk.spreadsheet.google.read_google_sheet_as
::: tempest_fastapi_sdk.spreadsheet.google.google_sheet_export_url
::: tempest_fastapi_sdk.spreadsheet.google.GoogleSheetAccessError
::: tempest_fastapi_sdk.spreadsheet.google.GoogleSheetRowError
::: tempest_fastapi_sdk.spreadsheet.google.download_google_sheet_xlsx
::: tempest_fastapi_sdk.spreadsheet.google.read_google_sheet_xlsx
::: tempest_fastapi_sdk.spreadsheet.google.DEFAULT_GOOGLE_SHEET_MAX_DOWNLOAD_BYTES
::: tempest_fastapi_sdk.spreadsheet.google.DEFAULT_GOOGLE_CSV_MAX_DOWNLOAD_BYTES
::: tempest_fastapi_sdk.spreadsheet.reader.read_xlsx
::: tempest_fastapi_sdk.spreadsheet.reader.read_xlsx_as
::: tempest_fastapi_sdk.spreadsheet.reader.read_xlsx_sheets
::: tempest_fastapi_sdk.spreadsheet.reader.read_xlsx_async
::: tempest_fastapi_sdk.spreadsheet.reader.read_xlsx_as_async
::: tempest_fastapi_sdk.spreadsheet.reader.read_xlsx_sheets_async
::: tempest_fastapi_sdk.spreadsheet.reader.XlsxCellValue
::: tempest_fastapi_sdk.spreadsheet.reader.XlsxSource
::: tempest_fastapi_sdk.spreadsheet.reader.InvalidSpreadsheetError
::: tempest_fastapi_sdk.spreadsheet.reader.SheetNotFoundError
::: tempest_fastapi_sdk.spreadsheet.reader.SpreadsheetRowError
::: tempest_fastapi_sdk.spreadsheet.reader.SpreadsheetTooLargeError
::: tempest_fastapi_sdk.spreadsheet.reader.DEFAULT_XLSX_MAX_ROWS
::: tempest_fastapi_sdk.spreadsheet.reader.DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES
::: tempest_fastapi_sdk.spreadsheet.reader.DEFAULT_XLSX_MAX_COMPRESSION_RATIO
::: tempest_fastapi_sdk.spreadsheet.reader.DEFAULT_MAX_CONCURRENT_XLSX_READS

---

## Slides, gabaritos e Markdown em PDF

### `tempest_fastapi_sdk.slides`

::: tempest_fastapi_sdk.slides.parser.parse_slides
::: tempest_fastapi_sdk.slides.parser.parse_answer_key
::: tempest_fastapi_sdk.slides.parser.DEFAULT_MAX_SOURCE_CHARS
::: tempest_fastapi_sdk.slides.renderer.SlideRenderer
::: tempest_fastapi_sdk.slides.renderer.AnswerKeyRenderer
::: tempest_fastapi_sdk.slides.vocabulary.SlideVocabulary
::: tempest_fastapi_sdk.slides.vocabulary.AnswerKeyVocabulary
::: tempest_fastapi_sdk.slides.vocabulary.DEFAULT_SLIDE_VOCABULARY
::: tempest_fastapi_sdk.slides.vocabulary.DEFAULT_ANSWER_KEY_VOCABULARY
::: tempest_fastapi_sdk.slides.vocabulary.DEFAULT_SENTENCE_VERBS
::: tempest_fastapi_sdk.slides.schemas.SlideDeck
::: tempest_fastapi_sdk.slides.schemas.Slide
::: tempest_fastapi_sdk.slides.schemas.SlideBullet
::: tempest_fastapi_sdk.slides.schemas.SlideKindEnum
::: tempest_fastapi_sdk.slides.schemas.SlideThemeEnum
::: tempest_fastapi_sdk.slides.schemas.AnswerKey
::: tempest_fastapi_sdk.slides.schemas.AnswerKeySection
::: tempest_fastapi_sdk.slides.schemas.AnswerKeyItem
::: tempest_fastapi_sdk.slides.markdown_pdf.MarkdownPdfRenderer
::: tempest_fastapi_sdk.slides.markdown_pdf.MarkdownPdf
::: tempest_fastapi_sdk.slides.markdown_pdf.DEFAULT_MAX_ARCHIVE_BYTES
::: tempest_fastapi_sdk.slides.markdown_pdf.DEFAULT_MAX_ARCHIVE_MEMBERS
::: tempest_fastapi_sdk.slides.styles.DEFAULT_MARKDOWN_CSS
::: tempest_fastapi_sdk.slides.styles.ABNT_MARKDOWN_CSS
::: tempest_fastapi_sdk.slides.exceptions.SlideSourceEmptyError
::: tempest_fastapi_sdk.slides.exceptions.SlideSourceTooLargeError
::: tempest_fastapi_sdk.slides.exceptions.MarkdownArchiveError

---

## Privacidade (LGPD)

### `tempest_fastapi_sdk.privacy`

::: tempest_fastapi_sdk.privacy
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

---

## Artefatos (registro de versões)

### `tempest_fastapi_sdk.artifacts`

::: tempest_fastapi_sdk.artifacts.model.ArtifactVersionMixin
::: tempest_fastapi_sdk.artifacts.registry.ArtifactRegistry
::: tempest_fastapi_sdk.artifacts.registry.ArtifactManifestEntry
::: tempest_fastapi_sdk.artifacts.registry.build_manifest_entries
::: tempest_fastapi_sdk.artifacts.digest.file_digest
::: tempest_fastapi_sdk.artifacts.digest.object_digest
::: tempest_fastapi_sdk.artifacts.actions.make_activate_artifact_action

---

## Compartilhamento (Open Graph)

### `tempest_fastapi_sdk.share`

::: tempest_fastapi_sdk.share
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"
