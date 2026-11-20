"""Tests for the OpenStreetMap loaders."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk.geo import (
    load_road_graph,
    road_graph_from_geojson,
    road_graph_from_osm_json,
    road_graph_from_overpass,
)


def _overpass() -> dict[str, Any]:
    """An Overpass answer: four ways sharing nodes, one footway.

    Returns:
        dict[str, Any]: The parsed ``[out:json]`` document.
    """
    nodes = [
        {"type": "node", "id": i, "lat": -5.09, "lon": -42.80 + i * 0.001}
        for i in range(1, 6)
    ]
    ways = [
        {
            "type": "way",
            "id": 10,
            "nodes": [1, 2, 3],
            "tags": {"highway": "residential", "name": "Rua A", "maxspeed": "40"},
        },
        {
            "type": "way",
            "id": 11,
            "nodes": [3, 4],
            "tags": {"highway": "primary", "oneway": "yes"},
        },
        {
            "type": "way",
            "id": 12,
            "nodes": [4, 5],
            "tags": {"highway": "primary", "oneway": "-1"},
        },
        {"type": "way", "id": 13, "nodes": [1, 5], "tags": {"highway": "footway"}},
        {"type": "way", "id": 14, "nodes": [2, 99, 4], "tags": {"highway": "service"}},
    ]
    return {"elements": [*nodes, *ways]}


class TestOverpass:
    """``road_graph_from_overpass``."""

    def test_ways_become_segments_with_osm_one_way_rules(self) -> None:
        """Two-way residential, one-way forward, one-way reversed, footway out."""
        graph = road_graph_from_overpass(_overpass())
        by_id = {e.id: e for e in graph.edges}

        assert set(by_id) == {"way/10:0", "way/10:1", "way/11:0", "way/12:0"}
        assert by_id["way/10:0"].two_way is True
        assert by_id["way/10:0"].data == {
            "highway": "residential",
            "name": "Rua A",
            "maxspeed": "40",
        }
        assert by_id["way/11:0"].two_way is False
        assert by_id["way/12:0"].two_way is False
        five = graph.nodes[by_id["way/12:0"].start]
        assert five.longitude == pytest.approx(-42.795)

    def test_missing_node_drops_only_its_edges(self) -> None:
        """Way 14 names node 99, absent: its edges go, nothing raises."""
        graph = road_graph_from_overpass(_overpass(), highways=None)

        assert not any(e.id and e.id.startswith("way/14") for e in graph.edges)
        assert any(e.id == "way/13:0" for e in graph.edges)


class TestGeoJSON:
    """``road_graph_from_geojson``."""

    def test_lines_join_on_shared_vertices(self) -> None:
        """Two lines ending on the same coordinate share a node."""
        data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "way/7",
                    "properties": {"highway": "residential"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[-42.8, -5.09], [-42.799, -5.09]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"highway": "residential", "junction": "roundabout"},
                    "geometry": {
                        "type": "MultiLineString",
                        "coordinates": [[[-42.799, -5.09], [-42.798, -5.09]]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {"type": "Point", "coordinates": [0, 0]},
                },
            ],
        }

        graph = road_graph_from_geojson(data)

        assert len(graph.nodes) == 3
        assert [e.id for e in graph.edges] == ["way/7:0", "way/1:0"]
        assert graph.edges[1].two_way is False

    def test_shape_is_detected(self) -> None:
        """``road_graph_from_osm_json`` picks the reader; unknown raises."""
        assert len(road_graph_from_osm_json(_overpass()).edges) == 4
        with pytest.raises(ValueError, match="Overpass"):
            road_graph_from_osm_json({"type": "Feature"})


class TestLoad:
    """``load_road_graph`` caches by path, size and mtime."""

    def test_same_file_is_built_once_and_rebuilt_on_change(
        self, tmp_path: Path
    ) -> None:
        """Second load is the same object; a rewrite rebuilds."""
        path = tmp_path / "city.json"
        path.write_text(json.dumps(_overpass()))

        first = load_road_graph(path)

        assert load_road_graph(path) is first
        data = _overpass()
        data["elements"] = [e for e in data["elements"] if e.get("id") != 12]
        path.write_text(json.dumps(data))
        os.utime(path, ns=(1, 1))
        rebuilt = load_road_graph(path)
        assert rebuilt is not first
        assert len(rebuilt.edges) == 3
