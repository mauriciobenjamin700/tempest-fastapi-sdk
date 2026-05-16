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

from tempest_fastapi_sdk.utils.datetime import to_utc, utcnow
from tempest_fastapi_sdk.utils.dict import modify_dict
from tempest_fastapi_sdk.utils.email import EmailUtils
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
from tempest_fastapi_sdk.utils.password import PasswordUtils
from tempest_fastapi_sdk.utils.regex import (
    CNPJ,
    CNPJ_PATTERN,
    CPF,
    CPF_CNPJ_PATTERN,
    CPF_PATTERN,
    PHONE_BR_PATTERN,
    CPFOrCNPJ,
    PhoneBR,
    is_valid_cnpj,
    is_valid_cpf,
    is_valid_cpf_cnpj,
    is_valid_phone_br,
    normalize_cnpj,
    normalize_cpf,
    normalize_cpf_cnpj,
    normalize_phone_br,
    only_digits,
)
from tempest_fastapi_sdk.utils.upload import UploadUtils

__all__: list[str] = [
    "CNPJ",
    "CNPJ_PATTERN",
    "CPF",
    "CPF_CNPJ_PATTERN",
    "CPF_PATTERN",
    "PHONE_BR_PATTERN",
    "CPFOrCNPJ",
    "CPUMetrics",
    "DiskMetrics",
    "EmailUtils",
    "GPUMetrics",
    "JWTUtils",
    "LogUtils",
    "MemoryMetrics",
    "MetricsUtils",
    "PasswordUtils",
    "PhoneBR",
    "SystemMetrics",
    "UploadUtils",
    "is_valid_cnpj",
    "is_valid_cpf",
    "is_valid_cpf_cnpj",
    "is_valid_phone_br",
    "modify_dict",
    "normalize_cnpj",
    "normalize_cpf",
    "normalize_cpf_cnpj",
    "normalize_phone_br",
    "only_digits",
    "to_utc",
    "utcnow",
]
