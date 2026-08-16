"""FCM transport — the mobile half of the unified module.

Delivers through ``firebase_admin.messaging``, reusing the credential and
the optional ``[firebase]`` extra that
:class:`tempest_fastapi_sdk.auth.FirebaseAuth` already loads. One service
account, one initialized app, two features.

Like every optional dependency in the SDK, ``firebase_admin`` is imported
inside the constructor, so ``import tempest_fastapi_sdk.push`` works with
the extra absent and only building an :class:`FCMTransport` needs it.
"""

from __future__ import annotations

import asyncio
import warnings
from typing import Any

from tempest_fastapi_sdk.auth.firebase import FirebaseAuth, FirebaseCredentialError
from tempest_fastapi_sdk.push.dispatcher import PushDeviceGoneError, PushError
from tempest_fastapi_sdk.push.schemas import (
    PushDevice,
    PushPayloadSchema,
    PushPlatform,
)


def _require_firebase_messaging() -> tuple[Any, Any, Any]:
    """Import ``firebase_admin.messaging`` lazily.

    Returns:
        tuple[Any, Any, Any]: The ``firebase_admin`` module, its
        ``messaging`` submodule and its ``exceptions`` submodule.

    Raises:
        ImportError: When the optional ``[firebase]`` extra is missing.
    """
    try:
        import firebase_admin
        from firebase_admin import exceptions, messaging
    except ImportError as exc:
        raise ImportError(
            "FCM push requires the optional [firebase] extra. "
            "Install with: pip install tempest-fastapi-sdk[firebase]",
        ) from exc
    return firebase_admin, messaging, exceptions


class FCMTransport:
    """Deliver unified payloads to iOS and Android through FCM.

    The transport does not own a credential of its own. Pass the
    :class:`~tempest_fastapi_sdk.auth.FirebaseAuth` the service already
    built for ID token verification and both features share one
    initialized app; pass ``app`` to target a specific
    ``firebase_admin.App``; pass neither and the default app is used, so a
    service that calls ``firebase_admin.initialize_app()`` itself still
    works.

    Attributes:
        platforms (frozenset[str]): ``{"ios", "android"}``.
        dry_run (bool): When ``True``, FCM validates the message and
            reports the result without waking any device. Useful to prove
            wiring in staging without notifying real users.
    """

    platforms: frozenset[str] = frozenset(
        {PushPlatform.IOS.value, PushPlatform.ANDROID.value}
    )

    def __init__(
        self,
        *,
        auth: FirebaseAuth | None = None,
        app: Any | None = None,
        dry_run: bool = False,
    ) -> None:
        """Initialize the transport.

        Args:
            auth (FirebaseAuth | None): An authenticator whose initialized
                Firebase app should be reused. Takes precedence over
                ``app``.
            app (Any | None): An explicit ``firebase_admin.App``.
            dry_run (bool): Send messages in FCM's validate-only mode.

        Raises:
            ImportError: When the optional ``[firebase]`` extra is missing.
            FirebaseCredentialError: When neither ``auth`` nor ``app`` was
                given and no default Firebase app has been initialized.
        """
        firebase_admin, messaging, exceptions = _require_firebase_messaging()
        self._messaging: Any = messaging
        self._exceptions: Any = exceptions
        self.dry_run: bool = dry_run
        if auth is not None:
            self._app: Any = auth.app
        elif app is not None:
            self._app = app
        else:
            try:
                self._app = firebase_admin.get_app()
            except ValueError as error:
                raise FirebaseCredentialError(
                    "No Firebase app is initialized: pass auth=FirebaseAuth(...), "
                    "pass app=, or call firebase_admin.initialize_app() first"
                ) from error

    def _build_message(self, device: PushDevice, payload: PushPayloadSchema) -> Any:
        """Map the unified payload onto an FCM message.

        Two measured details drive this mapping (``firebase-admin``
        7.5.0):

        * ``Message.token`` is marked deprecated in favour of ``fid``,
          but the two are **different wire fields** — ``token=`` encodes
          ``{"token": ...}`` and ``fid=`` encodes ``{"fid": ...}``. An FCM
          registration token belongs in ``token``, so that is what is used
          here and the per-message ``DeprecationWarning`` is suppressed
          rather than obeyed. ``tests/push/test_fcm.py`` asserts the
          serialized message still carries a ``token`` field.
        * ``data`` values must be strings. The payload schema already
          narrows the type, so nothing is coerced silently here.

        Args:
            device (PushDevice): The mobile device to deliver to.
            payload (PushPayloadSchema): The notification to deliver.

        Returns:
            Any: A ``firebase_admin.messaging.Message``.
        """
        messaging = self._messaging
        notification = None
        if payload.title or payload.body or payload.image:
            notification = messaging.Notification(
                title=payload.title,
                body=payload.body,
                image=payload.image,
            )
        android = None
        apns = None
        if payload.tag:
            android = messaging.AndroidConfig(collapse_key=payload.tag)
            apns = messaging.APNSConfig(headers={"apns-collapse-id": payload.tag})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            return messaging.Message(
                token=device.token,
                notification=notification,
                data=dict(payload.data),
                android=android,
                apns=apns,
            )

    async def send(self, device: PushDevice, payload: PushPayloadSchema) -> None:
        """Deliver ``payload`` to a mobile device.

        ``messaging.send`` is a blocking HTTP call, so it runs in a worker
        thread, matching the SDK's async-first convention.

        Only two provider errors mean "this device is gone":
        ``UnregisteredError`` (the app was uninstalled or the token was
        rotated) and ``SenderIdMismatchError`` (the token belongs to
        another project). ``InvalidArgumentError`` is deliberately **not**
        treated as gone even though it can be raised for a bad token: FCM
        also raises it for a malformed payload, and pruning on that would
        delete an entire user's fleet the first time a notification body
        is wrong. A bad token then costs one failed attempt per fan-out
        until the client re-registers, which is the cheaper mistake.

        Args:
            device (PushDevice): The mobile device to deliver to.
            payload (PushPayloadSchema): The notification to deliver.

        Raises:
            PushDeviceGoneError: When FCM disowns the token.
            PushError: For any other delivery failure.
        """
        messaging = self._messaging
        message = self._build_message(device, payload)
        try:
            await asyncio.to_thread(messaging.send, message, self.dry_run, self._app)
        except (messaging.UnregisteredError, messaging.SenderIdMismatchError) as error:
            raise PushDeviceGoneError(
                f"FCM disowned {device.masked_token}",
                masked_token=device.masked_token,
            ) from error
        except (self._exceptions.FirebaseError, ValueError) as error:
            raise PushError(
                f"FCM delivery failed for {device.masked_token}: "
                f"{type(error).__name__}",
                masked_token=device.masked_token,
            ) from error


__all__: list[str] = [
    "FCMTransport",
]
