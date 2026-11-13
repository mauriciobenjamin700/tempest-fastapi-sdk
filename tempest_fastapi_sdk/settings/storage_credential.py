"""The credential of one bucket, for ``STORAGE_BUCKET_CREDENTIALS``.

Lives in ``settings`` rather than ``storage`` because ``StorageSettings``
declares a field of this type, and ``storage`` already imports from
``settings``; ``tempest_fastapi_sdk.storage`` re-exports it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class StorageBucketCredential(BaseModel):
    """The access key and secret key one bucket is reached with.

    The secret is a :class:`~pydantic.SecretStr`, so ``repr``, ``str`` and
    ``model_dump`` print ``**********``; validation errors leave the input
    out (``hide_input_in_errors``), so a typo in the JSON does not echo the
    secret into the boot log. Unknown keys are refused, which turns
    ``{"access_key": ..., "secret": ...}`` into an error instead of a
    credential without a secret.

    Attributes:
        access_key (str): The S3 access key. Must not be empty.
        secret_key (SecretStr): The S3 secret key. Must not be empty.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    access_key: str = Field(min_length=1)
    secret_key: SecretStr

    @field_validator("secret_key")
    @classmethod
    def _require_secret(cls, value: SecretStr) -> SecretStr:
        """Refuse an empty secret key.

        Args:
            value (SecretStr): The secret key as given.

        Returns:
            SecretStr: ``value`` unchanged.

        Raises:
            ValueError: When the secret is empty.
        """
        if not value.get_secret_value():
            raise ValueError("secret_key must not be empty")
        return value


__all__: list[str] = ["StorageBucketCredential"]
