"""English (``en-US``) OpenAPI text of the bundled non-auth routers.

Covers ``make_app_error_router``, ``make_health_router``,
``make_logs_router``, ``make_pdf_router``, ``make_push_router``,
``make_session_router``, ``make_sse_router``, ``make_tool_spec_router``,
``make_voice_router`` and ``make_web_push_router``.

A :class:`RouteDoc` field left ``None`` is one the route never declared:
FastAPI then derives the summary from the endpoint name and the
description from its docstring, exactly as before #643.
"""

from __future__ import annotations

from tempest_fastapi_sdk.api.route_docs.doc import RouteDoc

PLATFORM_ROUTES: dict[str, RouteDoc] = {
    "app_errors.report_app_error": RouteDoc(
        summary=("Record an error reported by the client application"),
    ),
    "app_errors.list_app_errors": RouteDoc(
        summary=("List reported errors for investigation"),
    ),
    "health.liveness": RouteDoc(
        summary=("Liveness probe"),
    ),
    "health.readiness": RouteDoc(
        summary=("Readiness probe"),
    ),
    "logs.read_logs": RouteDoc(
        summary=("Read structured application logs"),
    ),
    "logs.clear_logs": RouteDoc(
        summary=("Truncate structured application logs"),
    ),
    "pdf.list_documents": RouteDoc(
        summary=("List the bundled documents and their payload schemas"),
        description=(
            "Every document this service can render, with the full JSON "
            "Schema of each payload — enough for a client to build the "
            "form without reading the SDK source."
        ),
    ),
    "pdf.render_document": RouteDoc(
        summary=("Render a bundled document to PDF"),
        description=(
            "Validates ``payload`` against the selected document's schema "
            "and returns the PDF bytes.\n\n"
            "An unknown document name returns **404** naming the ones that "
            "exist; a payload that fails validation returns **422** naming "
            "the field. Rendering runs in a worker thread, so a slow "
            "document does not stall the event loop — but it does occupy "
            "one of the renderer's slots, which is why this route belongs "
            "behind the service's rate limit."
        ),
    ),
    "push.register": RouteDoc(),
    "push.unregister": RouteDoc(),
    "push.get_vapid_public_key": RouteDoc(),
    "sessions.login": RouteDoc(
        summary=("Authenticate and start a session"),
    ),
    "sessions.logout": RouteDoc(
        summary=("Revoke the current session"),
    ),
    "sessions.me": RouteDoc(
        summary=("Return the live session for the current cookie"),
    ),
    "sessions.list_sessions": RouteDoc(
        summary=("List every session the current user owns"),
    ),
    "sessions.revoke_one": RouteDoc(
        summary=("Revoke one specific session by its public id"),
    ),
    "sse.issue_ticket": RouteDoc(
        summary="Issue an SSE ticket",
        description=(
            "Trade the authenticated session for a short-lived ticket that "
            "opens `GET {prefix}/stream?{ticket_param}=...`. The ticket "
            "authorizes that stream only; ask for a new one before every "
            "reconnect."
        ),
    ),
    "sse.stream": RouteDoc(
        summary="Open the event stream",
        description=(
            "Server-Sent Events. Authenticate with `?{ticket_param}=` from "
            "`POST {prefix}/ticket`."
        ),
    ),
    "tool_spec.tool_spec": RouteDoc(),
    "tool_spec.tool_spec_static": RouteDoc(),
    "voice.transcribe": RouteDoc(
        summary=("Transcribe a conversation, split by speaker"),
        description=(
            "Uploads a recording and returns the transcript with one "
            "entry per speaker turn.\n\n"
            "**Pass `num_speakers` whenever you know it** — on a "
            "two-party call, an interview, a support conversation you "
            "do. Clustering by threshold alone is the least reliable "
            "part of the pipeline.\n\n"
            "Turns carry `speaker`, a cluster index that is stable "
            "inside this recording and meaningless across recordings. "
            "A turn with `speaker = -1` is speech that fell outside "
            "every detected turn: it is transcribed rather than "
            "dropped, because losing words silently is worse."
        ),
    ),
    "voice.enroll": RouteDoc(
        summary=("Enrol the caller's voice"),
        description=(
            "Records a voiceprint for the **authenticated caller** — "
            "the user id comes from the session, never from the "
            "body.\n\n"
            "`consent_reference` is required and identifies what the "
            "person agreed to: a policy version, a signed document, a "
            "ticket. A voiceprint is sensitive personal data and needs "
            "specific, highlighted consent; a blank reference returns "
            "**422**.\n\n"
            "The audio is used to compute the vector and discarded. "
            "Nothing stores the recording."
        ),
    ),
    "voice.list_profiles": RouteDoc(
        summary=("List the caller's enrolled voice profiles"),
        description=(
            "What is enrolled, when, and under which consent. The "
            "embedding itself is **not** returned: the person needs to "
            "know a profile exists, not to receive a copy of their own "
            "biometric template over HTTP.\n\n"
            "Returns `200` with an empty list when there are none."
        ),
    ),
    "voice.erase": RouteDoc(
        summary=("Erase every voice profile the caller enrolled"),
        description=(
            "Deletes the caller's biometric data. This is the request "
            "the LGPD makes unconditional (Art. 18, VI), which is why "
            "it is an endpoint rather than a support ticket.\n\n"
            "Deleting when there is nothing to delete returns `200` "
            "with `deleted: 0` — a successful outcome, not an error."
        ),
    ),
    "webpush.subscribe": RouteDoc(),
    "webpush.unsubscribe": RouteDoc(),
    "webpush.get_vapid_public_key": RouteDoc(),
}
"""Operation text of the non-auth routers, keyed ``<router>.<endpoint name>``."""

PLATFORM_TEXTS: dict[str, str] = {
    "health.readiness.503": "At least one dependency is not ready.",
    "pdf.render_document.200": "The rendered document.",
    "logs.read_logs.source": (
        "Which log file(s) to read. 'all' merges every level; "
        "'500' returns only isolated unhandled-500 records."
    ),
    "logs.read_logs.q": ("Case-insensitive substring match on the message."),
    "logs.read_logs.start": ("Only records at or after this ISO-8601 instant."),
    "logs.read_logs.end": ("Only records at or before this ISO-8601 instant."),
    "logs.read_logs.page": ("Page number (1-indexed)."),
    "logs.read_logs.page_size": ("Items per page."),
    "logs.clear_logs.source": (
        "Which log file(s) to truncate. 'all' empties every "
        "level **and** the isolated 500 stream; '500' empties "
        "only the latter."
    ),
    "logs.clear_logs.delete_backups": (
        "Also delete the rotated backups (info.log.1, "
        "info.log.2026-10-20, ...). Off by default: deleting them "
        "cannot be undone."
    ),
    "app_errors.list_app_errors.page": ("Página, 1-indexed."),
    "app_errors.list_app_errors.page_size": ("Itens por página."),
    "voice.transcribe.audio": ("The recording."),
    "voice.enroll.audio": ("At least 3 seconds of speech."),
    "voice.enroll.consent_reference": ("Evidence of the person's consent."),
}
"""Parameter and response descriptions declared on the non-auth routes."""

__all__: list[str] = ["PLATFORM_ROUTES", "PLATFORM_TEXTS"]
