"""Declarative mixin for versioned, activatable binary artifacts.

A single logical artifact (``name``) owns many versions; exactly one of
them is flagged ``is_current`` at a time. The concrete table mixes this
into the SDK :class:`~tempest_fastapi_sdk.BaseModel` (optionally with
:class:`~tempest_fastapi_sdk.AuditMixin`)::

    from tempest_fastapi_sdk import BaseModel
    from tempest_fastapi_sdk.artifacts import ArtifactVersionMixin


    class ModelVersion(BaseModel, ArtifactVersionMixin):
        __tablename__ = "model_versions"

The ``is_current`` flag is deliberately distinct from ``BaseModel``'s
``is_active`` (the soft-delete flag): a version can be active (not
deleted) yet not the one currently served.

For static typing only, the mixin is declared as a
:class:`~tempest_fastapi_sdk.BaseModel` subclass so downstream generics
(``BaseRepository[TModel]``, :class:`~tempest_fastapi_sdk.artifacts.ArtifactRegistry`)
see the four base columns alongside the artifact columns. At runtime it
is a plain, unmapped mixin that composes with any concrete ``BaseModel``
table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

if TYPE_CHECKING:
    from tempest_fastapi_sdk.db.model import BaseModel

    _MixinBase = BaseModel
else:
    _MixinBase = object


class ArtifactVersionMixin(_MixinBase):
    """Columns for a versioned, activatable binary artifact.

    Mix into a concrete ``BaseModel`` table so an artifact ``name`` can
    hold many versions with exactly one ``is_current`` per name.

    Attributes:
        name (str): Indexed logical artifact key. Many versions share
            one ``name``; the manifest resolves the current one per name.
        version (str): Opaque, human-facing version label (e.g.
            ``"1.2.0"`` or a build hash). Not parsed or ordered by the
            SDK.
        file_key (str): Object-storage key of the uploaded bytes.
            Immutable once written — checksums are derived from it on
            demand and memoized by this key.
        is_current (bool): Whether this row is the version currently
            served for its ``name``. Defaults to ``False``; exactly one
            row per ``name`` should be ``True`` (enforced by the activate
            action, not a DB constraint).
    """

    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
        doc="Logical artifact key; many versions share one name.",
    )
    version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="",
        doc="Opaque version label for this upload.",
    )
    file_key: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        doc="Object-storage key of the uploaded bytes (immutable).",
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        doc="Whether this version is the one currently served for its name.",
    )


__all__: list[str] = [
    "ArtifactVersionMixin",
]
