"""Schemas served by :func:`~tempest_fastapi_sdk.make_sse_router`."""

from pydantic import Field

from tempest_fastapi_sdk.schemas.base import BaseSchema


class SSETicketResponseSchema(BaseSchema):
    """A ticket that opens one SSE stream.

    The ticket goes in the stream URL (``?ticket=...``) because the browser
    ``EventSource`` cannot send a header. It is a JWT with
    ``typ="stream"``: every guard built on
    :func:`~tempest_fastapi_sdk.token_type_allowed` refuses it, so a ticket
    copied out of an access log opens that one stream until it expires and
    calls nothing else. It is not single-use: until it expires it opens the
    stream every time it is presented.

    Attributes:
        ticket (str): The signed ticket to pass as the stream's query
            parameter.
        expires_in (int): Seconds until the ticket can no longer open a
            stream. A stream already open is not cut when it expires; a
            reconnect after it needs a new ticket.
    """

    ticket: str = Field(
        ...,
        title="Ticket",
        description="Signed ticket for the stream's query string",
    )
    expires_in: int = Field(
        ...,
        title="Expires in",
        description="Seconds until the ticket can no longer open a stream",
        ge=1,
    )


__all__: list[str] = [
    "SSETicketResponseSchema",
]
