"""Base Pydantic schema shared by every DTO in the SDK."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from tempest_fastapi_sdk.utils.dict import modify_dict


class BaseSchema(BaseModel):
    """Base class for every Pydantic schema in an application.

    Centralizes the configuration that all DTOs share: ignore extra
    fields, allow building schemas from ORM attributes, serialize
    enum values, strip whitespace from strings, and validate
    assignments after construction.

    Attributes:
        model_config (ConfigDict): The Pydantic configuration.
    """

    model_config = ConfigDict(
        extra="ignore",
        from_attributes=True,
        use_enum_values=True,
        str_strip_whitespace=True,
        validate_assignment=True,
        arbitrary_types_allowed=True,
    )

    def to_dict(
        self,
        exclude: list[str] | None = None,
        include: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Serialize the schema to a plain ``dict``.

        Drops ``None`` values, removes keys listed in ``exclude``
        and merges ``include`` on top of the remaining payload.

        Args:
            exclude (list[str] | None): Field names to drop from the
                output dictionary.
            include (dict[str, Any] | None): Extra entries to merge
                into the output (override existing keys).

        Returns:
            dict[str, Any]: The serialized representation.
        """
        data = self.model_dump(exclude_none=True, exclude_unset=True)
        return modify_dict(data, exclude=exclude, include=include)

    def to_json(self) -> str:
        """Serialize the schema to a JSON string.

        Returns:
            str: The JSON encoded representation of the schema.
        """
        return self.model_dump_json()


__all__: list[str] = [
    "BaseSchema",
]
