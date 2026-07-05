"""Regex patterns, validators and Pydantic types for common BR fields.

This module ships ready-to-use building blocks for the identity and
contact fields that show up in almost every Brazilian API:

* :data:`CPF_PATTERN`, :data:`CNPJ_PATTERN`, :data:`CPF_CNPJ_PATTERN`
  and :data:`PHONE_BR_PATTERN` for raw regex matching (masked or
  unmasked).
* :func:`is_valid_cpf`, :func:`is_valid_cnpj`, :func:`is_valid_cpf_cnpj`
  and :func:`is_valid_phone_br` for full validation (format + check
  digits where applicable).
* :func:`only_digits` and the ``normalize_*`` helpers for stripping
  masks down to a canonical digits-only representation.
* :data:`CPF`, :data:`CNPJ`, :data:`CPFOrCNPJ` and :data:`PhoneBR`
  annotated types ready to drop into Pydantic schema fields.
"""

import re
from typing import Annotated, Final

from pydantic import AfterValidator

from tempest_fastapi_sdk.core import BaseStrEnum

CPF_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\d{3}\.?\d{3}\.?\d{3}-?\d{2}$",
)
"""Match a CPF in either masked (``000.000.000-00``) or raw form."""

CNPJ_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}$",
)
"""Match a CNPJ in either masked (``00.000.000/0000-00``) or raw form."""

CPF_CNPJ_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"(?:{CPF_PATTERN.pattern[1:-1]})|(?:{CNPJ_PATTERN.pattern[1:-1]})",
)
"""Match either a CPF or a CNPJ (masked or raw)."""

PHONE_BR_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?:\+?55\s?)?(?:\(?\d{2}\)?\s?)?9?\d{4}[-\s]?\d{4}$",
)
"""Match a BR phone number with optional ``+55``, DDD, mask or 9th digit."""

CEP_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\d{5}-?\d{3}$",
)
"""Match a Brazilian CEP in either masked (``00000-000``) or raw form."""


def only_digits(value: str) -> str:
    """Strip every non-digit character from ``value``.

    Args:
        value (str): The raw input (masked CPF, phone, etc.).

    Returns:
        str: A string containing only the digits found in ``value``.
    """
    return re.sub(r"\D", "", value)


def _cpf_check_digits(digits: str) -> bool:
    """Validate the two CPF check digits.

    Args:
        digits (str): An 11-character digits-only CPF string.

    Returns:
        bool: ``True`` when both check digits are correct.
    """
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for size in (9, 10):
        total = sum(int(digits[i]) * ((size + 1) - i) for i in range(size))
        check = (total * 10) % 11
        if check == 10:
            check = 0
        if check != int(digits[size]):
            return False
    return True


def _cnpj_check_digits(digits: str) -> bool:
    """Validate the two CNPJ check digits.

    Args:
        digits (str): A 14-character digits-only CNPJ string.

    Returns:
        bool: ``True`` when both check digits are correct.
    """
    if len(digits) != 14 or len(set(digits)) == 1:
        return False
    weights_first: list[int] = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    weights_second: list[int] = [6, *weights_first]
    for size, weights in ((12, weights_first), (13, weights_second)):
        total = sum(int(digits[i]) * weights[i] for i in range(size))
        rest = total % 11
        check = 0 if rest < 2 else 11 - rest
        if check != int(digits[size]):
            return False
    return True


def is_valid_cpf(value: str) -> bool:
    """Check whether ``value`` is a syntactically valid CPF.

    Accepts both masked (``000.000.000-00``) and unmasked input.
    Sequences with all repeated digits (e.g. ``"11111111111"``) are
    rejected even though they pass the modulo math.

    Args:
        value (str): The CPF string to inspect.

    Returns:
        bool: ``True`` when the value matches the CPF pattern and
        has correct check digits.
    """
    if not CPF_PATTERN.fullmatch(value):
        return False
    return _cpf_check_digits(only_digits(value))


def is_valid_cnpj(value: str) -> bool:
    """Check whether ``value`` is a syntactically valid CNPJ.

    Accepts both masked (``00.000.000/0000-00``) and unmasked input.
    Sequences with all repeated digits are rejected.

    Args:
        value (str): The CNPJ string to inspect.

    Returns:
        bool: ``True`` when the value matches the CNPJ pattern and
        has correct check digits.
    """
    if not CNPJ_PATTERN.fullmatch(value):
        return False
    return _cnpj_check_digits(only_digits(value))


