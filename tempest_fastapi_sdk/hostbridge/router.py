"""An opt-in FastAPI router that exposes the host control surface over HTTP.

Mounting this publishes remote code execution and a power switch. That is
why ``dependencies`` is a **required** argument with no default: a router
whose auth is optional is a router that ships unauthenticated the first time
someone is in a hurry, and the blast radius here is the whole machine.

    from fastapi import Depends
    from tempest_fastapi_sdk.hostbridge import HostBridge, make_hostbridge_router

    app.include_router(
        make_hostbridge_router(
            HostBridge(config),
            dependencies=[Depends(require_admin)],
        ),
    )

Two surfaces, split by what they cost to get wrong. The read side — host
info, reading a file, listing a directory, reading a PDF — is always
mounted. The write side — running a command, writing or deleting a file,
powering the machine off, logging the user out — is behind
``destructive=True``, off by default, so a service that only wants to read
the host cannot hand out a shell by omission.

Bind whatever serves this to loopback unless something on another host
genuinely needs it, and put a firewall rule in front of it when it is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Query

from tempest_fastapi_sdk.hostbridge.bridge import HostBridge
from tempest_fastapi_sdk.hostbridge.schemas import (
    CommandResultSchema,
    CommandSchema,
    DirectoryListingSchema,
    FileContentSchema,
    FileDeleteSchema,
    FilePickResultSchema,
    FilePickSchema,
    FileWriteSchema,
    HostInfoSchema,
    HostMessageSchema,
    HostPdfSchema,
    PowerActionSchema,
)
from tempest_fastapi_sdk.pdf.pages import PdfExtractor

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi.params import Depends as DependsMarker


def make_hostbridge_router(
    bridge: HostBridge,
    *,
    dependencies: Sequence[DependsMarker],
    prefix: str = "/system",
    tags: list[str] | None = None,
    destructive: bool = False,
) -> APIRouter:
    """Build a router over a configured :class:`HostBridge`.

    Args:
        bridge (HostBridge): The configured bridge every route calls. It is
            captured in the closure rather than resolved per request: the
            configuration is process-wide, and making it injectable would be
            one more place a permissive default could enter.
        dependencies (Sequence[Depends]): Auth (and anything else) applied to
            every route. Required, and an empty sequence is refused — see
            this module's docstring for why.
        prefix (str): Path prefix for the routes. Defaults to ``"/system"``.
        tags (list[str] | None): OpenAPI tags. Defaults to ``["system"]``.
        destructive (bool): Also mount the write side — command execution,
            file writes and deletes, power actions. Defaults to ``False``.

    Returns:
        APIRouter: The router, ready for ``app.include_router``.

    Raises:
        ValueError: When ``dependencies`` is empty.
    """
    if not dependencies:
        raise ValueError(
            "make_hostbridge_router requires at least one dependency: these "
            "routes read the host's filesystem and, with destructive=True, "
            "run commands and power the machine off."
        )

    router = APIRouter(
        prefix=prefix,
        tags=list(tags) if tags is not None else ["system"],
        dependencies=list(dependencies),
    )

    @router.get("/info", response_model=HostInfoSchema)
    async def host_info() -> HostInfoSchema:
        """Report the host's name, user, OS version and uptime."""
        return await bridge.host_info()

    @router.get("/files", response_model=FileContentSchema)
    async def read_file(
        path: Annotated[str, Query(min_length=1)],
        encoding: str = "utf-8",
    ) -> FileContentSchema:
        """Read a text file from the host."""
        return await bridge.read_text(path, encoding=encoding)

    @router.get("/files/list", response_model=DirectoryListingSchema)
    async def list_dir(
        path: Annotated[str, Query(min_length=1)],
    ) -> DirectoryListingSchema:
        """List a directory on the host."""
        return await bridge.list_dir(path)

    @router.get("/files/pdf", response_model=HostPdfSchema)
    async def read_pdf(
        path: Annotated[str, Query(min_length=1)],
        extractor: PdfExtractor = PdfExtractor.TEXT,
        password: str | None = None,
    ) -> HostPdfSchema:
        """Read a PDF on the host, page by page."""
        return await bridge.read_pdf(path, extractor=extractor, password=password)

    @router.post("/files/pick", response_model=FilePickResultSchema)
    async def pick_file(payload: FilePickSchema) -> FilePickResultSchema:
        """Open the host's native file-picker and return what was chosen."""
        return await bridge.pick_file(payload)

    if destructive:
        _mount_destructive(router, bridge)

    return router


def _mount_destructive(router: APIRouter, bridge: HostBridge) -> None:
    """Add the write side of the surface to a router.

    Split out so the read-only shape is the one a reader sees first, and so
    the decision to publish a shell is one branch in one place.

    Args:
        router (APIRouter): The router being built.
        bridge (HostBridge): The configured bridge the routes call.
    """

    @router.post("/exec", response_model=CommandResultSchema)
    async def run_command(payload: CommandSchema) -> CommandResultSchema:
        """Run a command on the host and return everything it said."""
        return await bridge.run_command(payload)

    @router.post("/files", response_model=HostMessageSchema)
    async def write_file(payload: FileWriteSchema) -> HostMessageSchema:
        """Write text to a file on the host."""
        return HostMessageSchema(message=await bridge.write_text(payload))

    @router.delete("/files", response_model=HostMessageSchema)
    async def delete_file(
        path: Annotated[str, Query(min_length=1)],
        missing_ok: bool = False,
    ) -> HostMessageSchema:
        """Delete a file, or an empty directory, on the host."""
        return HostMessageSchema(
            message=await bridge.delete(
                FileDeleteSchema(path=path, missing_ok=missing_ok)
            )
        )

    @router.post("/shutdown", response_model=HostMessageSchema)
    async def shutdown(payload: PowerActionSchema) -> HostMessageSchema:
        """Schedule the host to power off."""
        return HostMessageSchema(message=await bridge.shutdown(payload))

    @router.post("/restart", response_model=HostMessageSchema)
    async def restart(payload: PowerActionSchema) -> HostMessageSchema:
        """Schedule the host to restart."""
        return HostMessageSchema(message=await bridge.restart(payload))

    @router.post("/abort", response_model=HostMessageSchema)
    async def abort_shutdown() -> HostMessageSchema:
        """Cancel a shutdown or restart that has not fired yet."""
        return HostMessageSchema(message=await bridge.abort_shutdown())

    @router.post("/lock", response_model=HostMessageSchema)
    async def lock() -> HostMessageSchema:
        """Lock the host's active session."""
        return HostMessageSchema(message=await bridge.lock())

    @router.post("/logoff", response_model=HostMessageSchema)
    async def logoff() -> HostMessageSchema:
        """Log the host's active user off."""
        return HostMessageSchema(message=await bridge.logoff())


__all__: list[str] = ["make_hostbridge_router"]
