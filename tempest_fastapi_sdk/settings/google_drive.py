"""Settings mixin for the Google Drive upload backend.

Kept in its own module rather than in :mod:`tempest_fastapi_sdk.settings.mixins`,
which is already several thousand lines long. Re-exported from
:mod:`tempest_fastapi_sdk.settings` like every other mixin.
"""

from __future__ import annotations

from typing import TypedDict

from pydantic import Field

from tempest_fastapi_sdk.settings.base import BaseAppSettings


class GoogleDriveStorageKwargs(TypedDict):
    """Keyword arguments for ``GoogleDriveUploadStorage.from_service_account``.

    Returned by :meth:`GoogleDriveSettings.google_drive_kwargs`, typed so the
    splat is checked against the factory's signature.

    Attributes:
        folder_id (str): ``GOOGLE_DRIVE_FOLDER_ID``.
        credentials_path (str | None): ``GOOGLE_DRIVE_CREDENTIALS_PATH``, or
            ``None`` when unset.
        credentials_json (str | None): ``GOOGLE_DRIVE_CREDENTIALS_JSON``, or
            ``None`` when unset.
        drive_id (str | None): ``GOOGLE_DRIVE_DRIVE_ID``, or ``None`` when
            unset.
    """

    folder_id: str
    credentials_path: str | None
    credentials_json: str | None
    drive_id: str | None


class GoogleDriveSettings(BaseAppSettings):
    """Service account and target folder of the Google Drive upload backend.

    Mirrors the arguments of
    :meth:`tempest_fastapi_sdk.integrations.storage.google_drive.GoogleDriveUploadStorage.from_service_account`.

    Each attribute below is also the name of the environment variable that
    sets it (matched case-sensitively, no prefix).

    ``GOOGLE_DRIVE_CREDENTIALS_JSON`` and ``GOOGLE_DRIVE_CREDENTIALS_PATH``
    describe the same service account through different channels, as in
    :class:`FirebaseSettings`: when both are set the inline JSON wins.

    Attributes:
        GOOGLE_DRIVE_CREDENTIALS_PATH (str): Path to the service-account JSON
            key file. Default: ``""``.
        GOOGLE_DRIVE_CREDENTIALS_JSON (str): The service-account JSON inline.
            Default: ``""``.
        GOOGLE_DRIVE_FOLDER_ID (str): Id of the folder files are written
            into — the last path segment of the folder's URL. It must be
            shared with the service account's ``client_email``.
            Default: ``""``.
        GOOGLE_DRIVE_DRIVE_ID (str): Id of the Shared Drive that holds the
            folder, when it lives in one. Optional: lookups then search that
            drive only instead of every drive the account can see.
            Default: ``""``.
    """

    GOOGLE_DRIVE_CREDENTIALS_PATH: str = Field(
        default="",
        title="Google Drive service account file",
        description="Filesystem path to the service-account JSON key.",
        examples=["", "drive-sa.json", "/run/secrets/drive-sa.json"],
    )
    GOOGLE_DRIVE_CREDENTIALS_JSON: str = Field(
        default="",
        title="Google Drive service account JSON (inline)",
        description=(
            "The service-account JSON key itself, for deployments that inject "
            "it as an environment variable instead of mounting a file. Wins "
            "over the file when both are set."
        ),
        examples=["", '{"type": "service_account", "client_email": "..."}'],
    )
    GOOGLE_DRIVE_FOLDER_ID: str = Field(
        default="",
        title="Google Drive folder id",
        description=(
            "Folder uploads are written into, shared with the service "
            "account's client_email."
        ),
        examples=["", "1AbCdEfGhIjKlMnOpQrStUvWxYz"],
    )
    GOOGLE_DRIVE_DRIVE_ID: str = Field(
        default="",
        title="Google Drive Shared Drive id",
        description=(
            "Shared Drive that holds the folder, when it lives in one. Empty "
            "searches every drive the service account can see."
        ),
        examples=["", "0AbCdEfGhIjKlUk9PVA"],
    )

    def google_drive_kwargs(self) -> GoogleDriveStorageKwargs:
        """Map these settings onto ``GoogleDriveUploadStorage.from_service_account``.

        Empty strings become ``None`` so an unset variable leaves the
        factory's own handling in place::

            storage = GoogleDriveUploadStorage.from_service_account(
                **settings.google_drive_kwargs()
            )

        Returns:
            GoogleDriveStorageKwargs: ``folder_id``, ``credentials_path``,
            ``credentials_json`` and ``drive_id``.
        """
        return {
            "folder_id": self.GOOGLE_DRIVE_FOLDER_ID,
            "credentials_path": self.GOOGLE_DRIVE_CREDENTIALS_PATH or None,
            "credentials_json": self.GOOGLE_DRIVE_CREDENTIALS_JSON or None,
            "drive_id": self.GOOGLE_DRIVE_DRIVE_ID or None,
        }


__all__: list[str] = ["GoogleDriveSettings", "GoogleDriveStorageKwargs"]