def is_valid_cpf_cnpj(value: str) -> bool:
    """Check whether ``value`` is a valid CPF or CNPJ.

    Args:
        value (str): The document string to inspect.

    Returns:
        bool: ``True`` when the value passes :func:`is_valid_cpf`
        or :func:`is_valid_cnpj`.
    """
    digits = only_digits(value)
    if len(digits) == 11:
        return is_valid_cpf(value)
    if len(digits) == 14:
        return is_valid_cnpj(value)
    return False


def is_valid_cep(value: str) -> bool:
    """Check whether ``value`` looks like a Brazilian CEP.

    CEPs have no check digits — validation only enforces the
    eight-digit shape (with or without the canonical ``00000-000``
    mask). Use the official Correios API for existence checks.

    Args:
        value (str): The CEP string to inspect.

    Returns:
        bool: ``True`` when the value matches the CEP pattern.
    """
    return CEP_PATTERN.fullmatch(value) is not None


def is_valid_phone_br(value: str) -> bool:
    """Check whether ``value`` looks like a Brazilian phone number.

    Accepts optional ``+55`` country code, optional DDD parentheses
    and an optional 9th digit for mobile numbers. After stripping
    non-digits the remaining length must be 10 (landline) or 11
    (mobile), with an optional leading ``55`` country code.

    Args:
        value (str): The phone string to inspect.

    Returns:
        bool: ``True`` when the value matches a BR phone shape.
    """
    if not PHONE_BR_PATTERN.fullmatch(value):
        return False
    digits = only_digits(value)
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]
    return len(digits) in (10, 11)


def normalize_cpf(value: str) -> str:
    """Return ``value`` as 11 digits, validating along the way.

    Args:
        value (str): The CPF string (masked or unmasked).

    Returns:
        str: The CPF stripped down to 11 digits.

    Raises:
        ValueError: If ``value`` is not a valid CPF.
    """
    if not is_valid_cpf(value):
        raise ValueError("invalid CPF")
    return only_digits(value)


def normalize_cnpj(value: str) -> str:
    """Return ``value`` as 14 digits, validating along the way.

    Args:
        value (str): The CNPJ string (masked or unmasked).

    Returns:
        str: The CNPJ stripped down to 14 digits.

    Raises:
        ValueError: If ``value`` is not a valid CNPJ.
    """
    if not is_valid_cnpj(value):
        raise ValueError("invalid CNPJ")
    return only_digits(value)


def normalize_cpf_cnpj(value: str) -> str:
    """Return ``value`` as 11 or 14 digits, validating along the way.

    Args:
        value (str): The document string (masked or unmasked).

    Returns:
        str: The document stripped down to digits only.

    Raises:
        ValueError: If ``value`` is not a valid CPF nor CNPJ.
    """
    if not is_valid_cpf_cnpj(value):
        raise ValueError("invalid CPF/CNPJ")
    return only_digits(value)


def normalize_cep(value: str) -> str:
    """Return ``value`` as 8 digits, validating along the way.

    Args:
        value (str): The CEP string (masked or unmasked).

    Returns:
        str: The CEP stripped down to 8 digits.

    Raises:
        ValueError: If ``value`` is not a valid CEP.
    """
    if not is_valid_cep(value):
        raise ValueError("invalid CEP")
    return only_digits(value)


def normalize_phone_br(value: str) -> str:
    """Return ``value`` as digits-only with an optional ``55`` prefix.

    Args:
        value (str): The phone string (masked or unmasked).

    Returns:
        str: The phone stripped down to digits only.

    Raises:
        ValueError: If ``value`` does not look like a BR phone.
    """
    if not is_valid_phone_br(value):
        raise ValueError("invalid BR phone")
    return only_digits(value)


_PIX_EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
)
"""Loose email shape for a PIX e-mail key (BACEN checks format, not MX)."""

_PIX_PHONE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\+55\d{10,11}$",
)
"""PIX phone key in E.164: ``+55`` + DDD + 8/9-digit number."""

_PIX_RANDOM_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
)
"""PIX random key (EVP): a UUID (``8-4-4-4-12`` hex)."""


class PixKeyType(BaseStrEnum):
    """The five kinds of PIX key defined by BACEN.

    * ``CPF`` — an 11-digit CPF (individual taxpayer id).
    * ``CNPJ`` — a 14-digit CNPJ (company taxpayer id).
    * ``EMAIL`` — an e-mail address.
    * ``PHONE`` — an E.164 phone (``+55`` + DDD + number).
    * ``RANDOM`` — a random key (EVP), i.e. a UUID.
    """

    CPF = "cpf"
    CNPJ = "cnpj"
    EMAIL = "email"
    PHONE = "phone"
    RANDOM = "random"


