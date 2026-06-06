"""Shared utility helpers exposed at the module level.

The feature-rich helpers each require an optional extra
(:class:`PasswordUtils` → ``[auth]``, :class:`JWTUtils` → ``[auth]``,
:class:`EmailUtils` → ``[email]``, :class:`UploadUtils` → ``[upload]``,
:class:`MetricsUtils` → ``[metrics]``). The missing dependency is
deferred until first instantiation, so ``import tempest_fastapi_sdk``
keeps working when only a subset of extras is installed; the
``ImportError`` is raised with a clear hint the moment the helper is
actually constructed.
"""

from tempest_fastapi_sdk.utils.client_ip import (
    get_client_ip,
    get_client_ip_from_scope,
)
from tempest_fastapi_sdk.utils.datetime import to_utc, utcnow
from tempest_fastapi_sdk.utils.dict import modify_dict
from tempest_fastapi_sdk.utils.download import (
    DownloadUtils,
    build_content_disposition,
)
from tempest_fastapi_sdk.utils.email import EmailUtils
from tempest_fastapi_sdk.utils.http_client import (
    REQUEST_ID_HEADER,
    CircuitOpenError,
    HTTPClient,
    RetryPolicy,
)
from tempest_fastapi_sdk.utils.jwt import JWTUtils
from tempest_fastapi_sdk.utils.log import LogUtils
from tempest_fastapi_sdk.utils.metrics import (
    CPUMetrics,
    DiskMetrics,
    GPUMetrics,
    MemoryMetrics,
    MetricsUtils,
    SystemMetrics,
)
from tempest_fastapi_sdk.utils.opaque_token import (
    generate_opaque_token,
    hash_opaque_token,
    verify_opaque_token,
)
from tempest_fastapi_sdk.utils.password import PasswordUtils
from tempest_fastapi_sdk.utils.regex import (
    CEP,
    CEP_PATTERN,
    CNPJ,
    CNPJ_PATTERN,
    CPF,
    CPF_CNPJ_PATTERN,
    CPF_PATTERN,
    PHONE_BR_PATTERN,
    CPFOrCNPJ,
    PhoneBR,
    is_valid_cep,
    is_valid_cnpj,
    is_valid_cpf,
    is_valid_cpf_cnpj,
    is_valid_phone_br,
    normalize_cep,
    normalize_cnpj,
    normalize_cpf,
    normalize_cpf_cnpj,
    normalize_phone_br,
    only_digits,
)
from tempest_fastapi_sdk.utils.storage_backends import (
    LocalUploadStorage,
    MinIOUploadStorage,
    UploadResult,
    UploadStorage,
)
from tempest_fastapi_sdk.utils.throttle import (
    AttemptThrottle,
    ThrottleBackend,
    ThrottleStatus,
)
from tempest_fastapi_sdk.utils.totp import TOTPHelper
from tempest_fastapi_sdk.utils.upload import UploadUtils, sniff_mime

__all__: list[str] = [
    "CEP",
    "CEP_PATTERN",
    "CNPJ",
    "CNPJ_PATTERN",
    "CPF",
    "CPF_CNPJ_PATTERN",
    "CPF_PATTERN",
    "PHONE_BR_PATTERN",
    "REQUEST_ID_HEADER",
    "AttemptThrottle",
    "CPFOrCNPJ",
    "CPUMetrics",
    "CircuitOpenError",
    "DiskMetrics",
    "DownloadUtils",
    "EmailUtils",
    "GPUMetrics",
    "HTTPClient",
    "JWTUtils",
    "LocalUploadStorage",
    "LogUtils",
    "MemoryMetrics",
    "MetricsUtils",
    "MinIOUploadStorage",
    "PasswordUtils",
    "PhoneBR",
    "RetryPolicy",
    "SystemMetrics",
    "TOTPHelper",
    "ThrottleBackend",
    "ThrottleStatus",
    "UploadResult",
    "UploadStorage",
    "UploadUtils",
    "build_content_disposition",
    "generate_opaque_token",
    "get_client_ip",
    "get_client_ip_from_scope",
    "hash_opaque_token",
    "is_valid_cep",
    "is_valid_cnpj",
    "is_valid_cpf",
    "is_valid_cpf_cnpj",
    "is_valid_phone_br",
    "modify_dict",
    "normalize_cep",
    "normalize_cnpj",
    "normalize_cpf",
    "normalize_cpf_cnpj",
    "normalize_phone_br",
    "only_digits",
    "sniff_mime",
    "to_utc",
    "utcnow",
    "verify_opaque_token",
]
