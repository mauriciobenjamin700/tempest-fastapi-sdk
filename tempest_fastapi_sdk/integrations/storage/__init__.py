"""Storage providers -- where uploaded files end up, outside the SDK's own stores.

The grouping is by what the third party does, as in ``payment`` and
``messaging``: a service that moves its uploads to another provider changes
one import segment.

Available:

- **`google_drive`** -- a folder (or Shared Drive) in Google Drive, written
  by a service account, as an :class:`~tempest_fastapi_sdk.utils.UploadStorage`
  backend for :class:`~tempest_fastapi_sdk.UploadUtils`.

.. code-block:: python

    from tempest_fastapi_sdk.integrations.storage.google_drive import (
        GoogleDriveUploadStorage,
    )
"""

from tempest_fastapi_sdk.integrations.storage import google_drive as google_drive

__all__: list[str] = ["google_drive"]
