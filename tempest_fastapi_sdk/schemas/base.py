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

    Enum fields:
        ``use_enum_values=True`` means a field annotated with an enum
        holds the **value**, not the member — so identity comparison
        never matches, and equality only survives when the enum is
        ``str``-based. Measured on this suite::

            class Kind(StrEnum):  # or BaseStrEnum
                GROUP = "group"

            S(kind=Kind.GROUP).kind is Kind.GROUP   # False
            S(kind=Kind.GROUP).kind == Kind.GROUP   # True

            class Plain(Enum):
                GROUP = "group"

            S(kind=Plain.GROUP).kind == Plain.GROUP  # False

        Both failures type-check, run without error and simply never
        enter the branch, which is the worst shape a comparison can
        take. So: annotate schema fields with
        :class:`~tempest_fastapi_sdk.BaseStrEnum` (or another
        ``str``-based enum) and compare with ``==`` / ``in``, never
        ``is``. ``tests/test_enum_schema_guard.py`` fails when a schema
        in this package annotates a field with a non-``str`` enum.

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
