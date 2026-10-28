"""English (``en-US``) OpenAPI text of the application-feature routers.

Covers ``make_chat_router``, ``make_reviews_router``,
``make_wallet_router``, ``make_hostbridge_router``, ``make_htmx_router``,
``make_web_app_router``, ``make_zap_webhook_router`` and
``make_revenuecat_webhook_router``.

Only the RevenueCat webhook ever declared a ``summary``; it keeps it.
Every other entry is left ``None``, so FastAPI keeps deriving the summary
from the endpoint name and the description from its docstring, exactly
as before #643.
"""

from __future__ import annotations

from tempest_fastapi_sdk.api.route_docs.doc import RouteDoc

APPS_ROUTES: dict[str, RouteDoc] = {
    "chat.start_conversation": RouteDoc(),
    "chat.list_conversations": RouteDoc(),
    "chat.get_conversation": RouteDoc(),
    "chat.update_conversation": RouteDoc(),
    "chat.set_preferences": RouteDoc(),
    "chat.add_participants": RouteDoc(),
    "chat.remove_participant": RouteDoc(),
    "chat.leave_conversation": RouteDoc(),
    "chat.mark_read": RouteDoc(),
    "chat.post_message": RouteDoc(),
    "chat.list_messages": RouteDoc(),
    "chat.edit_message": RouteDoc(),
    "chat.revoke_message": RouteDoc(),
    "chat.react": RouteDoc(),
    "chat.unreact": RouteDoc(),
    "chat.forward_message": RouteDoc(),
    "chat.stream_messages": RouteDoc(),
    "hostbridge.host_info": RouteDoc(),
    "hostbridge.read_file": RouteDoc(),
    "hostbridge.list_dir": RouteDoc(),
    "hostbridge.read_pdf": RouteDoc(),
    "hostbridge.pick_file": RouteDoc(),
    "hostbridge.run_command": RouteDoc(),
    "hostbridge.write_file": RouteDoc(),
    "hostbridge.delete_file": RouteDoc(),
    "hostbridge.shutdown": RouteDoc(),
    "hostbridge.restart": RouteDoc(),
    "hostbridge.abort_shutdown": RouteDoc(),
    "hostbridge.lock": RouteDoc(),
    "hostbridge.logoff": RouteDoc(),
    "htmx.htmx_js": RouteDoc(),
    "htmx.confirm_js": RouteDoc(),
    "revenuecat.receive_revenuecat_webhook": RouteDoc(
        summary="Receive a RevenueCat webhook",
    ),
    "reviews.add_comment": RouteDoc(),
    "reviews.list_comments": RouteDoc(),
    "reviews.rate": RouteDoc(),
    "reviews.aggregate": RouteDoc(),
    "wallet.get_balance": RouteDoc(),
    "wallet.get_statement": RouteDoc(),
    "wallet.withdraw": RouteDoc(),
    "web_app.index": RouteDoc(),
    "web_app.asset": RouteDoc(),
    "zap.receive_zap": RouteDoc(),
}
"""Operation text of the feature routers, keyed ``<router>.<endpoint name>``."""

__all__: list[str] = ["APPS_ROUTES"]
