"""An operation with several 2xx bodies gets one type, and says so.

A client method returns one annotation, so only one success status can be
modelled and the generator picks the lowest. That is free when the other
statuses carry the same shape, and it is not free when they do not: the
caller gets a method annotated for a body the API may never send, and
validating the real one raises at request time.

Through 0.286.0 the generator made that choice **in silence**. It said so
for a response it could not model at all (``text/plain``, ``image/png``)
and said nothing for one it modelled wrongly, which is the worse of the
two failures.

The calibration matters as much as the check. Measured across the two
vendored specifications the SDK already ships — 125 OpenPix operations and
143 Mercado Pago ones — this note fires **zero** times, because their
several-2xx operations differ only in the wording of a nested
``description``. A check that flagged those would be noise nobody reads.
"""

from __future__ import annotations

from typing import Any

from tempest_fastapi_sdk.openapi.parse import parse_spec

_NOTE_FRAGMENT = "success statuses with different bodies"


def _document(responses: dict[str, Any]) -> dict[str, Any]:
    """Build a one-operation specification with the given responses.

    Args:
        responses (dict[str, Any]): The ``responses`` block to attach.

    Returns:
        dict[str, Any]: A minimal but valid OpenAPI document.
    """
    return {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1"},
        "paths": {
            "/send": {
                "post": {
                    "operationId": "send",
                    "responses": responses,
                }
            }
        },
        "components": {
            "schemas": {
                "Sent": {
                    "type": "object",
                    "required": ["success"],
                    "properties": {"success": {"type": "boolean"}},
                },
                "Accepted": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {"id": {"type": "string"}},
                },
            }
        },
    }


def _json(ref: str, description: str = "ok") -> dict[str, Any]:
    """Build a response entry pointing at a component schema.

    Args:
        ref (str): The component schema name.
        description (str): The response description.

    Returns:
        dict[str, Any]: The response object.
    """
    return {
        "description": description,
        "content": {
            "application/json": {"schema": {"$ref": f"#/components/schemas/{ref}"}}
        },
    }


class TestDivergentSuccessIsReported:
    """Different shapes under different 2xx codes must not pass in silence."""

    def test_note_names_the_modelled_and_the_ignored_status(self) -> None:
        """The note has to be actionable, so it carries both status codes."""
        spec = parse_spec(
            _document({"200": _json("Sent"), "202": _json("Accepted")}),
            client_name="t",
        )
        notes = [n for n in spec.unsupported if _NOTE_FRAGMENT in n]
        assert len(notes) == 1
        assert "200" in notes[0]
        assert "202" in notes[0]

    def test_the_lowest_status_is_the_one_modelled(self) -> None:
        """The chosen annotation is still the lowest 2xx — only the silence changed."""
        spec = parse_spec(
            _document({"200": _json("Sent"), "202": _json("Accepted")}),
            client_name="t",
        )
        operation = spec.client.operations[0]
        assert operation.success_status == "200"
        assert operation.response_annotation == "Sent"

    def test_three_statuses_are_counted(self) -> None:
        """The count in the note is of statuses carrying a JSON body."""
        spec = parse_spec(
            _document(
                {
                    "200": _json("Sent"),
                    "201": _json("Accepted"),
                    "202": _json("Accepted"),
                }
            ),
            client_name="t",
        )
        notes = [n for n in spec.unsupported if _NOTE_FRAGMENT in n]
        assert len(notes) == 1
        assert "3 success statuses" in notes[0]


class TestSameShapeIsNotFlagged:
    """The check must stay quiet where nothing is lost."""

    def test_identical_schemas_are_silent(self) -> None:
        """``200`` and ``201`` answering one object loses nothing."""
        spec = parse_spec(
            _document({"200": _json("Sent"), "201": _json("Sent")}),
            client_name="t",
        )
        assert not [n for n in spec.unsupported if _NOTE_FRAGMENT in n]

    def test_wording_differences_are_silent(self) -> None:
        """This is the OpenPix case, and flagging it would be noise.

        ``createCashbackFidelity`` and ``createKycOnboarding`` declare
        ``200`` and ``201`` whose schemas differ only in the ``description``
        of a nested property. The structure is identical, so the generated
        method is correct for both.
        """
        document = _document(
            {
                "200": {
                    "description": "existing",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "cashback": {
                                        "type": "string",
                                        "description": "the existing cashback",
                                    }
                                },
                            }
                        }
                    },
                },
                "201": {
                    "description": "created",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "cashback": {
                                        "type": "string",
                                        "description": "the new cashback",
                                        "example": "abc",
                                    }
                                },
                            }
                        }
                    },
                },
            }
        )
        spec = parse_spec(document, client_name="t")
        assert not [n for n in spec.unsupported if _NOTE_FRAGMENT in n]

    def test_a_single_success_status_is_silent(self) -> None:
        """Nothing to choose between, so nothing to report."""
        spec = parse_spec(_document({"200": _json("Sent")}), client_name="t")
        assert not [n for n in spec.unsupported if _NOTE_FRAGMENT in n]

    def test_a_status_without_a_json_body_does_not_count(self) -> None:
        """A ``204`` alongside a ``200`` is not a divergence — it has no body."""
        spec = parse_spec(
            _document({"200": _json("Sent"), "204": {"description": "no content"}}),
            client_name="t",
        )
        assert not [n for n in spec.unsupported if _NOTE_FRAGMENT in n]
