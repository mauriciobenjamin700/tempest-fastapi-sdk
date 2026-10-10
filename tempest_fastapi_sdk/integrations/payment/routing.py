"""Run two Pix providers side by side while moving from one to the other.

Switching a payment provider in one deploy is a bet on everything the
sandbox did not show. The safer move is a rollout: new charges go to the
new provider for a slice of orders, every charge already open keeps being
read and cancelled where it was created, and the slice grows — or drops to
zero — without a deploy.

Two things make that harder than picking a provider per request:

* **A charge belongs to the provider that created it.** Reading it from the
  other one is a 404 at best. So reads and cancellations are routed by the
  provider name the service stored next to the charge id
  (:attr:`PixCharge.provider`), never by the rollout percentage, which may
  have changed since the charge was created.
* **The same order must land on the same provider every time.** A service
  that retries opening a charge for one order — after a timeout, say —
  should not open it once at each provider. The slice is therefore a
  deterministic function of :attr:`PixChargeRequest.reference`, not a coin
  flip.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from tempest_fastapi_sdk.integrations.payment.base import (
    PixCharge,
    PixChargeRequest,
    PixProvider,
)

BUCKETS: int = 100
"""How many slices :meth:`PixProviderRouter.bucket` divides references into."""


class PixProviderRouter:
    """Send a percentage of new charges to a candidate provider.

    Not itself a :class:`PixProvider`: the contract's ``get_pix_charge``
    takes only a charge id, and an id alone does not say which provider to
    ask. The router takes the stored provider name alongside it instead.

    Attributes:
        primary (str): The provider that takes every charge outside the
            candidate's slice.
        candidate (str | None): The provider being rolled out, or ``None``.
        candidate_percent (int): Share of new charges, 0 to 100, that go to
            :attr:`candidate`.
    """

    def __init__(
        self,
        providers: Mapping[str, PixProvider] | list[PixProvider],
        *,
        primary: str,
        candidate: str | None = None,
        candidate_percent: int = 0,
    ) -> None:
        """Register the providers and the rollout.

        Args:
            providers (Mapping[str, PixProvider] | list[PixProvider]): Every
                provider a stored charge may belong to — including one that
                no longer takes new charges, so its open charges can still
                be read and cancelled. A list is keyed by each provider's
                ``provider_name``; a mapping must use the same keys.
            primary (str): Name of the provider that takes new charges
                outside the candidate's slice.
            candidate (str | None): Name of the provider being rolled out.
            candidate_percent (int): Share of new charges, 0 to 100, sent
                to ``candidate``. ``0`` is the kill switch: nothing new goes
                there, and what is already there is still served.

        Raises:
            ValueError: If a mapping key differs from its provider's
                ``provider_name``, ``primary`` or ``candidate`` is not
                registered, or ``candidate_percent`` is outside 0..100 or
                non-zero without a candidate.
        """
        registry: dict[str, PixProvider] = {}
        if isinstance(providers, Mapping):
            for name, provider in providers.items():
                if name != provider.provider_name:
                    raise ValueError(
                        f"provider registered as {name!r} reports provider_name "
                        f"{provider.provider_name!r}; stored charges carry the "
                        "latter, so the two must match"
                    )
                registry[name] = provider
        else:
            registry = {provider.provider_name: provider for provider in providers}
        if primary not in registry:
            raise ValueError(f"primary provider {primary!r} is not registered")
        if candidate is not None and candidate not in registry:
            raise ValueError(f"candidate provider {candidate!r} is not registered")
        if not 0 <= candidate_percent <= BUCKETS:
            raise ValueError(
                f"candidate_percent must be between 0 and 100: {candidate_percent!r}"
            )
        if candidate is None and candidate_percent:
            raise ValueError("candidate_percent is set but no candidate is named")
        self._providers: dict[str, PixProvider] = registry
        self.primary: str = primary
        self.candidate: str | None = candidate
        self.candidate_percent: int = candidate_percent

    @staticmethod
    def bucket(reference: str) -> int:
        """Place a reference in one of :data:`BUCKETS` stable slices.

        Args:
            reference (str): The service's identifier for the charge.

        Returns:
            int: A slice in ``0..99``, the same for the same reference on
            every process and every run. SHA-256 rather than ``hash()``,
            which Python salts per process.
        """
        digest = hashlib.sha256(reference.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % BUCKETS

    def select(self, request: PixChargeRequest) -> PixProvider:
        """Pick the provider that should create this charge.

        Args:
            request (PixChargeRequest): The charge about to be created.

        Returns:
            PixProvider: The candidate when the reference falls in its
            slice, the primary otherwise. Raising the percentage only adds
            references to the candidate's slice; a reference already there
            stays there.
        """
        if (
            self.candidate is not None
            and self.bucket(request.reference) < self.candidate_percent
        ):
            return self._providers[self.candidate]
        return self._providers[self.primary]

    def provider_for(self, provider_name: str) -> PixProvider:
        """Return the provider a stored charge belongs to.

        Args:
            provider_name (str): The :attr:`PixCharge.provider` the service
                stored when the charge was created.

        Returns:
            PixProvider: That provider.

        Raises:
            KeyError: If no provider with that name is registered. A stored
                charge from a provider the router no longer knows cannot be
                read or cancelled, and saying so beats asking another
                provider about an id it never issued.
        """
        try:
            return self._providers[provider_name]
        except KeyError:
            raise KeyError(
                f"no provider registered as {provider_name!r}; keep a provider "
                "registered while it still has open charges"
            ) from None

    async def create_pix_charge(self, request: PixChargeRequest) -> PixCharge:
        """Create the charge at the provider :meth:`select` picks.

        Args:
            request (PixChargeRequest): What to charge.

        Returns:
            PixCharge: The created charge. Store its ``provider`` next to
            its ``provider_charge_id``: reads and cancellations are routed
            by it.
        """
        return await self.select(request).create_pix_charge(request)

    async def get_pix_charge(self, provider_name: str, charge_id: str) -> PixCharge:
        """Read a stored charge from the provider that created it.

        Args:
            provider_name (str): The stored :attr:`PixCharge.provider`.
            charge_id (str): The stored :attr:`PixCharge.provider_charge_id`.

        Returns:
            PixCharge: The charge as that provider reports it.
        """
        return await self.provider_for(provider_name).get_pix_charge(charge_id)

    async def cancel_pix_charge(self, provider_name: str, charge_id: str) -> PixCharge:
        """Cancel a stored charge at the provider that created it.

        Args:
            provider_name (str): The stored :attr:`PixCharge.provider`.
            charge_id (str): The stored :attr:`PixCharge.provider_charge_id`.

        Returns:
            PixCharge: The charge after cancellation.
        """
        return await self.provider_for(provider_name).cancel_pix_charge(charge_id)


__all__: list[str] = ["BUCKETS", "PixProviderRouter"]
