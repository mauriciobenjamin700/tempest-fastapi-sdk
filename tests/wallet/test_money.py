"""Fee and split arithmetic of the wallet module."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.wallet import (
    OPENPIX_FEE_TIERS,
    OpenPixFeeTiers,
    openpix_fee_cents,
    split_net,
)


class TestOpenPixFeeTiers:
    def test_the_ported_schedule_is_pinned(self) -> None:
        """Ported from alofans-api; drift upstream must show up as a failure."""
        assert (
            OpenPixFeeTiers(
                low_tier_max_cents=6_250,
                low_tier_cents=50,
                mid_tier_max_cents=62_500,
                mid_tier_per_mille=8,
                high_tier_cents=500,
                fixed_cents=100,
            )
            == OPENPIX_FEE_TIERS
        )

    @pytest.mark.parametrize(
        ("total", "fee"),
        [
            (1, 150),
            (6_250, 150),
            (6_251, 150),
            (10_000, 180),
            (62_500, 600),
            (62_501, 600),
            (1_000_000, 600),
        ],
    )
    def test_each_tier_boundary(self, total: int, fee: int) -> None:
        assert openpix_fee_cents(total) == fee

    def test_the_middle_tier_rounds_down(self) -> None:
        assert openpix_fee_cents(12_345) == 12_345 * 8 // 1000 + 100

    def test_a_custom_schedule_is_honoured(self) -> None:
        tiers = OpenPixFeeTiers(
            low_tier_max_cents=100,
            low_tier_cents=1,
            mid_tier_max_cents=1_000,
            mid_tier_per_mille=10,
            high_tier_cents=20,
            fixed_cents=0,
        )
        assert openpix_fee_cents(50, tiers=tiers) == 1
        assert openpix_fee_cents(500, tiers=tiers) == 5
        assert openpix_fee_cents(5_000, tiers=tiers) == 20

    def test_a_negative_total_is_refused(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            openpix_fee_cents(-1)


class TestSplitNet:
    def test_the_split_adds_back_up_to_the_total(self) -> None:
        """Exhaustive over every total from R$ 0,01 to R$ 2.000,00."""
        for total in range(1, 200_001):
            split = split_net(
                total,
                platform_bps=1_500,
                gateway_fee_cents=openpix_fee_cents(total),
                residual_recipient="producer",
                shares_bps={"a": 2_000, "b": 1_000},
            )
            if split.net_cents == 0:
                assert all(value == 0 for value in split.shares.values())
                continue
            assert (
                split.platform_cents
                + split.gateway_fee_cents
                + sum(split.shares.values())
                == total
            )

    def test_the_residual_recipient_takes_the_rounding(self) -> None:
        split = split_net(
            1_001,
            platform_bps=0,
            gateway_fee_cents=0,
            residual_recipient="producer",
            shares_bps={"a": 3_333},
        )
        assert split.shares == {"a": 333, "producer": 668}

    def test_a_single_payee_takes_the_whole_net(self) -> None:
        split = split_net(
            10_000,
            platform_bps=500,
            gateway_fee_cents=180,
            residual_recipient="driver",
        )
        assert split.platform_cents == 500
        assert split.net_cents == 9_320
        assert split.shares == {"driver": 9_320}

    def test_fees_larger_than_the_total_floor_the_net_at_zero(self) -> None:
        split = split_net(
            100,
            platform_bps=1_500,
            gateway_fee_cents=150,
            residual_recipient="producer",
            shares_bps={"a": 5_000},
        )
        assert split.net_cents == 0
        assert split.shares == {"a": 0, "producer": 0}

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"platform_bps": -1}, "basis points"),
            ({"platform_bps": 10_001}, "basis points"),
            ({"shares_bps": {"a": 6_000, "b": 5_000}}, "more than 10000"),
            ({"shares_bps": {"producer": 100}}, "must not appear"),
            ({"gateway_fee_cents": -1}, "negative"),
        ],
    )
    def test_invalid_input_is_refused(
        self, kwargs: dict[str, object], message: str
    ) -> None:
        arguments: dict[str, object] = {
            "platform_bps": 1_500,
            "gateway_fee_cents": 0,
            "residual_recipient": "producer",
            **kwargs,
        }
        with pytest.raises(ValueError, match=message):
            split_net(1_000, **arguments)  # type: ignore[arg-type]
