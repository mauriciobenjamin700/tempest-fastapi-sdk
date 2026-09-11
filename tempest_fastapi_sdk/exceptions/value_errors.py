"""``ValueError`` subclass that carries a localizable code."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class ValidationValueError(ValueError):
    """A ``ValueError`` that names its catalog key.

    Pydantic files everything a field validator raises under the single
    error type ``value_error``, with the exception itself in
    ``ctx["error"]``. The catalog localizes by error *type*, so every
    message the SDK's own BR validators wrote — ``invalid CPF/CNPJ``,
    ``invalid CEP``, ``invalid PIX key`` — reached the client in English
    inside a Portuguese template::

        Valor inválido: invalid CPF/CNPJ

    A consumer could not fix that from outside: the only key the six
    cases share is ``VALIDATION.value_error``, so translating them meant
    matching **substrings** of the SDK's English phrases — an
    unannounced coupling that breaks silently the day a phrase is
    reworded.

    Raising this instead attaches a stable ``code`` the validation
    handler resolves as ``VALIDATION.<code>``, falling back to
    ``VALIDATION.value_error`` when the catalog does not know it. A
    plain ``ValueError`` raised by a consumer's own validator keeps
    behaving exactly as before.

    Attributes:
        code (str): Catalog key suffix, ``UPPER_SNAKE`` — which is also
            what keeps it apart from pydantic's ``lower_snake`` error
            types under the same ``VALIDATION.`` namespace.
        params (dict[str, Any]): Values interpolated into the localized
            template, merged over pydantic's own ``ctx``.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        """Initialize the error.

        Args:
            code (str): Catalog key suffix resolved under
                ``VALIDATION.``.
            message (str): The English phrase, kept as ``str(exc)`` so
                pydantic's own ``msg`` and any existing handler read the
                same text they read before.
            params (Mapping[str, Any] | None): Template values for the
                localized message.
        """
        self.code: str = code
        self.params: dict[str, Any] = dict(params or {})
        super().__init__(message)


__all__: list[str] = [
    "ValidationValueError",
]
