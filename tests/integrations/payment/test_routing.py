"""``PixProviderRouter``: a rollout that keeps every charge where it was born.

Two fakes stand in for the providers. What is asserted is routing — which
provider got the call — and the two properties the module docstring names:
the same reference always lands on the same provider, and a stored charge
is read from the provider that created it whatever the percentage says now.
"""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.integrations.payment import (
    PixChargeRequest,
    PixProviderRouter,
)
from tempest_fastapi_sdk.testing.fakes import FakePixProvider

REFERENCES: list[str] = [f"order-{n}" for n in range(2000)]


def _fakes() -> tuple[FakePixProvider, FakePixProvider]:
    """Build two fakes with distinct provider names.

    Returns:
        tuple[FakePixProvider, FakePixProvider]: The primary and the
        candidate.
    """
    return (
        FakePixProvider(provider_name="openpix"),
        FakePixProvider(provider_name="mercado_pago"),
    )


def _request(reference: str) -> PixChargeRequest:
    """Build a request.

    Args:
        reference (str): The order reference.

    Returns:
        PixChargeRequest: A 1-real charge.
    """
    return PixChargeRequest(amount_cents=100, reference=reference)


class TestSelection:
    """The slice is deterministic and grows monotonically."""

    def test_zero_percent_sends_nothing_to_the_candidate(self) -> None:
        """The kill switch."""
        primary, candidate = _fakes()
        router = PixProviderRouter(
            [primary, candidate], primary="openpix", candidate="mercado_pago"
        )

        assert all(router.select(_request(r)) is primary for r in REFERENCES)

    def test_hundred_percent_sends_everything(self) -> None:
        """The cut-over."""
        primary, candidate = _fakes()
        router = PixProviderRouter(
            [primary, candidate],
            primary="openpix",
            candidate="mercado_pago",
            candidate_percent=100,
        )

        assert all(router.select(_request(r)) is candidate for r in REFERENCES)

    def test_the_same_reference_always_lands_in_the_same_place(self) -> None:
        """A retried charge for one order is not opened at both providers."""
        assert [PixProviderRouter.bucket(r) for r in REFERENCES] == [
            PixProviderRouter.bucket(r) for r in REFERENCES
        ]
        assert PixProviderRouter.bucket("order-1042") == PixProviderRouter.bucket(
            "order-1042"
        )

    def test_raising_the_percentage_only_adds_references(self) -> None:
        """Nobody moves from the candidate back to the primary on a raise."""
        primary, candidate = _fakes()

        def chosen(percent: int) -> set[str]:
            router = PixProviderRouter(
                [primary, candidate],
                primary="openpix",
                candidate="mercado_pago",
                candidate_percent=percent,
            )
            return {r for r in REFERENCES if router.select(_request(r)) is candidate}

        assert chosen(10) <= chosen(25) <= chosen(50)

    def test_the_share_is_close_to_the_percentage(self) -> None:
        """Over 2000 references, 10% lands between 8% and 12%."""
        primary, candidate = _fakes()
        router = PixProviderRouter(
            [primary, candidate],
            primary="openpix",
            candidate="mercado_pago",
            candidate_percent=10,
        )

        share = sum(router.select(_request(r)) is candidate for r in REFERENCES)

        assert 160 <= share <= 240


class TestStoredCharges:
    """Reads and cancellations follow the stored provider name."""

    async def test_a_charge_is_read_where_it_was_created(self) -> None:
        """Even after the rollout went back to zero."""
        primary, candidate = _fakes()
        rollout = PixProviderRouter(
            [primary, candidate],
            primary="openpix",
            candidate="mercado_pago",
            candidate_percent=100,
        )
        charge = await rollout.create_pix_charge(_request("order-1"))

        rolled_back = PixProviderRouter(
            [primary, candidate], primary="openpix", candidate="mercado_pago"
        )
        read = await rolled_back.get_pix_charge(
            charge.provider, charge.provider_charge_id
        )
        cancelled = await rolled_back.cancel_pix_charge(
            charge.provider, charge.provider_charge_id
        )

        assert charge.provider == "mercado_pago"
        assert read.provider_charge_id == charge.provider_charge_id
        assert cancelled.provider == "mercado_pago"

    def test_an_unregistered_provider_is_a_clear_error(self) -> None:
        """Never ask another provider about an id it did not issue."""
        primary, _ = _fakes()
        router = PixProviderRouter([primary], primary="openpix")

        with pytest.raises(KeyError, match="keep a provider registered"):
            router.provider_for("mercado_pago")


class TestConfiguration:
    """Misconfiguration fails at construction, not at the first charge."""

    def test_a_mapping_key_must_match_the_provider_name(self) -> None:
        """Stored charges carry `provider_name`; the key must agree."""
        primary, _ = _fakes()

        with pytest.raises(ValueError, match="provider_name"):
            PixProviderRouter({"woovi": primary}, primary="woovi")

    @pytest.mark.parametrize(
        ("primary", "candidate", "percent"),
        [
            ("stripe", None, 0),
            ("openpix", "stripe", 10),
            ("openpix", "mercado_pago", 101),
            ("openpix", "mercado_pago", -1),
            ("openpix", None, 10),
        ],
    )
    def test_bad_rollouts_are_refused(
        self, primary: str, candidate: str | None, percent: int
    ) -> None:
        """Unknown names, out-of-range or orphan percentages."""
        providers = list(_fakes())

        with pytest.raises(ValueError):
            PixProviderRouter(
                providers,
                primary=primary,
                candidate=candidate,
                candidate_percent=percent,
            )
