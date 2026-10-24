"""``image_media_type_field`` restricts an ``ImageMediaType`` field (#615)."""

from __future__ import annotations

from typing import Annotated

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tempest_fastapi_sdk import (
    BaseSchema,
    CursorPaginationFilterSchema,
    ImageMediaType,
    image_media_type_field,
)
from tempest_fastapi_sdk.utils import AllowedImageMediaTypes

PHOTO_TYPES: tuple[ImageMediaType, ...] = (
    ImageMediaType.JPEG,
    ImageMediaType.PNG,
    ImageMediaType.WEBP,
    ImageMediaType.HEIC,
)
"""What a service storing camera photos accepts."""


class EvidenceSchema(BaseSchema):
    photo_type: Annotated[
        ImageMediaType,
        image_media_type_field(*PHOTO_TYPES, title="Photo type"),
    ]


class AnyImageSchema(BaseSchema):
    image_type: Annotated[ImageMediaType, image_media_type_field()]


class TestValidation:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("image/jpeg", ImageMediaType.JPEG),
            ("IMAGE/JPG ", ImageMediaType.JPEG),
            ("image/pjpeg", ImageMediaType.JPEG),
            ("image/heic", ImageMediaType.HEIC),
        ],
    )
    def test_the_enum_normalizes_before_the_check(
        self, raw: str, expected: ImageMediaType
    ) -> None:
        assert EvidenceSchema(photo_type=raw).photo_type == expected

    def test_a_type_outside_the_list_is_refused(self) -> None:
        with pytest.raises(ValidationError) as caught:
            EvidenceSchema(photo_type="image/gif")
        error = caught.value.errors()[0]
        assert error["type"] == "value_error"
        assert "image/jpeg, image/png, image/webp, image/heic" in error["msg"]

    def test_an_unknown_type_is_refused_by_the_enum(self) -> None:
        with pytest.raises(ValidationError) as caught:
            EvidenceSchema(photo_type="application/pdf")
        assert caught.value.errors()[0]["type"] == "enum"

    def test_without_arguments_every_member_is_allowed(self) -> None:
        for member in ImageMediaType:
            assert AnyImageSchema(image_type=member.value).image_type == member

    def test_an_empty_restriction_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one type"):
            AllowedImageMediaTypes(())


class TestSchema:
    def test_openapi_lists_only_the_allowed_types(self) -> None:
        field = EvidenceSchema.model_json_schema()["properties"]["photo_type"]
        assert field["enum"] == [item.value for item in PHOTO_TYPES]
        assert field["title"] == "Photo type"
        assert field["examples"] == ["image/jpeg"]
        assert "image/heic" in field["description"]

    def test_explicit_metadata_wins(self) -> None:
        info = image_media_type_field(
            ImageMediaType.PNG,
            title="Logo",
            description="PNG only.",
            examples=["image/png"],
        )
        assert (info.title, info.description, info.examples) == (
            "Logo",
            "PNG only.",
            ["image/png"],
        )

    def test_through_a_route_the_refusal_is_a_422(self) -> None:
        """What the client of a service sees."""
        app = FastAPI()

        @app.post("/evidence")
        async def create(payload: EvidenceSchema) -> dict[str, str]:
            return {"photo_type": payload.photo_type}

        client = TestClient(app)
        ok = client.post("/evidence", json={"photo_type": "IMAGE/PNG"})
        refused = client.post("/evidence", json={"photo_type": "image/gif"})
        schema = app.openapi()["components"]["schemas"]["EvidenceSchema"]
        assert ok.json() == {"photo_type": "image/png"}
        assert refused.status_code == 422
        assert schema["properties"]["photo_type"]["enum"] == [
            item.value for item in PHOTO_TYPES
        ]


class TestCursorPaginationExamples:
    def test_ascending_carries_examples(self) -> None:
        field = CursorPaginationFilterSchema.model_json_schema()["properties"][
            "ascending"
        ]
        assert field["examples"] == [False, True]
