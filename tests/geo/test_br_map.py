"""The offline map of Brazil: outlines, state lookup, projection and SVG."""

from __future__ import annotations

import re

import pytest

from tempest_fastapi_sdk.geo import (
    BR_MAP_WIDTH,
    BRMapLegendEntry,
    BRMapPoint,
    Coordinate,
    br_map_height,
    br_map_legend,
    br_municipalities,
    br_state_outlines,
    project_br,
    render_br_map_svg,
    uf_at,
)
from tempest_fastapi_sdk.utils.locations import UFEnum

BRASILIA: Coordinate = Coordinate(latitude=-15.7939, longitude=-47.8828)
"""Inside the Federal District, which the mesh draws inside Goiás too."""

ATLANTIC: Coordinate = Coordinate(latitude=-20.0, longitude=-30.0)
"""Open sea, east of the coast."""


class TestOutlines:
    def test_every_state_has_an_outline(self) -> None:
        assert set(br_state_outlines()) == set(UFEnum)

    def test_rings_are_open_and_have_three_vertices(self) -> None:
        for rings in br_state_outlines().values():
            for ring in rings:
                assert len(ring) >= 3
                assert ring[0] != ring[-1]

    def test_islands_are_extra_rings(self) -> None:
        outlines = br_state_outlines()
        assert len(outlines[UFEnum.SP]) == 2
        assert len(outlines[UFEnum.MG]) == 1


class TestUfAt:
    def test_every_capital_falls_in_its_state(self) -> None:
        capitals = [m for m in br_municipalities() if m.capital]
        assert len(capitals) == 27
        assert all(uf_at(m.coordinate) is m.uf for m in capitals)

    def test_federal_district_beats_goias(self) -> None:
        assert uf_at(BRASILIA) is UFEnum.DF

    def test_sea_is_none(self) -> None:
        assert uf_at(ATLANTIC) is None

    def test_almost_every_municipality_falls_in_its_state(self) -> None:
        """The lowest-quality mesh misplaces towns on a coast or a border.

        Deterministic over the shipped data: 52 of 5571 at the time of
        writing. The bound leaves room for a regenerated mesh without
        letting it drift far.
        """
        municipalities = br_municipalities()
        misplaced = [m for m in municipalities if uf_at(m.coordinate) is not m.uf]
        assert len(misplaced) / len(municipalities) < 0.01


class TestProjection:
    def test_every_municipality_lands_inside_the_view_box(self) -> None:
        height = br_map_height()
        for municipality in br_municipalities():
            x, y = project_br(municipality.coordinate)
            assert 0 <= x <= BR_MAP_WIDTH
            assert 0 <= y <= height

    def test_north_is_up_and_east_is_right(self) -> None:
        boa_vista = project_br(Coordinate(latitude=2.82, longitude=-60.67))
        porto_alegre = project_br(Coordinate(latitude=-30.03, longitude=-51.23))
        recife = project_br(Coordinate(latitude=-8.05, longitude=-34.88))
        assert boa_vista[1] < porto_alegre[1]
        assert recife[0] > boa_vista[0]


class TestLegend:
    def test_ranges_are_logarithmic(self) -> None:
        assert br_map_legend({"SP": 100, "RJ": 1}) == [
            BRMapLegendEntry(level=1, low=1, high=2),
            BRMapLegendEntry(level=2, low=3, high=6),
            BRMapLegendEntry(level=3, low=7, high=15),
            BRMapLegendEntry(level=4, low=16, high=39),
            BRMapLegendEntry(level=5, low=40, high=100),
        ]

    def test_shades_no_integer_falls_in_are_left_out(self) -> None:
        assert br_map_legend({"SP": 2}) == [
            BRMapLegendEntry(level=1, low=1, high=1),
            BRMapLegendEntry(level=5, low=2, high=2),
        ]

    @pytest.mark.parametrize("peak", [1, 2, 7, 100, 184, 1_000_000])
    def test_legend_agrees_with_the_state_shades(self, peak: int) -> None:
        legend = br_map_legend({"SP": peak})
        assert legend[0].low == 1
        assert legend[-1].high == peak
        values = {
            value: f'--l{entry.level}"'
            for entry in legend
            for value in {entry.low, entry.high, (entry.low + entry.high) // 2}
        }
        for value, shade in values.items():
            svg = render_br_map_svg({"SP": peak, "RJ": value})
            assert re.search(rf'{shade} data-uf="RJ"', svg), (value, shade)

    def test_nothing_counted_is_empty(self) -> None:
        assert br_map_legend({}) == []


class TestRender:
    def _state_class(self, svg: str, uf: str) -> str:
        match = re.search(rf'class="([^"]+)" data-uf="{uf}"', svg)
        assert match is not None
        return match.group(1)

    def test_states_are_shaded_by_value(self) -> None:
        svg = render_br_map_svg({"SP": 100, UFEnum.RJ: 10, "am": 1})
        assert svg.startswith('<svg class="tempest-br-map"')
        assert self._state_class(svg, "SP").endswith("--l5")
        assert self._state_class(svg, "RJ").endswith("--l3")
        assert self._state_class(svg, "AM").endswith("--l1")
        assert self._state_class(svg, "BA").endswith("--l0")
        assert svg.count("<path ") == 27

    def test_federal_district_is_drawn_last(self) -> None:
        svg = render_br_map_svg({})
        assert re.findall(r'data-uf="(\w\w)"', svg)[-1] == "DF"

    def test_bubbles_largest_first_with_tooltip(self) -> None:
        sao_paulo = Coordinate(latitude=-23.55, longitude=-46.63)
        campinas = Coordinate(latitude=-22.90, longitude=-47.06)
        svg = render_br_map_svg(
            {"SP": 10},
            [
                BRMapPoint("Campinas (SP)", campinas, 2),
                BRMapPoint("São Paulo (SP)", sao_paulo, 8),
                BRMapPoint("Nowhere", campinas, 0),
            ],
            value_label="users",
        )
        titles = re.findall(r"<circle [^>]+><title>([^<]+)</title>", svg)
        assert titles == ["São Paulo (SP): 8 users", "Campinas (SP): 2 users"]

    def test_labels_are_escaped(self) -> None:
        svg = render_br_map_svg(
            {"SP": 1},
            [BRMapPoint("<script>alert(1)</script>", BR_POINT, 1)],
            title='"><img>',
        )
        assert "<script>" not in svg
        assert "&lt;script&gt;" in svg
        assert '"><img>' not in svg

    def test_unknown_state_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a Brazilian state"):
            render_br_map_svg({"XX": 1})

    def test_levels_below_one_are_refused(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            render_br_map_svg({}, levels=0)


BR_POINT: Coordinate = Coordinate(latitude=-23.55, longitude=-46.63)
"""Any point inside Brazil."""
