---
description: "Primitivas transversais: tipagem estrita, logging, redação de dados sensíveis e locale da documentação."
---

# `tempest_fastapi_sdk.core`

Primitivas transversais: tipagem estrita, logging, redação de dados sensíveis e locale da documentação.

[« Admin, dados e API](../admin-data-api.md)

---

::: tempest_fastapi_sdk.core.typed.strict_types
::: tempest_fastapi_sdk.core.typed.typed
::: tempest_fastapi_sdk.core.typed.require_annotations
::: tempest_fastapi_sdk.core.docs_locale.normalize_docs_locale
::: tempest_fastapi_sdk.core.docs_locale.DocsLocale
::: tempest_fastapi_sdk.core.logging.configure_root_once
::: tempest_fastapi_sdk.core.logging.reinitialize_logging
::: tempest_fastapi_sdk.core.redaction.RedactionPolicy
::: tempest_fastapi_sdk.core.redaction.RedactionFilter
::: tempest_fastapi_sdk.core.redaction.REDACTED
::: tempest_fastapi_sdk.core.redaction.DEFAULT_REDACT_KEYS
::: tempest_fastapi_sdk.core.redaction.DEFAULT_REDACT_PATTERNS
::: tempest_fastapi_sdk.core.redaction.EMAIL_PATTERN
::: tempest_fastapi_sdk.core.redaction.BEARER_PATTERN
::: tempest_fastapi_sdk.core.redaction.JWT_PATTERN
::: tempest_fastapi_sdk.core.redaction.CAPABILITY_PATH_PATTERN
::: tempest_fastapi_sdk.core.redaction.CAPABILITY_PATH_SEGMENTS
::: tempest_fastapi_sdk.core.redaction.CAPABILITY_QUERY_PATTERN
::: tempest_fastapi_sdk.core.redaction.redact_capability_url

::: tempest_fastapi_sdk.core.context.clear_request_id
::: tempest_fastapi_sdk.core.context.get_request_id
::: tempest_fastapi_sdk.core.context.request_id_ctx
::: tempest_fastapi_sdk.core.context.set_request_id
::: tempest_fastapi_sdk.core.enums.BaseIntEnum
::: tempest_fastapi_sdk.core.enums.BaseStrEnum
::: tempest_fastapi_sdk.core.enums.LocaleEnum
::: tempest_fastapi_sdk.core.enums.normalize_locale_tag
::: tempest_fastapi_sdk.core.logging.configure_logging
::: tempest_fastapi_sdk.core.logging.DEFAULT_LOG_BACKUP_COUNT
::: tempest_fastapi_sdk.core.logging.DEFAULT_LOG_MAX_BYTES
::: tempest_fastapi_sdk.core.logging.HTTP_500_MARKER
::: tempest_fastapi_sdk.core.logging.JSONFormatter
::: tempest_fastapi_sdk.core.logging.LOG_ROTATE_WHEN_CHOICES
::: tempest_fastapi_sdk.core.logging.validate_rotate_when
