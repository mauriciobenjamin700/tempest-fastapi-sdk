"""Admin site registry — analog of ``django.contrib.admin.AdminSite``."""

from __future__ import annotations

from typing import Any

from tempest_fastapi_sdk.admin.config import AdminModel


class AdminSite:
    """Holds the set of :class:`AdminModel` configurations to expose.

    Each project instantiates one site, registers its admin
    configurations, and passes the site to :func:`make_admin_router`.
    Sites are explicit (no auto-discovery) so the surface remains
    predictable across deployments.

    Attributes:
        title (str): Branding shown at the top of every admin page.
        index_subtitle (str): Optional subtitle for the dashboard.
        site_url (str | None): Optional "View site" link rendered in
            the admin header.
    """

    def __init__(
        self,
        title: str = "Admin",
        *,
        index_subtitle: str = "Site administration",
        site_url: str | None = None,
    ) -> None:
        """Initialize the site.

        Args:
            title (str): Branding text.
            index_subtitle (str): Dashboard subtitle.
            site_url (str | None): Optional outbound link rendered
                in the admin header.
        """
        self.title: str = title
        self.index_subtitle: str = index_subtitle
        self.site_url: str | None = site_url
        self._registry: dict[str, type[AdminModel[Any]]] = {}

    def register(self, admin_cls: type[AdminModel[Any]]) -> type[AdminModel[Any]]:
        """Register ``admin_cls`` against its model slug.

        Doubles as a class decorator::

            @site.register
            class UserAdmin(AdminModel[UserModel]):
                model = UserModel

        Args:
            admin_cls (type[AdminModel[Any]]): The admin configuration.

        Returns:
            type[AdminModel[Any]]: The same class (so the decorator
            form is non-destructive).

        Raises:
            ValueError: When another admin is already registered under
                the same slug.
        """
        slug = admin_cls.get_slug()
        if slug in self._registry:
            existing = self._registry[slug]
            raise ValueError(
                f"AdminModel for slug {slug!r} already registered "
                f"({existing.__name__}); refusing to overwrite with "
                f"{admin_cls.__name__}"
            )
        self._registry[slug] = admin_cls
        return admin_cls

    def unregister(self, slug: str) -> None:
        """Remove a previously registered admin.

        Args:
            slug (str): The slug to drop.

        Raises:
            KeyError: When no admin is registered under ``slug``.
        """
        del self._registry[slug]

    def get(self, slug: str) -> type[AdminModel[Any]] | None:
        """Return the admin registered under ``slug``, or ``None``.

        Args:
            slug (str): The admin slug.

        Returns:
            type[AdminModel[Any]] | None: The configuration class.
        """
        return self._registry.get(slug)

    def require(self, slug: str) -> type[AdminModel[Any]]:
        """Return the admin registered under ``slug`` or raise.

        Args:
            slug (str): The admin slug.

        Returns:
            type[AdminModel[Any]]: The configuration class.

        Raises:
            KeyError: When no admin matches the slug.
        """
        admin = self.get(slug)
        if admin is None:
            raise KeyError(f"No admin registered for slug {slug!r}")
        return admin

    @property
    def registry(self) -> dict[str, type[AdminModel[Any]]]:
        """Return a copy of the slug→admin mapping.

        Returns:
            dict[str, type[AdminModel[Any]]]: The current registry.
        """
        return dict(self._registry)

    def iter_models(self) -> list[type[AdminModel[Any]]]:
        """Return registered admins ordered by display name.

        Returns:
            list[type[AdminModel[Any]]]: Ordered admin classes.
        """
        return sorted(
            self._registry.values(),
            key=lambda cls: cls.get_verbose_name_plural().lower(),
        )


__all__: list[str] = [
    "AdminSite",
]
