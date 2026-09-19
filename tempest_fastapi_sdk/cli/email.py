"""``tempest email`` — prove the SMTP configuration before a user does.

The pair that costs the most time in email setup is STARTTLS (port 587,
``SMTP_USE_TLS``) against implicit TLS (port 465, ``SMTP_USE_SSL``), and
the only honest way to tell which one a server wants is to connect and
send. ``email test`` does exactly that, through
``EmailUtils(**settings.email_kwargs())`` — the same construction the
service performs, so a message that arrives here arrives from the app.
"""

from __future__ import annotations

import asyncio
from typing import Any

import typer

from tempest_fastapi_sdk.cli.project import load_project_settings

email_app: typer.Typer = typer.Typer(
    name="email",
    help="Send a test message through the project's SMTP settings.",
    no_args_is_help=True,
)

_REQUIRED_FIELDS: tuple[str, ...] = ("SMTP_HOST", "SMTP_PORT", "SMTP_FROM_ADDR")
"""Fields ``email_kwargs()`` needs, checked before it is called.

A project composes only the mixins it uses, so a ``Settings`` without
``EmailSettings`` is ordinary, not broken — it has to read as "this
project has no email configured", not as an ``AttributeError``.
"""


@email_app.command("test")
def email_test(
    to: str = typer.Option(
        ...,
        "--to",
        "-t",
        help="Recipient address.",
    ),
    subject: str = typer.Option(
        "tempest email test",
        "--subject",
        "-s",
        help="Subject line.",
    ),
    body: str = typer.Option(
        "",
        "--body",
        "-b",
        help="Plain-text body. Defaults to a line naming the host it came from.",
    ),
    html: bool = typer.Option(
        False,
        "--html",
        help="Also send an HTML alternative, exercising the multipart path.",
    ),
) -> None:
    """Send one message with the project's own SMTP settings.

    Raises:
        typer.Exit: Exit code 2 when the project composes no
            ``EmailSettings`` or the ``[email]`` extra is missing; code
            1 when the server refuses the message, carrying its reply.
    """
    settings = load_project_settings()
    missing = [field for field in _REQUIRED_FIELDS if not hasattr(settings, field)]
    if settings is None or missing:
        typer.echo(
            "error: this project has no SMTP settings. Compose EmailSettings "
            "into your Settings class (missing: "
            f"{', '.join(missing) or 'the settings module itself'}).",
            err=True,
        )
        raise typer.Exit(2)

    kwargs: dict[str, Any] = settings.email_kwargs()
    text = body or (
        f"Sent by 'tempest email test' through {kwargs['host']}:{kwargs['port']}."
    )

    async def _send() -> None:
        from tempest_fastapi_sdk import EmailUtils

        mailer = EmailUtils(**kwargs)
        await mailer.send(
            to,
            subject,
            text,
            html=f"<p>{text}</p>" if html else None,
        )

    try:
        asyncio.run(_send())
    except ImportError as exc:
        typer.echo(
            "error: sending email needs the [email] extra. Install it: "
            'uv add "tempest-fastapi-sdk[email]".',
            err=True,
        )
        raise typer.Exit(2) from exc
    except Exception as exc:
        typer.echo(
            f"error: {kwargs['host']}:{kwargs['port']} refused the message — "
            f"{type(exc).__name__}: {exc}",
            err=True,
        )
        typer.echo(
            "       Port 587 wants SMTP_USE_TLS (STARTTLS); port 465 wants "
            "SMTP_USE_SSL (implicit TLS).",
            err=True,
        )
        raise typer.Exit(1) from exc

    typer.echo(f"Sent to {to} via {kwargs['host']}:{kwargs['port']}.")


__all__: list[str] = [
    "email_app",
]
