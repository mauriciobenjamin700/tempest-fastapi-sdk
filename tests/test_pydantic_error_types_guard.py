"""Guard: the validation catalog covers exactly pydantic's error types.

The messages a 422 shows are keyed by the pydantic error *type*, not by
field, which is what makes a new field on a schema translated the day it
is added. That only holds while the key set matches the installed
pydantic — and nothing type-checks a dictionary key.

Both directions are failures:

* a type upstream added and we do not translate would reach a Portuguese
  client in English, silently and looking deliberate;
* a key we carry that upstream removed is a rename nobody finished, and
  it makes the count meaningless.

The set is derived from ``pydantic_core.ErrorType``, the public
enumeration, cross-checked against ``list_all_errors()``. Portuguese is
the only locale checked, and that is deliberate: pydantic's own ``msg``
*is* the English message, so the handler falls back to it rather than
this SDK keeping a copy that can drift from upstream wording.

Blind spot: the *quality* of a translation is a judgement call no guard
makes. This proves coverage and placeholder validity, not wording.
"""

from __future__ import annotations

import string
import typing
from typing import Any, Final

import pydantic_core
import pytest
from pydantic_core import _pydantic_core

from tempest_fastapi_sdk.exceptions.i18n import (
    _PYDANTIC_ERROR_TYPES_PT_BR,
    VALIDATION_KEY_PREFIX,
    default_message_catalog,
)

EXEMPT: Final[dict[str, str]] = {}
"""Error types deliberately left untranslated, each with the reason why.

Empty today. An entry here is a decision, and a type missing from both
this mapping and the table is the defect the guard exists for.
"""

UNAVAILABLE_PLACEHOLDER: Final[str] = "expected_plural"
"""A placeholder upstream templates use that never reaches ``ctx``.

pydantic computes it while rendering its own message, so a template of
ours that used it would reach the client with literal braces —
``MessageCatalog.resolve`` returns the template unformatted when a param
is missing. The Portuguese table writes ``caractere(s)`` instead.
"""


def upstream_error_types() -> frozenset[str]:
    """Return every error type the installed pydantic declares.

    Returns:
        frozenset[str]: The members of ``pydantic_core.ErrorType``.
    """
    return frozenset(typing.get_args(pydantic_core.ErrorType))


def upstream_contexts() -> dict[str, dict[str, Any]]:
    """Return each error type's example context.

    Returns:
        dict[str, dict[str, Any]]: ``{type: example_context}``, which
        names the placeholders pydantic actually supplies.
    """
    return {
        entry["type"]: (entry.get("example_context") or {})
        for entry in _pydantic_core.list_all_errors()
    }


class TestTheTwoUpstreamListsAgree:
    """``ErrorType`` and ``list_all_errors()`` must describe one set."""

    def test_the_literal_matches_the_runtime_list(self) -> None:
        assert upstream_error_types() == frozenset(upstream_contexts())


class TestCoverage:
    def test_every_upstream_type_is_translated_or_exempt(self) -> None:
        missing = (
            upstream_error_types() - set(_PYDANTIC_ERROR_TYPES_PT_BR) - set(EXEMPT)
        )

        assert not missing, (
            f"{len(missing)} pydantic error type(s) have no pt-BR message: "
            + ", ".join(sorted(missing))
        )

    def test_no_key_survives_its_removal_upstream(self) -> None:
        stale = set(_PYDANTIC_ERROR_TYPES_PT_BR) - upstream_error_types()

        assert not stale, (
            f"pt-BR translates types pydantic no longer has: {sorted(stale)}"
        )

    def test_every_exemption_carries_a_reason(self) -> None:
        assert all(reason.strip() for reason in EXEMPT.values())


class TestPlaceholdersAreOnesPydanticSupplies:
    """A template naming a param that never arrives ships literal braces."""

    @pytest.mark.parametrize("error_type", sorted(_PYDANTIC_ERROR_TYPES_PT_BR))
    def test_each_placeholder_exists_in_the_context(self, error_type: str) -> None:
        template = _PYDANTIC_ERROR_TYPES_PT_BR[error_type]
        used = {field for _, field, _, _ in string.Formatter().parse(template) if field}
        available = set(upstream_contexts().get(error_type, {}))

        assert used <= available, (
            f"{error_type} uses {sorted(used - available)}, "
            f"but ctx only carries {sorted(available)}"
        )

    def test_the_plural_placeholder_is_never_used(self) -> None:
        offenders = [
            error_type
            for error_type, template in _PYDANTIC_ERROR_TYPES_PT_BR.items()
            if UNAVAILABLE_PLACEHOLDER in template
        ]

        assert not offenders, offenders


class TestTheGuardFires:
    """Fed a table missing a type, the coverage check has to refuse."""

    def test_a_missing_type_is_reported(self) -> None:
        partial = {"missing": "Campo obrigatório"}

        uncovered = upstream_error_types() - set(partial) - set(EXEMPT)

        assert "string_too_short" in uncovered
        assert len(uncovered) == len(upstream_error_types()) - 1

    def test_a_stale_key_is_reported(self) -> None:
        table = {**_PYDANTIC_ERROR_TYPES_PT_BR, "renamed_upstream": "..."}

        stale = set(table) - upstream_error_types()

        assert stale == {"renamed_upstream"}


class TestTheShippedCatalogAnswersInPortuguese:
    """The end the guard exists for: a real type, negotiated, in PT-BR."""

    def test_a_bound_error_interpolates_its_context(self) -> None:
        catalog = default_message_catalog()
        locale = catalog.negotiate("pt-BR,pt;q=0.9")

        message = catalog.resolve(
            f"{VALIDATION_KEY_PREFIX}string_too_short",
            locale,
            {"min_length": 12},
        )

        assert message == "O texto deve ter no mínimo 12 caractere(s)"

    def test_english_is_absent_on_purpose(self) -> None:
        """The handler falls back to pydantic's own English ``msg``."""
        catalog = default_message_catalog()

        english = catalog.resolve(
            f"{VALIDATION_KEY_PREFIX}string_too_short",
            catalog.negotiate("en-US"),
        )

        assert english is None
