"""Shared utility helpers exposed at the module level.

The four feature-rich helpers — :class:`PasswordUtils`,
:class:`JWTUtils`, :class:`EmailUtils` and :class:`UploadUtils` —
each require an optional extra (``[auth]``, ``[email]`` or
``[upload]``). Importing this package eagerly imports them, so the
missing extra surfaces immediately with a clear ``ImportError``.
"""

from tempest_fastapi_sdk.utils.datetime import to_utc, utcnow
from tempest_fastapi_sdk.utils.dict import modify_dict
from tempest_fastapi_sdk.utils.email import EmailUtils
from tempest_fastapi_sdk.utils.jwt import JWTUtils
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
    "EmailUtils",
    "JWTUtils",
    "PasswordUtils",
    "PhoneBR",
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
