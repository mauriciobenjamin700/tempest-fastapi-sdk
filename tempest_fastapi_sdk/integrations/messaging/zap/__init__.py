"""zap-api — the in-house WhatsApp gateway.

Everything below ``client`` and ``schemas`` is generated from
``vendor/zap-openapi.yaml`` by ``scripts/regen_zap.py`` and checked in.
Editing either file by hand is caught by
``tests/integrations/messaging/zap/test_generated_drift.py``.

.. code-block:: python

    from tempest_fastapi_sdk import HTTPClient
    from tempest_fastapi_sdk.integrations.messaging.zap import (
        AcceptedResponseStatus,
        SendTextRequest,
        ZapClient,
    )

    http: HTTPClient = HTTPClient(
        base_url="http://127.0.0.1:3000",
        default_headers={"x-api-key": "<your key>"},
    )
    client: ZapClient = ZapClient(http)
    accepted = await client.send_text(
        body=SendTextRequest(to="5511999999999", text="oi"),
        idempotency_key="4f1c…",
    )
    assert accepted.status == AcceptedResponseStatus.QUEUED

Three things the generated surface does not say, each measured against the
document this package was generated from.

**A send is asynchronous.** ``send_text`` and its siblings answer ``202``
with :class:`AcceptedResponse` — the row that was enqueued, not a delivery.
``status`` walks ``queued → sending → sent → delivered → read`` (or
``failed``), and those transitions arrive on the gateway's webhook — the
same URL that receives incoming messages.

**Retries need the idempotency key.** Because the send is asynchronous, a
lost ``202`` leaves the caller unable to tell whether the message was
enqueued. Pass a fresh ``idempotency_key`` per message and reuse it when
retrying that same message: the second call answers with the original row
and ``deduped=True`` instead of sending twice. The key is scoped to the
consumer and stays claimed while the row exists, so reusing an old key for
a *new* message answers ``deduped=True`` and sends nothing.

**Two methods are typed ``-> None`` on purpose.** ``metrics`` answers
``text/plain`` and ``get_session_qr_image`` answers ``image/png``; the
generator models ``application/json`` only and reports both. Read the
pairing code as JSON from :meth:`ZapClient.get_session_qr` instead — the
PNG route is a browser convenience over the same value.

Authentication is the ``x-api-key`` header, and only that header. Set it
once as a default on the ``HTTPClient``; ``idempotency_key`` does not
authenticate anything.

**The webhook is the hand-written half.** The specification describes it
in prose and declares no ``webhooks`` block and no ``callbacks``, so
nothing can be generated; ``webhooks.py`` is ported from the gateway's code
instead. :func:`make_zap_webhook_dependency` verifies the HMAC over the raw
body and hands the route a :class:`ZapWebhookDelivery`, already parsed into
:class:`ZapInboundMessage` or :class:`ZapStatusCallback` by ``event``:

.. code-block:: python

    from fastapi import Depends, FastAPI
    from tempest_fastapi_sdk.integrations.messaging.zap import (
        ZapWebhookDelivery,
        ZapWebhookEvent,
        make_zap_webhook_dependency,
    )

    app: FastAPI = FastAPI()
    verify_zap = make_zap_webhook_dependency(secret="<webhook secret>")

    @app.post("/zap/webhook")
    async def zap_webhook(
        delivery: ZapWebhookDelivery = Depends(verify_zap),
    ) -> dict[str, str]:
        if delivery.event is ZapWebhookEvent.MESSAGE_RECEIVED:
            ...
        return {"status": "ok"}

Status notifications can arrive out of order and more than once; apply one
only when :func:`is_forward_transition` says it advances the stored state.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tempest_fastapi_sdk.integrations.messaging.zap.client import *  # noqa: F403
    from tempest_fastapi_sdk.integrations.messaging.zap.schemas import *  # noqa: F403
    from tempest_fastapi_sdk.integrations.messaging.zap.webhooks import (
        ZAP_INBOUND_EVENT as ZAP_INBOUND_EVENT,
    )
    from tempest_fastapi_sdk.integrations.messaging.zap.webhooks import (
        ZAP_WEBHOOK_SIGNATURE_HEADER as ZAP_WEBHOOK_SIGNATURE_HEADER,
    )
    from tempest_fastapi_sdk.integrations.messaging.zap.webhooks import (
        ZAP_WEBHOOK_SIGNATURE_PREFIX as ZAP_WEBHOOK_SIGNATURE_PREFIX,
    )
    from tempest_fastapi_sdk.integrations.messaging.zap.webhooks import (
        ZapInboundMediaType as ZapInboundMediaType,
    )
    from tempest_fastapi_sdk.integrations.messaging.zap.webhooks import (
        ZapInboundMessage as ZapInboundMessage,
    )
    from tempest_fastapi_sdk.integrations.messaging.zap.webhooks import (
        ZapStatusCallback as ZapStatusCallback,
    )
    from tempest_fastapi_sdk.integrations.messaging.zap.webhooks import (
        ZapWebhookDelivery as ZapWebhookDelivery,
    )
    from tempest_fastapi_sdk.integrations.messaging.zap.webhooks import (
        ZapWebhookEvent as ZapWebhookEvent,
    )
    from tempest_fastapi_sdk.integrations.messaging.zap.webhooks import (
        is_forward_transition as is_forward_transition,
    )
    from tempest_fastapi_sdk.integrations.messaging.zap.webhooks import (
        make_zap_webhook_dependency as make_zap_webhook_dependency,
    )
    from tempest_fastapi_sdk.integrations.messaging.zap.webhooks import (
        webhook_verifier as webhook_verifier,
    )

_HAND_WRITTEN: tuple[str, ...] = (
    "ZAP_INBOUND_EVENT",
    "ZAP_WEBHOOK_SIGNATURE_HEADER",
    "ZAP_WEBHOOK_SIGNATURE_PREFIX",
    "ZapInboundMediaType",
    "ZapInboundMessage",
    "ZapStatusCallback",
    "ZapWebhookDelivery",
    "ZapWebhookEvent",
    "is_forward_transition",
    "make_zap_webhook_dependency",
    "webhook_verifier",
)
"""Names this package defines itself, in ``webhooks``.

