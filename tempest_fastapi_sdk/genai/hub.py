"""Weight lifecycle for the HuggingFace models a service self-hosts.

Loading a model by id is the easy half. The other half is everything the
first ``from_pretrained`` call hides: which commit you actually got, how
many gigabytes it wrote to which directory, whether the disk had room,
and how to make the next boot reproduce the same weights without a
network.

This module owns that half.

* :class:`ModelRef` — the identity of a set of weights (id + revision +
  cache + token + offline/remote-code flags). Every loader in
  :mod:`tempest_fastapi_sdk.genai` builds one and forwards it, so
  pinning a revision is the same keyword everywhere.
* :func:`resolve_revision` — turn a moving branch (``"main"``) into the
  immutable commit sha to pin in configuration.
* :func:`model_disk_bytes` — how much the repo weighs, read from the Hub
  metadata, without downloading it.
* :func:`download_model` — fetch the weights **ahead of** the first
  request (image build, deploy step, warm-up task), refusing to start
  when the disk cannot hold them.
* :func:`list_cached_models` / :func:`cache_size_bytes` /
  :func:`remove_cached_model` — see and reclaim what the cache holds.

``huggingface_hub`` is imported inside the functions that need it, so
this module imports with no extra installed and the schemas stay usable
(and testable) on a host that will never download anything.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from pydantic import Field

from tempest_fastapi_sdk.schemas.base import BaseSchema

_HUB_EXTRA_HINT: str = (
    "Talking to the HuggingFace Hub requires huggingface_hub. "
    "Install with: pip install tempest-fastapi-sdk[genai-hub] "
    "(already included in [genai])"
)


def _require_hub() -> Any:
    """Import ``huggingface_hub`` or raise a helpful error.

    Returns:
        Any: The ``huggingface_hub`` module.

    Raises:
        ImportError: When neither the ``[genai-hub]`` nor the ``[genai]``
            extra is installed.
    """
    try:
        import huggingface_hub
    except ImportError as exc:
        raise ImportError(_HUB_EXTRA_HINT) from exc
    return huggingface_hub


class ModelRef(BaseSchema):
    """Everything needed to resolve one set of weights, reproducibly.

    A bare model id names a moving target: the author pushes to ``main``
    and the next restart loads different weights. Pinning ``revision``
    to a commit sha (see :func:`resolve_revision`) makes a deployment
    reproducible and removes a supply-chain surface — the weights a
    service runs stop depending on when it happened to boot.

    Example:

        >>> ref = ModelRef(
        ...     model_id="Qwen/Qwen2.5-0.5B-Instruct",
        ...     revision="a8b602d",
        ...     local_files_only=True,
        ... )
        >>> ref.loader_kwargs()
        {'revision': 'a8b602d', 'local_files_only': True}

    Attributes:
        model_id (str): The Hub model id (``"org/name"``) or a local path.
        revision (str | None): Branch, tag or commit sha. ``None`` means
            the Hub default (``main``) — convenient, not reproducible.
        cache_dir (str | None): Where weights are cached. ``None`` uses
            the ``HF_HUB_CACHE`` default.
        token (str | None): Hub token for gated or private repositories.
        local_files_only (bool): When ``True``, never touch the network:
            load from the cache or fail. This is what an air-gapped or
            deploy-frozen production host wants.
        trust_remote_code (bool): When ``True``, allow the repository's
            own Python to run at load time. Some architectures require
            it; it also executes code you did not review, so it stays
            opt-in and per-model.
    """

    model_id: str = Field(
        title="Model id",
        description="Hub model id (org/name) or a local path.",
        examples=["Qwen/Qwen2.5-0.5B-Instruct"],
    )
    revision: str | None = Field(
        default=None,
        title="Revision",
        description="Branch, tag or commit sha. None means the Hub default.",
        examples=["main", "a8b602d5f1c9"],
    )
    cache_dir: str | None = Field(
        default=None,
        title="Cache directory",
        description="Weight cache directory; None uses the HF_HUB_CACHE default.",
        examples=["/var/lib/models"],
    )
    token: str | None = Field(
        default=None,
        title="Hub token",
        description="Token for gated or private repositories.",
        examples=["hf_xxx"],
    )
    local_files_only: bool = Field(
        default=False,
        title="Offline only",
        description="Load from cache only; never reach the network.",
        examples=[True],
    )
    trust_remote_code: bool = Field(
        default=False,
        title="Trust remote code",
        description="Allow the repository's own Python to run at load time.",
        examples=[False],
    )

    def loader_kwargs(self) -> dict[str, Any]:
        """Return the ``from_pretrained`` keywords this ref implies.

        Only non-default values are emitted. That keeps the call
        byte-identical to what the loaders sent before this ref existed
        when nothing was pinned, and it keeps the same dictionary usable
        with narrower loaders (``tokenizers.Tokenizer.from_pretrained``
        accepts ``revision`` but not ``trust_remote_code``).

        ``model_id`` is not included — it is the positional argument.

        Returns:
            dict[str, Any]: Keywords to splat into ``from_pretrained``.
        """
        kwargs: dict[str, Any] = {}
        if self.cache_dir is not None:
            kwargs["cache_dir"] = self.cache_dir
        if self.token is not None:
            kwargs["token"] = self.token
        if self.revision is not None:
            kwargs["revision"] = self.revision
        if self.local_files_only:
            kwargs["local_files_only"] = True
        if self.trust_remote_code:
            kwargs["trust_remote_code"] = True
        return kwargs

    def download_kwargs(self) -> dict[str, Any]:
        """Return the ``snapshot_download`` keywords this ref implies.

        Same values as :meth:`loader_kwargs` minus ``trust_remote_code``,
        which is a load-time decision and means nothing while fetching
        files.

        Returns:
            dict[str, Any]: Keywords to splat into ``snapshot_download``.
        """
        kwargs = self.loader_kwargs()
        kwargs.pop("trust_remote_code", None)
        return kwargs


class ModelSnapshot(BaseSchema):
    """The result of materializing one revision on local disk.

    Attributes:
        model_id (str): The Hub model id that was downloaded.
        revision (str | None): The revision asked for (``None`` for the
            Hub default).
        path (str): Local directory holding the snapshot.
        size_bytes (int): Total size of the downloaded files.
        file_count (int): Number of files in the snapshot.
    """

    model_id: str = Field(
        title="Model id",
        description="The Hub model id that was downloaded.",
        examples=["Qwen/Qwen2.5-0.5B-Instruct"],
    )
    revision: str | None = Field(
        default=None,
        title="Revision",
        description="The revision asked for; None for the Hub default.",
        examples=["main"],
    )
    path: str = Field(
        title="Snapshot path",
        description="Local directory holding the downloaded files.",
        examples=["/home/u/.cache/huggingface/hub/models--org--name/snapshots/abc"],
    )
    size_bytes: int = Field(
        title="Size on disk",
        description="Total size of the downloaded files, in bytes.",
        examples=[988_000_000],
    )
    file_count: int = Field(
        title="File count",
        description="Number of files in the snapshot.",
        examples=[9],
    )


class CachedRevision(BaseSchema):
    """One revision of a model present in the local cache.

    Attributes:
        revision (str): The commit sha the snapshot belongs to.
        refs (list[str]): Branch/tag names pointing at this commit.
        size_bytes (int): Bytes this revision occupies on disk.
        path (str): The snapshot directory.
        last_modified (float | None): Unix timestamp of the last change,
            or ``None`` when the cache does not report one.
    """

    revision: str = Field(
        title="Revision",
        description="The commit sha the snapshot belongs to.",
        examples=["a8b602d5f1c9"],
    )
    refs: list[str] = Field(
        default_factory=list,
        title="Refs",
        description="Branch/tag names pointing at this commit.",
        examples=[["main"]],
    )
    size_bytes: int = Field(
        title="Size on disk",
        description="Bytes this revision occupies on disk.",
        examples=[988_000_000],
    )
    path: str = Field(
        title="Snapshot path",
        description="The snapshot directory.",
        examples=["/home/u/.cache/huggingface/hub/models--org--name/snapshots/abc"],
    )
    last_modified: float | None = Field(
        default=None,
        title="Last modified",
        description="Unix timestamp of the last change, when reported.",
        examples=[1_754_000_000.0],
    )


class CachedModel(BaseSchema):
    """One model repository present in the local cache.

    Attributes:
        model_id (str): The Hub model id.
        size_bytes (int): Total bytes the repository occupies, counting
            every cached revision once (blobs shared between revisions
            are not double-counted).
        path (str): The repository directory inside the cache.
        revisions (list[CachedRevision]): The cached revisions, largest
            first.
    """

    model_id: str = Field(
        title="Model id",
        description="The Hub model id.",
        examples=["Qwen/Qwen2.5-0.5B-Instruct"],
    )
    size_bytes: int = Field(
        title="Size on disk",
        description="Total bytes the repository occupies in the cache.",
        examples=[988_000_000],
    )
    path: str = Field(
        title="Repository path",
        description="The repository directory inside the cache.",
        examples=["/home/u/.cache/huggingface/hub/models--org--name"],
    )
    revisions: list[CachedRevision] = Field(
        default_factory=list,
        title="Revisions",
        description="The cached revisions, largest first.",
    )


def resolve_revision(
    model_id: str,
    *,
    revision: str = "main",
    token: str | None = None,
) -> str | None:
    """Resolve a branch or tag to the immutable commit sha behind it.

    Pin the returned sha in configuration and the service stops loading
    whatever ``main`` happens to hold at boot.

    Example:

        >>> sha = resolve_revision("Qwen/Qwen2.5-0.5B-Instruct")
        >>> isinstance(sha, str) or sha is None
        True

    Args:
        model_id (str): The Hub model id.
        revision (str): Branch, tag or sha to resolve. Defaults to
            ``"main"``.
        token (str | None): Token for gated or private repositories.

    Returns:
        str | None: The commit sha, or ``None`` when the Hub cannot be
        reached or the repository/revision does not exist. Callers treat
        ``None`` as "could not pin", never as an error to swallow
        silently — the caller decides whether to proceed unpinned.

    Raises:
        ImportError: When ``huggingface_hub`` is not installed.
    """
    hub = _require_hub()
    try:
        info = hub.HfApi().model_info(model_id, revision=revision, token=token)
    except Exception:
        return None
    sha = getattr(info, "sha", None)
    return str(sha) if sha else None


def model_disk_bytes(
    model_id: str,
    *,
    revision: str | None = None,
    token: str | None = None,
) -> int | None:
    """Return how many bytes a repository would occupy, without fetching it.

    Reads the file sizes from the Hub metadata. Use it to decide whether
    a download is worth starting — :func:`can_run` sizes RAM and VRAM,
    this sizes the disk.

    Args:
        model_id (str): The Hub model id.
        revision (str | None): Branch, tag or sha; ``None`` for the Hub
            default.
        token (str | None): Token for gated or private repositories.

    Returns:
        int | None: Total size in bytes, or ``None`` when the Hub is
        unreachable or reports no per-file sizes.

    Raises:
        ImportError: When ``huggingface_hub`` is not installed.
    """
    hub = _require_hub()
    try:
        info = hub.HfApi().model_info(
            model_id,
            revision=revision,
            files_metadata=True,
            token=token,
        )
    except Exception:
        return None
    siblings = getattr(info, "siblings", None) or []
    sizes = [getattr(sibling, "size", None) for sibling in siblings]
    known = [int(size) for size in sizes if size]
    if not known:
        return None
    return sum(known)


def _cache_root(cache_dir: str | None) -> str:
    """Return the directory whose free space a download would consume.

    Args:
        cache_dir (str | None): An explicit cache directory, or ``None``
            for the ``huggingface_hub`` default.

    Returns:
        str: An existing directory to measure free space on — the
        nearest existing parent when the cache itself is not created
        yet, since ``shutil.disk_usage`` needs a path that exists.
    """
    root = cache_dir
    if root is None:
        try:
            hub = _require_hub()
            root = str(hub.constants.HF_HUB_CACHE)
        except ImportError:
            root = str(Path.home() / ".cache" / "huggingface" / "hub")
    path = Path(root)
    while not path.exists() and path != path.parent:
        path = path.parent
    return str(path)


def download_model(
    model_id: str,
    *,
    revision: str | None = None,
    cache_dir: str | None = None,
    token: str | None = None,
    local_files_only: bool = False,
    allow_patterns: list[str] | None = None,
    ignore_patterns: list[str] | None = None,
    check_disk: bool = True,
    disk_margin: float = 1.1,
) -> ModelSnapshot:
    """Materialize a model's weights on local disk, ahead of first use.

    Left to itself, the first ``/generate`` request pays for a
    multi-gigabyte download inside the request. Call this from the image
    build, the deploy step or a warm-up task instead, and the request
    path only ever loads from disk.

    ``check_disk`` refuses to start a download the filesystem cannot
    hold. Failing in two seconds with a number beats failing forty
    minutes later with a half-written cache.

    Example:

        >>> snapshot = download_model(
        ...     "Qwen/Qwen2.5-0.5B-Instruct",
        ...     revision="main",
        ...     allow_patterns=["*.json", "*.safetensors"],
        ... )
        >>> snapshot.size_bytes > 0
        True

    Args:
        model_id (str): The Hub model id.
        revision (str | None): Branch, tag or sha; ``None`` for the Hub
            default.
        cache_dir (str | None): Where to write; ``None`` uses the
            ``HF_HUB_CACHE`` default.
        token (str | None): Token for gated or private repositories.
        local_files_only (bool): Resolve from the cache without touching
            the network (turns this call into a "is it already here?"
            check).
        allow_patterns (list[str] | None): Only fetch files matching
            these globs — e.g. skip the ``.bin`` duplicates of a repo
            that also ships ``.safetensors``.
        ignore_patterns (list[str] | None): Skip files matching these
            globs.
        check_disk (bool): Verify free space before downloading.
        disk_margin (float): Multiplier applied to the estimated size
            for the free-space check. The default 1.1 covers the
            temporary files the download writes next to the blobs.

    Returns:
        ModelSnapshot: Where the weights landed, how big they are and
        how many files they span.

    Raises:
        ImportError: When ``huggingface_hub`` is not installed.
        OSError: When ``check_disk`` is on and the target filesystem has
            less free space than the estimate times ``disk_margin``.
    """
    hub = _require_hub()
    ref = ModelRef(
        model_id=model_id,
        revision=revision,
        cache_dir=cache_dir,
        token=token,
        local_files_only=local_files_only,
    )
    if check_disk and not local_files_only:
        needed = model_disk_bytes(model_id, revision=revision, token=token)
        if needed is not None:
            required = int(needed * disk_margin)
            free = shutil.disk_usage(_cache_root(cache_dir)).free
            if free < required:
                raise OSError(
                    f"{model_id} needs ~{required / 1e9:.1f} GB "
                    f"(estimate x{disk_margin}) but only "
                    f"{free / 1e9:.1f} GB are free on "
                    f"{_cache_root(cache_dir)}",
                )
    path = hub.snapshot_download(
        model_id,
        allow_patterns=allow_patterns,
        ignore_patterns=ignore_patterns,
        **ref.download_kwargs(),
    )
    files = [item for item in Path(path).rglob("*") if item.is_file()]
    return ModelSnapshot(
        model_id=model_id,
        revision=revision,
        path=str(path),
        size_bytes=sum(item.stat().st_size for item in files),
        file_count=len(files),
    )


def list_cached_models(cache_dir: str | None = None) -> list[CachedModel]:
    """Report the models currently held in the local weight cache.

    Args:
        cache_dir (str | None): The cache to scan; ``None`` uses the
            ``HF_HUB_CACHE`` default.

    Returns:
        list[CachedModel]: One entry per cached model repository, largest
        first. Empty when the cache directory does not exist yet — an
        empty cache is a valid state, not an error.

    Raises:
        ImportError: When ``huggingface_hub`` is not installed.
    """
    hub = _require_hub()
    try:
        info = hub.scan_cache_dir(cache_dir)
    except Exception:
        return []
    models: list[CachedModel] = []
    for repo in info.repos:
        if getattr(repo, "repo_type", "model") != "model":
            continue
        revisions = [
            CachedRevision(
                revision=str(revision.commit_hash),
                refs=sorted(str(ref) for ref in (revision.refs or [])),
                size_bytes=int(revision.size_on_disk),
                path=str(revision.snapshot_path),
                last_modified=(
                    float(revision.last_modified)
                    if revision.last_modified is not None
                    else None
                ),
            )
            for revision in repo.revisions
        ]
        revisions.sort(key=lambda item: item.size_bytes, reverse=True)
        models.append(
            CachedModel(
                model_id=str(repo.repo_id),
                size_bytes=int(repo.size_on_disk),
                path=str(repo.repo_path),
                revisions=revisions,
            ),
        )
    models.sort(key=lambda item: item.size_bytes, reverse=True)
    return models


def cache_size_bytes(cache_dir: str | None = None) -> int:
    """Return the total size of the cached model repositories.

    Args:
        cache_dir (str | None): The cache to scan; ``None`` uses the
            ``HF_HUB_CACHE`` default.

    Returns:
        int: Total bytes across every cached model, or ``0`` when the
        cache does not exist yet.

    Raises:
        ImportError: When ``huggingface_hub`` is not installed.
    """
    return sum(model.size_bytes for model in list_cached_models(cache_dir))


def remove_cached_model(
    model_id: str,
    *,
    revision: str | None = None,
    cache_dir: str | None = None,
    dry_run: bool = False,
) -> int:
    """Delete a model from the local cache and report the space freed.

    Args:
        model_id (str): The Hub model id to remove.
        revision (str | None): Remove only this revision (sha or ref
            name). ``None`` removes every cached revision of the model.
        cache_dir (str | None): The cache to operate on; ``None`` uses
            the ``HF_HUB_CACHE`` default.
        dry_run (bool): Compute the freed size without deleting.

    Returns:
        int: Bytes freed (or that would be freed under ``dry_run``).
        ``0`` when the model — or that revision of it — is not cached,
        which is a successful no-op rather than an error.

    Raises:
        ImportError: When ``huggingface_hub`` is not installed.
    """
    hub = _require_hub()
    try:
        info = hub.scan_cache_dir(cache_dir)
    except Exception:
        return 0
    hashes: list[str] = []
    for repo in info.repos:
        if str(repo.repo_id) != model_id:
            continue
        if getattr(repo, "repo_type", "model") != "model":
            continue
        for cached in repo.revisions:
            commit = str(cached.commit_hash)
            refs = {str(ref) for ref in (cached.refs or [])}
            if revision is None or revision == commit or revision in refs:
                hashes.append(commit)
    if not hashes:
        return 0
    strategy = info.delete_revisions(*hashes)
    freed = int(strategy.expected_freed_size)
    if not dry_run:
        strategy.execute()
    return freed
