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


CPF = Annotated[str, AfterValidator(normalize_cpf)]
"""Pydantic type that validates and normalizes a CPF to 11 digits."""

CNPJ = Annotated[str, AfterValidator(normalize_cnpj)]
"""Pydantic type that validates and normalizes a CNPJ to 14 digits."""

CPFOrCNPJ = Annotated[str, AfterValidator(normalize_cpf_cnpj)]
"""Pydantic type that accepts either a CPF or a CNPJ."""

PhoneBR = Annotated[str, AfterValidator(normalize_phone_br)]
"""Pydantic type that validates a BR phone and normalizes to digits."""


__all__: list[str] = [
    "CNPJ",
    "CNPJ_PATTERN",
    "CPF",
    "CPF_CNPJ_PATTERN",
    "CPF_PATTERN",
    "PHONE_BR_PATTERN",
    "CPFOrCNPJ",
    "PhoneBR",
    "is_valid_cnpj",
    "is_valid_cpf",
    "is_valid_cpf_cnpj",
    "is_valid_phone_br",
    "normalize_cnpj",
    "normalize_cpf",
    "normalize_cpf_cnpj",
    "normalize_phone_br",
    "only_digits",
]
