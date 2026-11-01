"""``prune_orphan_enum_schemas`` — the enum components nothing references.

FastAPI publishes a component for every ``Enum`` in a field annotation, even
when the field's schema inlines a narrower enum (#637). Pinned: the orphan
left by ``image_media_type_field`` goes, a referenced enum and an orphan
object schema stay, and the patch is idempotent.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Any

from fastapi import FastAPI

from tempest_fastapi_sdk import prune_orphan_enum_schemas, register_exception_handlers
from tempest_fastapi_sdk.api import drop_orphan_enum_schemas
from tempest_fastapi_sdk.schemas import BaseSchema
from tempest_fastapi_sdk.utils.fields import image_media_type_field
from tempest_fastapi_sdk.utils.media_types import ImageMediaTypeEnum


class Status(StrEnum):
    """An enum some field references by ``$ref``."""

    OPEN = "open"


class EvidenceCreateSchema(BaseSchema):
    """A schema carrying the narrowed enum and a plain one."""

    photo_type: Annotated[
        ImageMediaTypeEnum,
        image_media_type_field(ImageMediaTypeEnum.JPEG, ImageMediaTypeEnum.PNG),
    ]
    status: Status


def build(*, prune: bool) -> FastAPI:
    """Build an app with one route over :class:`EvidenceCreateSchema`.

    Args:
        prune (bool): Whether to install the pruning.

    Returns:
        FastAPI: The app.
    """
    app = FastAPI()
    register_exception_handlers(app)
    if prune:
        prune_orphan_enum_schemas(app)

    @app.post("/evidence")
    async def create(body: EvidenceCreateSchema) -> EvidenceCreateSchema:
        return body

    return app


def schemas(app: FastAPI) -> dict[str, Any]:
    """Return the app's schema components.

    Args:
        app (FastAPI): The app.

    Returns:
        dict[str, Any]: ``components.schemas``.
    """
    components: dict[str, Any] = app.openapi()["components"]["schemas"]
    return components


def test_fastapi_alone_publishes_the_orphan() -> None:
    app = build(prune=False)
    document = json.dumps(app.openapi())

    assert "ImageMediaTypeEnum" in schemas(app)
    assert "#/components/schemas/ImageMediaTypeEnum" not in document


def test_the_orphan_goes_and_the_narrowed_enum_stays_inline() -> None:
    app = build(prune=True)
    components = schemas(app)

    assert "ImageMediaTypeEnum" not in components
    assert components["EvidenceCreateSchema"]["properties"]["photo_type"]["enum"] == [
        "image/jpeg",
        "image/png",
    ]


def test_a_referenced_enum_stays() -> None:
    assert "Status" in schemas(build(prune=True))


def test_regenerating_the_schema_is_harmless() -> None:
    app = build(prune=True)
    first = sorted(schemas(app))
    app.openapi_schema = None

    assert sorted(schemas(app)) == first


def test_only_unreferenced_enums_are_dropped() -> None:
    document: dict[str, Any] = {
        "paths": {
            "/a": {"get": {"responses": {"200": {"$ref": "#/components/schemas/Used"}}}}
        },
        "components": {
            "schemas": {
                "Used": {"type": "string", "enum": ["a"]},
                "Orphan": {"type": "string", "enum": ["b"]},
                "LooseObject": {"type": "object", "properties": {}},
                "Nested": {
                    "type": "object",
                    "properties": {"x": {"$ref": "#/components/schemas/Inner"}},
                },
                "Inner": {"enum": ["c"]},
            }
        },
    }

    assert drop_orphan_enum_schemas(document) == ["Orphan"]
    assert sorted(document["components"]["schemas"]) == [
        "Inner",
        "LooseObject",
        "Nested",
        "Used",
    ]