def detect_pix_key_type(value: str) -> PixKeyType | None:
    """Return the :class:`PixKeyType` a PIX key belongs to, or ``None``.

    Detection is by shape: ``@`` → e-mail, leading ``+`` → phone, UUID
    shape → random, otherwise digits-only length + check digits decide
    between CPF and CNPJ.

    Args:
        value (str): The raw PIX key (masked CPF/CNPJ accepted).

    Returns:
        PixKeyType | None: The detected type, or ``None`` when ``value``
        is not a valid key of any type.
    """
    candidate = value.strip()
    if not candidate:
        return None
    if "@" in candidate:
        return PixKeyType.EMAIL if _PIX_EMAIL_PATTERN.match(candidate) else None
    if candidate.startswith("+"):
        return PixKeyType.PHONE if _PIX_PHONE_PATTERN.match(candidate) else None
    if _PIX_RANDOM_PATTERN.match(candidate):
        return PixKeyType.RANDOM
    digits = only_digits(candidate)
    if len(digits) == 11 and is_valid_cpf(digits):
        return PixKeyType.CPF
    if len(digits) == 14 and is_valid_cnpj(digits):
        return PixKeyType.CNPJ
    return None


def is_valid_pix_key(value: str) -> bool:
    """Return ``True`` when ``value`` is a valid PIX key of any type.

    Args:
        value (str): The PIX key to check.

    Returns:
        bool: Whether the value is a recognizable, valid PIX key.
    """
    return detect_pix_key_type(value) is not None


def normalize_pix_key(value: str) -> str:
    """Validate a PIX key and return it in canonical form.

    Canonicalization per type: CPF/CNPJ → digits only; e-mail → trimmed +
    lowercased; phone → the E.164 ``+55…`` string; random → lowercased
    UUID.

    Args:
        value (str): The raw PIX key.

    Returns:
        str: The normalized key.

    Raises:
        ValueError: When ``value`` is not a valid PIX key.
    """
    key_type = detect_pix_key_type(value)
    if key_type is None:
        raise ValueError("invalid PIX key")
    candidate = value.strip()
    if key_type in (PixKeyType.CPF, PixKeyType.CNPJ):
        return only_digits(candidate)
    if key_type is PixKeyType.PHONE:
        return candidate
    return candidate.lower()


CPFField = Annotated[str, AfterValidator(normalize_cpf)]
"""Pydantic field type that validates and normalizes a CPF to 11 digits."""

CNPJField = Annotated[str, AfterValidator(normalize_cnpj)]
"""Pydantic field type that validates and normalizes a CNPJ to 14 digits."""

CPFOrCNPJField = Annotated[str, AfterValidator(normalize_cpf_cnpj)]
"""Pydantic field type that accepts either a CPF or a CNPJ."""

PhoneBRField = Annotated[str, AfterValidator(normalize_phone_br)]
"""Pydantic field type that validates a BR phone and normalizes to digits."""

CEPField = Annotated[str, AfterValidator(normalize_cep)]
"""Pydantic field type that validates a Brazilian CEP, normalized to 8 digits."""

PixKeyField = Annotated[str, AfterValidator(normalize_pix_key)]
"""Pydantic field type that validates any PIX key and normalizes it.

Accepts a CPF, CNPJ, e-mail, E.164 phone (``+55…``) or random UUID key;
raises ``ValidationError`` (HTTP 422) on anything else. Use
:func:`detect_pix_key_type` when you also need to know *which* type.
"""

# Deprecated aliases (pre-0.76 names without the ``Field`` suffix). Kept
# so existing imports keep working; prefer the ``*Field`` names. Slated
# for removal in a future major.
CPF = CPFField
CNPJ = CNPJField
CPFOrCNPJ = CPFOrCNPJField
PhoneBR = PhoneBRField
CEP = CEPField


__all__: list[str] = [
    "CEP",
    "CEP_PATTERN",
    "CNPJ",
    "CNPJ_PATTERN",
    "CPF",
    "CPF_CNPJ_PATTERN",
    "CPF_PATTERN",
    "PHONE_BR_PATTERN",
    "CEPField",
    "CNPJField",
    "CPFField",
    "CPFOrCNPJ",
    "CPFOrCNPJField",
    "PhoneBR",
    "PhoneBRField",
    "PixKeyField",
    "PixKeyType",
    "detect_pix_key_type",
    "is_valid_cep",
    "is_valid_cnpj",
    "is_valid_cpf",
    "is_valid_cpf_cnpj",
    "is_valid_phone_br",
    "is_valid_pix_key",
    "normalize_cep",
    "normalize_cnpj",
    "normalize_cpf",
    "normalize_cpf_cnpj",
    "normalize_phone_br",
    "normalize_pix_key",
    "only_digits",
]
