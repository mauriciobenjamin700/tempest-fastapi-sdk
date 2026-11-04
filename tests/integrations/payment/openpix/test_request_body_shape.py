"""What actually leaves the process on ``POST /api/v1/charge``.

Every other test here stubs the client or asserts on the answer, so nothing
looked at the request body — and the body was the defect. ``ChargePayload``
generated ``splits`` and ``additionalInfo`` with ``default_factory=list``,
``_dump`` only knew how to drop ``None``, and every single call therefore
sent ``"splits": []``. Woovi answers that with
``400 O array de split precisa ter ao menos um item``; the identical body
without the key is accepted.

These tests read the bytes off an ``httpx.MockTransport``, which is the only
place the difference between "not informed" and "informed as empty" is
visible.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment.openpix import (
    ChargePayload,
    ChargePayloadSplitsItem,
    OpenPixClient,
)

_CHARGE_ANSWER: dict[str, Any] = {
    "charge": {
        "status": "ACTIVE",
        "correlationID": "probe-0001",
        "value": 1190,
        "brCode": "00020101021226980014br.gov.bcb.pix",
    }
}


class _Recorder:
    """Answers every request with a fixed charge, keeping the body sent.

    Attributes:
        body (dict[str, Any]): The decoded JSON of the last request.
    """

    def __init__(self) -> None:
        """Start with no recorded body."""
        self.body: dict[str, Any] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record the request body and answer with a created charge.

        Args:
            request (httpx.Request): The outgoing request.

        Returns:
            httpx.Response: A ``200`` carrying a minimal charge.
        """
        self.body = json.loads(request.content.decode())
        return httpx.Response(200, json=_CHARGE_ANSWER)


def _client(recorder: _Recorder) -> OpenPixClient:
    """Build a client whose transport records instead of connecting.

    Args:
        recorder (_Recorder): The handler capturing the request body.

    Returns:
        OpenPixClient: A client pointed at the recording transport.
    """
    return OpenPixClient(
        HTTPClient(
            base_url="https://api.woovi-sandbox.com",
            transport=httpx.MockTransport(recorder),
        )
    )


class TestCreateChargeBody:
    async def test_an_optional_array_never_informed_stays_off_the_wire(self) -> None:
        """The exact body Woovi rejected, and now does not receive."""
        recorder = _Recorder()
        await _client(recorder).create_charge(
            body=ChargePayload(correlation_id="probe-0001", value=1190)
        )
        assert "splits" not in recorder.body
        assert "additionalInfo" not in recorder.body
        assert recorder.body == {"correlationID": "probe-0001", "value": 1190}

    async def test_an_array_the_caller_informed_is_sent(self) -> None:
        """Omitting is about silence, not about dropping what was said."""
        recorder = _Recorder()
        await _client(recorder).create_charge(
            body=ChargePayload(
                correlation_id="probe-0002",
                value=1190,
                splits=[
                    ChargePayloadSplitsItem(
                        value=100,
                        pix_key="key@example.com",
                    )
                ],
            )
        )
        assert recorder.body["splits"] == [{"value": 100, "pixKey": "key@example.com"}]

    def test_an_empty_split_list_is_refused_before_the_request(self) -> None:
        """``splits=[]`` used to go on the wire and come back as a 400 (#699).

        Woovi answers ``400 O array de split precisa ter ao menos um item`` to
        ``"splits": []``. The overlay declares ``minItems: 1`` on the field, so
        the payload refuses it at construction, naming the field, before any
        request is built.
        """
        with pytest.raises(ValidationError) as caught:
            ChargePayload(correlation_id="probe-0003", value=1190, splits=[])

        assert [error["loc"] for error in caught.value.errors()] == [("splits",)]
        assert caught.value.errors()[0]["type"] == "too_short"

    async def test_none_still_stays_off_the_wire(self) -> None:
        """The workaround the issue documents keeps working: ``or None``."""
        recorder = _Recorder()
        await _client(recorder).create_charge(
            body=ChargePayload(correlation_id="probe-0004", value=1190, splits=None)
        )
        assert "splits" not in recorder.body
