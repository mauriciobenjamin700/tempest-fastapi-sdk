"""Tests for file controls and multipart parsing (#347)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from tempestweb.html import render_to_html

from tempest_fastapi_sdk.ui.forms import (
    UnsupportedFieldError,
    fields_for,
    form_for,
    form_spec_for,
    parse_form,
)


class AvatarSchema(BaseModel):
    """A text field next to a required upload and an optional batch."""

    title: str = Field(min_length=3)
    avatar: UploadFile = Field(json_schema_extra={"ui": {"accept": "image/*"}})
    attachments: list[UploadFile] = Field(default_factory=list)


BOUNDARY = "tempestboundary"


def _multipart(parts: list[tuple[str, str | None, bytes]]) -> bytes:
    """Encode a ``multipart/form-data`` body the way a browser does.

    Args:
        parts (list[tuple[str, str | None, bytes]]): ``(name, filename,
            content)`` per part; ``filename=None`` is a text part and
            ``filename=""`` is an empty file control.

    Returns:
        bytes: The encoded body, delimited by :data:`BOUNDARY`.
    """
    chunks: list[bytes] = []
    for name, filename, content in parts:
        disposition = f'form-data; name="{name}"'
        headers = f"Content-Disposition: {disposition}"
        if filename is not None:
            headers = (
                f'Content-Disposition: {disposition}; filename="{filename}"\r\n'
                "Content-Type: application/octet-stream"
            )
        chunks.append(f"--{BOUNDARY}\r\n{headers}\r\n\r\n".encode() + content + b"\r\n")
    chunks.append(f"--{BOUNDARY}--\r\n".encode())
    return b"".join(chunks)


def _client() -> TestClient:
    """Build a client posting to a route that parses :class:`AvatarSchema`.

    Returns:
        TestClient: A client whose ``/avatar`` route answers JSON on
        success and the re-rendered form (``422``) on failure.
    """
    app = FastAPI()

    @app.post("/avatar")
    async def avatar(request: Request) -> Response:
        """Parse the upload and echo what reached the model."""
        result = await parse_form(AvatarSchema, request)
        if not result.ok:
            return HTMLResponse(
                render_to_html(
                    form_for(
                        AvatarSchema,
                        action="/avatar",
                        values=result.values,
                        errors=result.errors,
                    ),
                ),
                status_code=422,
            )
        model = result.unwrap()
        return JSONResponse(
            {
                "title": model.title,
                "avatar": [model.avatar.filename, (await model.avatar.read()).decode()],
                "attachments": [item.filename for item in model.attachments],
            },
        )

    return TestClient(app)


def _post(client: TestClient, parts: list[tuple[str, str | None, bytes]]) -> Response:
    """Post a raw multipart body.

    Args:
        client (TestClient): The client from :func:`_client`.
        parts (list[tuple[str, str | None, bytes]]): See :func:`_multipart`.

    Returns:
        Response: The route's response.
    """
    return client.post(
        "/avatar",
        content=_multipart(parts),
        headers={"content-type": f"multipart/form-data; boundary={BOUNDARY}"},
    )


def test_upload_annotation_renders_a_file_control() -> None:
    specs = {spec.name: spec for spec in fields_for(AvatarSchema)}
    assert specs["avatar"].control == "file"
    assert specs["avatar"].accept == "image/*"
    assert specs["avatar"].multiple is False
    assert specs["attachments"].control == "file"
    assert specs["attachments"].multiple is True


def test_file_control_switches_the_enctype() -> None:
    html = render_to_html(form_for(AvatarSchema, action="/avatar"))
    assert 'enctype="multipart/form-data"' in html
    assert 'type="file" accept="image/*"' in html
    assert 'name="attachments"' in html
    assert 'multiple="multiple"' in html
    assert form_spec_for(AvatarSchema, action="/avatar").multipart is True


def test_explicit_enctype_wins() -> None:
    html = render_to_html(
        form_for(AvatarSchema, action="/avatar", attrs={"enctype": "text/plain"}),
    )
    assert 'enctype="text/plain"' in html
    assert "multipart/form-data" not in html


def test_forms_without_files_carry_no_enctype() -> None:
    class PlainSchema(BaseModel):
        """No upload at all."""

        title: str

    assert "enctype" not in render_to_html(form_for(PlainSchema, action="/x"))


def test_control_override_renders_a_file_control() -> None:
    class ForcedSchema(BaseModel):
        """A field marked as a file control by its ui block."""

        document: UploadFile | None = Field(
            default=None,
            json_schema_extra={
                "ui": {"control": "file", "accept": ".pdf", "multiple": True},
            },
        )

    spec = fields_for(ForcedSchema)[0]
    assert (spec.control, spec.accept, spec.multiple) == ("file", ".pdf", True)


def test_bytes_fields_are_still_rejected() -> None:
    class RawSchema(BaseModel):
        """Raw bytes have no form control."""

        payload: bytes

    with pytest.raises(UnsupportedFieldError, match="UploadFile"):
        fields_for(RawSchema)


def test_multipart_submission_delivers_the_upload_files() -> None:
    response = _post(
        _client(),
        [
            ("title", None, b"Holiday"),
            ("avatar", "me.png", b"PNGDATA"),
            ("attachments", "a.txt", b"a"),
            ("attachments", "b.txt", b"b"),
        ],
    )
    assert response.status_code == 200
    assert response.json() == {
        "title": "Holiday",
        "avatar": ["me.png", "PNGDATA"],
        "attachments": ["a.txt", "b.txt"],
    }


def test_empty_file_control_reads_as_absent() -> None:
    response = _post(
        _client(),
        [
            ("title", None, b"Holiday"),
            ("avatar", "", b""),
            ("attachments", "", b""),
        ],
    )
    assert response.status_code == 422
    html = response.text
    assert 'value="Holiday"' in html
    assert '<p class="tui-field__error" id="f-avatar-error">Field required</p>' in html


def test_re_render_keeps_text_and_empties_the_file() -> None:
    response = _post(
        _client(),
        [("title", None, b"ab"), ("avatar", "me.png", b"PNGDATA")],
    )
    assert response.status_code == 422
    html = response.text
    assert 'value="ab"' in html
    assert 'id="f-title-error"' in html
    file_input = html.split('name="avatar"', 1)[1].split("/>", 1)[0]
    assert "value=" not in file_input
