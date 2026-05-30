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
        self._registry: dict[str, AdminModel[Any]] = {}

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


__all__: list[str] = [
    "AdminSite",
]
