"""Tests for the generic validated Pydantic field types."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from tempest_fastapi_sdk.utils import (
    CentsField,
    HexColorField,
    LatitudeField,
    LongitudeField,
    NonEmptyStrField,
    NonNegativeFloatField,
    NonNegativeIntField,
    PercentField,
    PortField,
    PositiveFloatField,
    PositiveIntField,
    PriceField,
    RatioField,
    SlugField,
)


class IntModel(BaseModel):
    positive: PositiveIntField = 1
    non_negative: NonNegativeIntField = 0
    cents: CentsField = 0
    port: PortField = 8000


class FloatModel(BaseModel):
    positive: PositiveFloatField = 1.0
    non_negative: NonNegativeFloatField = 0.0
    percent: PercentField = 0.0
    ratio: RatioField = 0.0
    lat: LatitudeField = 0.0
    lon: LongitudeField = 0.0


class StrModel(BaseModel):
    name: NonEmptyStrField = "x"
    slug: SlugField = "ok"
    color: HexColorField = "#fff"


class TestIntegerFields:
    def test_accepts_valid(self) -> None:
        m = IntModel(positive=3, non_negative=0, cents=1599, port=443)
        assert (m.positive, m.cents, m.port) == (3, 1599, 443)

    @pytest.mark.parametrize("value", [0, -1])
    def test_positive_int_rejects_non_positive(self, value: int) -> None:
        with pytest.raises(ValidationError):
            IntModel(positive=value)

    def test_non_negative_int_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            IntModel(non_negative=-1)

    def test_cents_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            IntModel(cents=-1)

    @pytest.mark.parametrize("value", [0, 65536])
    def test_port_range(self, value: int) -> None:
        with pytest.raises(ValidationError):
            IntModel(port=value)


class TestFloatFields:
    def test_percent_bounds(self) -> None:
        assert FloatModel(percent=100.0).percent == 100.0
        with pytest.raises(ValidationError):
            FloatModel(percent=100.1)
        with pytest.raises(ValidationError):
            FloatModel(percent=-0.1)

    def test_ratio_bounds(self) -> None:
        assert FloatModel(ratio=1.0).ratio == 1.0
        with pytest.raises(ValidationError):
            FloatModel(ratio=1.1)

    def test_latitude_bounds(self) -> None:
        assert FloatModel(lat=-90.0).lat == -90.0
        with pytest.raises(ValidationError):
            FloatModel(lat=90.1)

    def test_longitude_bounds(self) -> None:
        assert FloatModel(lon=180.0).lon == 180.0
        with pytest.raises(ValidationError):
            FloatModel(lon=-180.1)

    def test_positive_float_rejects_zero(self) -> None:
        with pytest.raises(ValidationError):
            FloatModel(positive=0.0)


class TestPriceField:
    class PriceModel(BaseModel):
        amount: PriceField

    def test_accepts_two_places(self) -> None:
        assert self.PriceModel(amount="9.99").amount == Decimal("9.99")

    def test_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            self.PriceModel(amount="-1.00")

    def test_rejects_more_than_two_places(self) -> None:
        with pytest.raises(ValidationError):
            self.PriceModel(amount="1.999")


class TestStringFields:
    def test_non_empty_strips_and_requires_content(self) -> None:
        assert StrModel(name="  hi ").name == "hi"
        with pytest.raises(ValidationError):
            StrModel(name="   ")

    @pytest.mark.parametrize("value", ["my-post-1", "abc", "a1-b2-c3"])
    def test_slug_accepts_valid(self, value: str) -> None:
        assert StrModel(slug=value).slug == value

    @pytest.mark.parametrize("value", ["Bad Slug", "UPPER", "trailing-", "-lead"])
    def test_slug_rejects_invalid(self, value: str) -> None:
        with pytest.raises(ValidationError):
            StrModel(slug=value)

    @pytest.mark.parametrize("value", ["#fff", "#FFFFFF", "#abc123"])
    def test_hex_color_accepts_valid(self, value: str) -> None:
        assert StrModel(color=value).color == value

    @pytest.mark.parametrize("value", ["fff", "#ff", "#gggggg", "#12345"])
    def test_hex_color_rejects_invalid(self, value: str) -> None:
        with pytest.raises(ValidationError):
            StrModel(color=value)