Resolved lazily like the generated ones rather than imported eagerly:
``webhooks`` builds its schemas over the generated
``AcceptedResponseStatus``, so an eager import would load ``schemas`` on
every ``import`` of this package.
"""

_HAND_WRITTEN_MODULE: str = "webhooks"
"""Submodule holding every name in :data:`_HAND_WRITTEN`."""

_GENERATED_MODULES: tuple[str, ...] = ("schemas", "client")
"""Submodules holding the generated code, in dependency order."""


def _generated_names() -> dict[str, str]:
    """Map every generated name to the submodule that defines it.

    Returns:
        dict[str, str]: ``{name: submodule}``, built by importing the
        generated modules. Called only from :func:`__getattr__` and
        :func:`__dir__`, so importing this package does not trigger it.

    Imports go through :func:`importlib.import_module` rather than
    ``from . import schemas``. The ``from`` form asks the **package** for
    the attribute, which lands back in :func:`__getattr__`, which calls
    this — unbounded recursion, and the traceback blames the last frame
    rather than the loop.
    """
    from importlib import import_module

    mapping: dict[str, str] = {}
    for module_name in _GENERATED_MODULES:
        module = import_module(f"{__name__}.{module_name}")
        mapping.update(dict.fromkeys(module.__all__, module_name))
    return mapping


def __getattr__(name: str) -> Any:
    """Resolve a generated or hand-written name, or a submodule, on first access.

    Args:
        name (str): The attribute being looked up.

    Returns:
        Any: The class, function, constant, client or submodule.

    Raises:
        AttributeError: If no submodule of this package defines ``name``.
    """
    from importlib import import_module

    if name in (*_GENERATED_MODULES, _HAND_WRITTEN_MODULE):
        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module

    if name in _HAND_WRITTEN:
        module_name: str | None = _HAND_WRITTEN_MODULE
    else:
        module_name = _generated_names().get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """List everything importable from this package.

    Returns:
        list[str]: Hand-written names plus every generated one, sorted, so
        autocompletion and ``help()`` see the whole surface.
    """
    return sorted({*_HAND_WRITTEN, *_generated_names()})


__all__: list[str] = [
    "DEFAULT_BASE_URL",
    "ZAP_INBOUND_EVENT",
    "ZAP_WEBHOOK_SIGNATURE_HEADER",
    "ZAP_WEBHOOK_SIGNATURE_PREFIX",
    "AcceptedResponse",
    "AcceptedResponseStatus",
    "CheckNumberResponse",
    "ErrorResponse",
    "HistoryMessage",
    "HistoryMessageDirection",
    "HistoryResponse",
    "QrResponse",
    "ReactionRequest",
    "ReadRequest",
    "SendAudioBase64Request",
    "SendAudioRequest",
    "SendDocumentBase64Request",
    "SendDocumentRequest",
    "SendImageBase64Request",
    "SendImageRequest",
    "SendTextRequest",
    "SendVideoBase64Request",
    "SendVideoRequest",
    "SessionStartResponse",
    "SessionStatusResponse",
    "SessionStatusResponseStatus",
    "TypingRequest",
    "TypingRequestState",
    "UploadAudioForm",
    "UploadDocumentForm",
    "UploadImageForm",
    "UploadVideoForm",
    "ZapClient",
    "ZapInboundMediaType",
    "ZapInboundMessage",
    "ZapStatusCallback",
    "ZapWebhookDelivery",
    "ZapWebhookEvent",
    "is_forward_transition",
    "make_zap_webhook_dependency",
    "webhook_verifier",
]
