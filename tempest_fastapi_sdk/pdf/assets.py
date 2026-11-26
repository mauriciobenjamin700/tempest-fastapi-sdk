"""What a template is allowed to load while it renders.

An HTML renderer resolves URLs on the page's behalf: ``<img src>``,
``@import``, ``<link rel=stylesheet>``, ``url()`` in CSS. Point that at
a document whose contents came from a user and it becomes two bugs at
once — ``file:///etc/passwd`` reads the host, and
``http://169.254.169.254/`` reaches the cloud metadata endpoint from
inside your network. Neither needs the attacker to see the PDF: an image
that fails to load still leaks through timing, and one that loads is
rendered into a document somebody downloads.

So the fetcher here **denies by default** and the policy is an explicit
argument, not an inference. ``data:`` URIs are always allowed because
they carry their own bytes and fetch nothing; everything else has to be
named.

The second half is refusing *loudly*. WeasyPrint's default is to log a
failed fetch and carry on, so a blocked logo would become a silent hole
in an invoice. The fetcher built here carries ``_fail_on_errors``, which
makes the first refusal abort the render outright — no layout is spent
on a document that was going to be wrong. The lenient mode is opt-in and
still records what it dropped.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from tempest_fastapi_sdk.exceptions import ValidationException

DEFAULT_MAX_ASSET_BYTES: int = 5 * 1024 * 1024
"""Ceiling for a single fetched asset, in bytes.

A template that pulls a 400 MB image does not fail — it renders, slowly,
holding the whole thing in memory per concurrent request. 5 MiB fits any
logo, signature or chart a document needs and turns the pathological
case into an error instead of an outage.
"""

DEFAULT_REMOTE_TIMEOUT: float = 5.0
"""Seconds a remote asset has to answer.

