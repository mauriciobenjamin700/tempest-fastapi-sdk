"""Admin site registry — analog of ``django.contrib.admin.AdminSite``."""

from __future__ import annotations

from types import ModuleType
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.admin.config import AdminModel
from tempest_fastapi_sdk.admin.dashboard import MetricCard
from tempest_fastapi_sdk.admin.theme import AdminTheme

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tempest_fastapi_sdk.db.model import BaseModel


class AdminSite:
    """Holds the set of :class:`AdminModel` configurations to expose.

    Each project instantiates one site, registers its admin
    configurations (one at a time with :meth:`register`, or all at once
    with :meth:`automap`), and passes the site to
    :func:`make_admin_router`.

    Attributes:
        title (str): Branding shown in the page ``<title>`` and the
            dashboard heading.
        brand (str | None): Optional override for the **centered**
            header brand text. Falls back to :attr:`title` when unset —
            read it through :attr:`brand_text`.
        index_subtitle (str): Optional subtitle for the dashboard.
        site_url (str | None): Optional "View site" link rendered in
            the admin header.
        theme (AdminTheme): Typed appearance overrides (colors, logo,
            favicon, font, footer, dark mode). Defaults to a stock
            :class:`AdminTheme`, so the look is unchanged when omitted.
        dashboard_cards (list[MetricCard]): Business-metric cards
            (value / trend / partition) rendered at the top of the
            dashboard, each computed from the DB on load. Distinct from
            the system CPU/RAM/disk panel.
    """

    def __init__(
        self,
        title: str = "Admin",
        *,
        brand: str | None = None,
        index_subtitle: str = "Site administration",
        site_url: str | None = None,
        theme: AdminTheme | None = None,
        dashboard_cards: Sequence[MetricCard] = (),
    ) -> None:
        """Initialize the site.

        Args:
            title (str): Branding text — used in the page ``<title>``
                and the dashboard heading.
            brand (str | None): Optional text for the centered header
                brand. ``None`` (default) falls back to ``title``, so
                existing sites keep their current header. Set it to
                show a distinct name (e.g. ``"servus-backend-admin"``)
                centered at the top of every page.
            index_subtitle (str): Dashboard subtitle.
            site_url (str | None): Optional outbound link rendered
                in the admin header.
            theme (AdminTheme | None): Typed appearance overrides.
                ``None`` (default) uses a stock :class:`AdminTheme`,
                leaving the look identical to earlier versions.
        """
        self.title: str = title
        self.brand: str | None = brand
        self.index_subtitle: str = index_subtitle
        self.site_url: str | None = site_url
        self.theme: AdminTheme = theme or AdminTheme()
        self.dashboard_cards: list[MetricCard] = list(dashboard_cards)
        self._registry: dict[str, AdminModel[Any]] = {}

    @property
    def brand_text(self) -> str:
        """Return the centered header brand text.

        Returns:
            str: :attr:`brand` when set, otherwise :attr:`title`.
        """
        return self.brand or self.title

    def register(self, admin: AdminModel[Any]) -> AdminModel[Any]:
        """Register ``admin`` against its model slug.

        Example::

            site.register(AdminModel(model=UserModel))

        Args:
            admin (AdminModel[Any]): The admin configuration instance.

        Returns:
            AdminModel[Any]: The same instance (so the call can be
            chained or assigned).

        Raises:
            ValueError: When another admin is already registered under
                the same slug.
        """
        slug = admin.get_slug()
        if slug in self._registry:
            existing = self._registry[slug]
            raise ValueError(
                f"AdminModel for slug {slug!r} already registered "
                f"({existing.model.__name__}); refusing to overwrite with "
                f"{admin.model.__name__}"
            )
        self._registry[slug] = admin
        return admin

    def unregister(self, slug: str) -> None:
        """Remove a previously registered admin.

        Args:
            slug (str): The slug to drop.

        Raises:
            KeyError: When no admin is registered under ``slug``.
        """
        del self._registry[slug]

    def get(self, slug: str) -> AdminModel[Any] | None:
        """Return the admin registered under ``slug``, or ``None``.

        Args:
            slug (str): The admin slug.

        Returns:
            AdminModel[Any] | None: The configuration instance.
        """
        return self._registry.get(slug)

    def require(self, slug: str) -> AdminModel[Any]:
        """Return the admin registered under ``slug`` or raise.

        Args:
            slug (str): The admin slug.

        Returns:
            AdminModel[Any]: The configuration instance.

        Raises:
            KeyError: When no admin matches the slug.
        """
        admin = self.get(slug)
        if admin is None:
            raise KeyError(f"No admin registered for slug {slug!r}")
        return admin

    @property
    def registry(self) -> dict[str, AdminModel[Any]]:
        """Return a copy of the slug→admin mapping.

        Returns:
            dict[str, AdminModel[Any]]: The current registry.
        """
        return dict(self._registry)

    def iter_models(self) -> list[AdminModel[Any]]:
        """Return registered admins ordered by display name.

        Returns:
            list[AdminModel[Any]]: Ordered admin instances.
        """
        return sorted(
            self._registry.values(),
            key=lambda admin: admin.get_verbose_name_plural().lower(),
        )

    def automap(
        self,
        source: str | ModuleType,
        *,
        exclude: Sequence[type[BaseModel] | str] = (),
        skip_registered: bool = True,
        **admin_kwargs: Any,
    ) -> list[AdminModel[Any]]:
        """Register every concrete model found under ``source`` at once.

        The batch counterpart to :meth:`register`: instead of writing
        one ``site.register(AdminModel(model=...))`` per table, point
        this at the package that holds the ORM models and every
        concrete :class:`~tempest_fastapi_sdk.BaseModel` subclass is
        wrapped in a default :class:`AdminModel` and registered.

        Example::

            site = AdminSite(title="Servus", brand="servus-backend-admin")
            site.automap("src.db.models")

        Abstract bases (``BaseUserModel`` and friends — no
        ``__tablename__``) are skipped automatically. Models you want
        configured by hand can be registered first and left out here
        with ``skip_registered=True`` (the default), or hidden entirely
        via ``exclude``.

        Args:
            source (str | ModuleType): Dotted module path (e.g.
                ``"src.db.models"``) or an already-imported
                module/package. Packages have their submodules swept
                too.
            exclude (Sequence[type[BaseModel] | str]): Models to skip —
                each entry is the model class, its class name, or its
                table name.
            skip_registered (bool): When ``True`` (default), a model
                whose slug is already registered is left untouched, so
                you can register a hand-tuned ``AdminModel`` first and
                let ``automap`` fill in the rest. When ``False``, a
                collision raises ``ValueError`` (as :meth:`register`
                does).
            **admin_kwargs (Any): Forwarded to every ``AdminModel``
                constructed here (e.g. ``page_size=50``,
                ``can_delete=False``). Applied uniformly — reach for
                per-model :meth:`register` when a model needs its own
                configuration.

        Returns:
            list[AdminModel[Any]]: The admins newly registered by this
            call, ordered by table name.

        Raises:
            ValueError: When ``skip_registered`` is ``False`` and a
                discovered model's slug is already registered.
        """
        from tempest_fastapi_sdk.admin.discovery import discover_models

        registered: list[AdminModel[Any]] = []
        for model in discover_models(source, exclude=exclude):
            admin: AdminModel[Any] = AdminModel(model=model, **admin_kwargs)
            if skip_registered and admin.get_slug() in self._registry:
                continue
            registered.append(self.register(admin))
        return registered


__all__: list[str] = [
    "AdminSite",
]