The default renderer timeout is 10 seconds *per asset*, which a page
with twenty images turns into a request that outlives any sane gateway.
"""


class AssetRefused(ValidationException):
    """Raised when a template tried to load something the policy denies.

    Carries every refusal from one render rather than the first, so a
    template with three blocked references is fixed in one pass.
    """

    code: str = "PDF_ASSET_REFUSED"
    message: str = "The document referenced an asset that is not allowed"


@dataclass(slots=True)
class AssetPolicy:
    """Which URLs a template may resolve while rendering.

    The default denies every fetch. That is deliberate: the bundled
    templates need no fetching at all — their CSS is handed to the
    renderer directly and images arrive as ``data:`` URIs — so the
    permissive settings exist for templates that genuinely need a local
    asset directory, and turning them on is a decision somebody makes on
    purpose.

    Attributes:
        allow_dirs (tuple[Path, ...]): Directories whose files a
            template may load through ``file://``. A path is accepted
            only when it resolves *inside* one of them, so neither
            ``../`` nor a symlink pointing outside gets through.
        allow_remote (bool): Whether ``http://`` and ``https://`` may be
            fetched at all. ``False`` (default) — leaving it on means
            anything that reaches the template controls a request made
            from inside your network.
        remote_timeout (float): Seconds a remote asset has to answer.
        max_bytes (int): Ceiling for a single asset.
        refusals (list[str]): Human-readable reasons collected by
            :meth:`refuse`. :class:`~tempest_fastapi_sdk.pdf.PdfRenderer`
            never writes here: every render fetches through its own
            :meth:`for_render` copy and reports what it refused on the
            ``AssetRefused`` it raises (or the warning it logs), so this
            list only fills when you call :func:`build_url_fetcher` with
            this policy yourself.
    """

    allow_dirs: tuple[Path, ...] = ()
    allow_remote: bool = False
    remote_timeout: float = DEFAULT_REMOTE_TIMEOUT
    max_bytes: int = DEFAULT_MAX_ASSET_BYTES
    refusals: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Resolve the allowed directories once, at construction.

        Resolving here rather than per fetch means a symlink swapped
        after the policy was built cannot widen it, and it fixes the
        comparison base for :meth:`resolve_local_path`.

        Raises:
            ValueError: If any allowed directory does not exist. A typo
                would otherwise read as "nothing is allowed" and surface
                much later as a document missing its images.
        """
        resolved: list[Path] = []
        for directory in self.allow_dirs:
            path = Path(directory).resolve()
            if not path.is_dir():
                raise ValueError(f"allow_dirs entry is not a directory: {directory}")
            resolved.append(path)
        self.allow_dirs = tuple(resolved)

    def refuse(self, url: str, reason: str) -> None:
        """Record a refusal and raise, so the fetch does not succeed.

        Args:
            url (str): The URL the template asked for.
            reason (str): Why the policy denied it.

        Raises:
            AssetRefused: Always. The renderer catches it, notes the
                asset is missing, and continues; the collected reason is
                what turns that into a real error at the end.
        """
        note = f"{url} — {reason}"
        self.refusals.append(note)
        raise AssetRefused(message=f"asset refused: {note}")

    def for_render(self) -> AssetPolicy:
        """Return a copy with the same rules and an empty refusal list.

        A policy is configuration plus a collector, and only the
        configuration may be shared. One instance handed to several
        concurrent renders would let each read — and clear — the others'
        refusals, so one request's error could carry another request's
        URL, presigned token included. Each render fetches through its
        own copy instead.

        The copy is shallow and skips ``__post_init__``: ``allow_dirs``
        keeps the paths resolved when this policy was built, so a
        symlink swapped since then still cannot widen it, and a
        directory removed since then does not make the render raise
        ``ValueError``.

        Returns:
            AssetPolicy: A policy enforcing the same rules, whose
            ``refusals`` belongs to the caller alone.
        """
        clone = copy.copy(self)
        clone.refusals = []
        return clone

    def take_refusals(self) -> list[str]:
        """Return the refusals collected so far and reset the list.

        Returns:
            list[str]: One entry per refused URL, in the order the
            template referenced them.
        """
        collected = list(self.refusals)
        self.refusals.clear()
        return collected

    def resolve_local_path(self, url: str) -> Path:
        """Map a ``file://`` URL to a path inside an allowed directory.

        Args:
            url (str): The ``file://`` URL from the document.

        Returns:
            Path: The resolved, allowed path.

        Raises:
            AssetRefused: When no directory is allowed, or the resolved
                path escapes all of them.
        """
        if not self.allow_dirs:
            self.refuse(url, "local files are not allowed by this policy")
        parts = urlsplit(url)
        if parts.netloc not in ("", "localhost"):
            self.refuse(url, f"file:// host {parts.netloc!r} is not local")
        path = Path(url2pathname(unquote(parts.path))).resolve()
        for allowed in self.allow_dirs:
            if path == allowed or path.is_relative_to(allowed):
                if not path.is_file():
                    self.refuse(url, "file does not exist")
                return path
        self.refuse(url, "path is outside every allowed directory")
        raise AssertionError("unreachable")  # pragma: no cover


class UrlFetcherResponse(Protocol):
    """Subset of ``weasyprint.urls.URLFetcherResponse`` returned by a fetcher.

    WeasyPrint does not ship a ``py.typed`` marker, so callers receive this
    protocol instead of ``Any`` and keep typed access to the response bytes
    and media type.
    """

    url: str
    status: int

    @property
    def content_type(self) -> str:
        """MIME media type of the fetched asset."""
        ...

    def read(self) -> bytes:
        """Read the fetched asset bytes."""
        ...

    def close(self) -> None:
        """Close the underlying stream."""
        ...


class UrlFetcher(Protocol):
    """Callable returned by :func:`build_url_fetcher` for WeasyPrint.

    Attributes:
        _fail_on_errors (bool): Read by WeasyPrint to decide whether a
            failed fetch aborts the render or is only logged.
    """

    _fail_on_errors: bool

    def __call__(self, url: str, /) -> UrlFetcherResponse:
        """Resolve one URL under the configured :class:`AssetPolicy`."""
        ...


class _PolicyUrlFetcher:
    """URL fetcher enforcing an :class:`AssetPolicy` for WeasyPrint."""

    def __init__(self, policy: AssetPolicy, *, fail_on_errors: bool) -> None:
        """Bind ``policy`` and WeasyPrint's ``_fail_on_errors`` flag.

        Args:
            policy (AssetPolicy): The policy to enforce.
            fail_on_errors (bool): Whether fetch failures abort rendering.
        """
        self._policy: AssetPolicy = policy
        self._fail_on_errors: bool = fail_on_errors

    def __call__(self, url: str) -> UrlFetcherResponse:
        """Resolve one URL under the policy.

        Args:
            url (str): The URL the document referenced.

        Returns:
            UrlFetcherResponse: A ``URLFetcherResponse`` carrying the asset.

        Raises:
            AssetRefused: When the policy denies the URL.
        """
        from weasyprint.urls import URLFetcher, URLFetcherResponse

        scheme = urlsplit(url).scheme.lower()
        if scheme == "data":
            return cast(
                "UrlFetcherResponse",
                URLFetcher(allowed_protocols=("data",)).fetch(url),
            )
        if scheme == "file":
            path = self._policy.resolve_local_path(url)
            payload = path.read_bytes()
            if len(payload) > self._policy.max_bytes:
                self._policy.refuse(
                    url, f"asset is larger than {self._policy.max_bytes} bytes"
                )
            return cast(
                "UrlFetcherResponse",
                URLFetcherResponse(
                    url,
                    body=payload,
                    headers={
                        "Content-Type": _guess_mime(path) or "application/octet-stream"
                    },
                ),
            )
        if scheme in ("http", "https"):
            if not self._policy.allow_remote:
                self._policy.refuse(url, "remote assets are not allowed by this policy")
            return cast(
                "UrlFetcherResponse",
                URLFetcher(
                    timeout=self._policy.remote_timeout,
                    allowed_protocols=("http", "https"),
                ).fetch(url),
            )
        self._policy.refuse(url, f"scheme {scheme!r} is not allowed")
        raise AssertionError("unreachable")  # pragma: no cover


def build_url_fetcher(
    policy: AssetPolicy, *, fail_on_errors: bool = True
) -> UrlFetcher:
    """Build the fetch callable the renderer consults for every URL.

    The returned callable carries ``_fail_on_errors``, which WeasyPrint
    reads off it to decide whether a failed fetch stops the render or is
    only logged. Without it, a refused asset would silently become a
    document with a hole in it.

    Args:
        policy (AssetPolicy): The policy to enforce. Every refusal is
            appended to ``policy.refusals``, so give each render its own
            collector — :meth:`AssetPolicy.for_render` — rather than one
            instance shared across concurrent calls.
            :class:`~tempest_fastapi_sdk.pdf.PdfRenderer` does exactly
            that on every render.
        fail_on_errors (bool): Sets ``_fail_on_errors`` on the returned
            callable. ``True`` (default) stops at the first refusal
            instead of laying out a document that is already missing
            something.

    Returns:
        UrlFetcher: A ``(url) -> UrlFetcherResponse`` callable matching
        WeasyPrint's ``url_fetcher`` contract.
    """
    return _PolicyUrlFetcher(policy, fail_on_errors=fail_on_errors)


def _guess_mime(path: Path) -> str | None:
    """Guess an asset's media type from its name.

    Args:
        path (Path): The resolved file.

    Returns:
        str | None: The media type, or ``None`` when unknown — the
        renderer then sniffs the content itself.
    """
    from mimetypes import guess_type

    return guess_type(path.name)[0]


__all__: list[str] = [
    "DEFAULT_MAX_ASSET_BYTES",
    "DEFAULT_REMOTE_TIMEOUT",
    "AssetPolicy",
    "AssetRefused",
    "UrlFetcher",
    "UrlFetcherResponse",
    "build_url_fetcher",
]
