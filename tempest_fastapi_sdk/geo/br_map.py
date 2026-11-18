"""A map of Brazil by state, drawn offline as SVG.

The outlines of the 27 federative units ship with the package
(:mod:`._br_states_geometry_data`, generated from the IBGE meshes API by
``scripts/regen_br_states_geometry.py``), so nothing here makes a request:

* :func:`br_state_outlines` — each state's rings, as coordinates.
* :func:`uf_at` — the state a coordinate falls in, or ``None`` off the map.
* :func:`project_br` — a coordinate's ``(x, y)`` in the map's SVG space.
* :func:`render_br_map_svg` — states shaded by a value per state, plus one
  bubble per point (a city), as an ``<svg>`` string ready to embed.

The outlines are the IBGE's lowest quality (about 5 000 points for the whole
country): right for a distribution map, not for a border-precise question.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from html import escape
from typing import Final

from tempest_fastapi_sdk.geo._br_states_geometry_data import BR_STATE_OUTLINES
from tempest_fastapi_sdk.geo.geometry import point_in_polygon
from tempest_fastapi_sdk.geo.schemas import Coordinate
from tempest_fastapi_sdk.utils.locations import UFEnum

BR_MAP_WIDTH: Final[float] = 1000.0
"""Width of the map's SVG ``viewBox``; the height follows the projection."""

BR_MAP_PADDING: Final[float] = 10.0
"""Blank margin, in SVG units, around the outline of the country."""

BR_MAP_LEVELS: Final[int] = 5
"""How many shades :func:`render_br_map_svg` splits the state values into."""

BR_MAP_MIN_RADIUS: Final[float] = 3.0
"""Radius, in SVG units, of the bubble of the smallest positive point."""

BR_MAP_MAX_RADIUS: Final[float] = 28.0
"""Radius, in SVG units, of the bubble of the largest point."""

_DRAW_LAST: Final[UFEnum] = UFEnum.DF
"""The state drawn after the others and tested before them.

The lowest-quality mesh does not cut the Federal District out of Goiás, so
the district's outline sits inside the outline of Goiás: drawn first, it
would be painted over, and tested last, every point in it would answer GO.
"""


@dataclass(frozen=True, slots=True)
class BRMapPoint:
    """One bubble on the map.

    Attributes:
        label (str): What the bubble's tooltip names (``"São Paulo (SP)"``).
        coordinate (Coordinate): Where it is drawn.
        value (int): What it counts; sets the bubble's area. ``0`` or less
            draws nothing.
    """

    label: str
    coordinate: Coordinate
    value: int


@dataclass(frozen=True, slots=True)
class BRMapLegendEntry:
    """One shade of the state fill, with the values it covers.

    Attributes:
        level (int): The shade, ``1`` (lightest) to ``levels`` (darkest);
            the state's CSS class is ``tempest-br-map__state--l{level}``.
        low (int): The smallest value shaded at this level.
        high (int): The largest value shaded at this level.
    """

    level: int
    low: int
    high: int


@lru_cache(maxsize=1)
def br_state_outlines() -> dict[UFEnum, tuple[tuple[Coordinate, ...], ...]]:
    """Return every state's outline, parsed once.

    Returns:
        dict[UFEnum, tuple[tuple[Coordinate, ...], ...]]: The rings of each
        state, in the order the source lists them; a ring is not closed
        (its last vertex is not a repeat of the first). A state with islands
        has more than one ring.
    """
    rings: dict[UFEnum, list[tuple[Coordinate, ...]]] = {}
    current: UFEnum | None = None
    points: list[str] = []

    def flush() -> None:
        """Store the ring being read, if any."""
        if current is None or not points:
            return
        vertices = [
            Coordinate(longitude=float(lon), latitude=float(lat))
            for lon, lat in (pair.split(",") for pair in points)
        ]
        if len(vertices) > 1 and vertices[0] == vertices[-1]:
            vertices.pop()
        rings.setdefault(current, []).append(tuple(vertices))

    for line in BR_STATE_OUTLINES.splitlines():
        if line.startswith(">"):
            flush()
            current = UFEnum(line[1:])
            points = []
        elif line:
            points.extend(line.split())
    flush()
    return {uf: tuple(state_rings) for uf, state_rings in rings.items()}


def _drawing_order() -> list[UFEnum]:
    """Return the states in drawing order, the Federal District last.

    Returns:
        list[UFEnum]: Every state with an outline.
    """
    states = [uf for uf in br_state_outlines() if uf is not _DRAW_LAST]
    return [*states, _DRAW_LAST]


def uf_at(coordinate: Coordinate) -> UFEnum | None:
    """Return the state a coordinate falls in.

    Args:
        coordinate (Coordinate): The point.

    Returns:
        UFEnum | None: The state whose outline contains the point, or
        ``None`` for a point outside Brazil (or in the sea between an island
        and the coast). The outlines are the IBGE's lowest quality, so a
        point a few kilometres from a border can land in the neighbour.
    """
    outlines = br_state_outlines()
    for uf in reversed(_drawing_order()):
        if any(point_in_polygon(coordinate, list(ring)) for ring in outlines[uf]):
            return uf
    return None


@lru_cache(maxsize=1)
def _frame() -> tuple[float, float, float, float, float]:
    """Return the projection's frame, from the outlines and the municipalities.

    The municipalities count because the lowest-quality mesh leaves out
    small islands that do have a municipality (Fernando de Noronha, east of
    the coast): framed on the outlines alone, its bubble would fall outside
    the ``viewBox``.

    Returns:
        tuple[float, float, float, float, float]: ``(west, north, scale,
        squeeze, height)``: the westmost longitude, the northmost latitude,
        SVG units per degree of latitude, the cosine that narrows a degree
        of longitude, and the ``viewBox`` height.
    """
    from tempest_fastapi_sdk.geo.municipalities import br_municipalities

    vertices = [
        vertex
        for state_rings in br_state_outlines().values()
        for ring in state_rings
        for vertex in ring
    ]
    vertices.extend(municipality.coordinate for municipality in br_municipalities())
    west = min(vertex.longitude for vertex in vertices)
    east = max(vertex.longitude for vertex in vertices)
    north = max(vertex.latitude for vertex in vertices)
    south = min(vertex.latitude for vertex in vertices)
    squeeze = math.cos(math.radians((north + south) / 2))
    scale = (BR_MAP_WIDTH - 2 * BR_MAP_PADDING) / ((east - west) * squeeze)
    height = (north - south) * scale + 2 * BR_MAP_PADDING
    return west, north, scale, squeeze, height


def br_map_height() -> float:
    """Return the height of the map's SVG ``viewBox``.

    Returns:
        float: The height, for a width of :data:`BR_MAP_WIDTH`.
    """
    return _frame()[4]


def project_br(coordinate: Coordinate) -> tuple[float, float]:
    """Return where a coordinate lands on the map.

    The projection is equirectangular, with longitude squeezed by the
    cosine of the country's middle latitude so the map is not stretched
    sideways. North is up.

    Args:
        coordinate (Coordinate): The point.

    Returns:
        tuple[float, float]: ``(x, y)`` in the ``viewBox`` of
        :func:`render_br_map_svg`, ``0`` to :data:`BR_MAP_WIDTH` wide and
        ``0`` to :func:`br_map_height` tall for a point inside Brazil or at
        any of its municipalities.
    """
    west, north, scale, squeeze, _height = _frame()
    x = BR_MAP_PADDING + (coordinate.longitude - west) * squeeze * scale
    y = BR_MAP_PADDING + (north - coordinate.latitude) * scale
    return x, y


def _ceiling(peak: int, level: int, levels: int) -> int:
    """Return the largest value shade ``level`` covers.

    The scale is logarithmic: shade ``n`` ends at ``peak ** (n / levels)``.
    Computed on integers (``value ** levels <= peak ** n``), so a boundary
    never moves by a float rounding.

    Args:
        peak (int): The largest value on the map, at least ``1``.
        level (int): The shade, ``1`` to ``levels``.
        levels (int): How many shades there are.

    Returns:
        int: The largest integer ``v`` with ``v ** levels <= peak ** level``.
    """
    bound: int = peak**level
    guess: int = max(1, round(math.pow(peak, level / levels)))
    while guess**levels > bound:
        guess -= 1
    while (guess + 1) ** levels <= bound:
        guess += 1
    return guess


def _level(value: int, peak: int, levels: int) -> int:
    """Return the shade of a value, on a logarithmic scale.

    A logarithmic scale keeps a map readable when one state dominates: on a
    linear one, a state with 184 rows next to states with 1 to 40 leaves every
    state up to 36 in the lightest shade.

    Args:
        value (int): The state's value.
        peak (int): The largest value on the map.
        levels (int): How many shades there are.

    Returns:
        int: ``0`` for a value of ``0`` or less, else the smallest shade
        ``n`` (``1`` to ``levels``) with ``value ** levels <= peak ** n``.
    """
    if value <= 0 or peak <= 0:
        return 0
    for level in range(1, levels + 1):
        if value**levels <= peak**level:
            return level
    return levels


def br_map_legend(
    state_values: Mapping[UFEnum | str, int], *, levels: int = BR_MAP_LEVELS
) -> list[BRMapLegendEntry]:
    """Return the shades a set of state values uses, with their ranges.

    The scale is logarithmic: shade ``n`` covers the values up to
    ``peak ** (n / levels)`` not covered by the shade before it. A shade no
    integer falls in (several when the peak is small) is left out.

    Args:
        state_values (Mapping[UFEnum | str, int]): The value of each state.
        levels (int): How many shades to split into.

    Returns:
        list[BRMapLegendEntry]: The shades, lightest first; empty when no
        state has a positive value.

    Raises:
        ValueError: When ``levels`` is below 1.
    """
    if levels < 1:
        raise ValueError("levels must be at least 1")
    peak = max((value for value in state_values.values()), default=0)
    if peak <= 0:
        return []
    entries: list[BRMapLegendEntry] = []
    low = 1
    for level in range(1, levels + 1):
        high = _ceiling(peak, level, levels)
        if low <= high:
            entries.append(BRMapLegendEntry(level=level, low=low, high=high))
            low = high + 1
    return entries


def _path(ring: Sequence[Coordinate]) -> str:
    """Return a ring as SVG path data.

    Args:
        ring (Sequence[Coordinate]): The ring's vertices.

    Returns:
        str: ``M x,y L x,y ... Z``, one decimal place.
    """
    commands = []
    for index, vertex in enumerate(ring):
        x, y = project_br(vertex)
        commands.append(f"{'M' if index == 0 else 'L'}{x:.1f},{y:.1f}")
    return " ".join(commands) + " Z"


@lru_cache(maxsize=1)
def _state_paths() -> dict[UFEnum, str]:
    """Return each state's SVG path data, computed once.

    Returns:
        dict[UFEnum, str]: The rings of each state, projected.
    """
    return {
        uf: " ".join(_path(ring) for ring in rings)
        for uf, rings in br_state_outlines().items()
    }


def render_br_map_svg(
    state_values: Mapping[UFEnum | str, int],
    points: Sequence[BRMapPoint] = (),
    *,
    levels: int = BR_MAP_LEVELS,
    title: str = "Brazil",
    value_label: str = "",
) -> str:
    """Draw Brazil with each state shaded by its value and a bubble per point.

    The markup carries class names only, no colours, so the page's
    stylesheet paints it: every state is ``tempest-br-map__state`` plus
    ``tempest-br-map__state--l{n}`` (``l0`` for no value, then
    ``l1``..``l{levels}``), and every bubble ``tempest-br-map__point``. Each
    shape has a ``<title>`` the browser shows on hover. Every text is
    HTML-escaped, so a label from the database cannot inject markup.

    Bubbles are drawn largest first, so a small one is never hidden under a
    big one; the area grows with the value (radius from
    :data:`BR_MAP_MIN_RADIUS` to :data:`BR_MAP_MAX_RADIUS`, by square root).

    Args:
        state_values (Mapping[UFEnum | str, int]): The value of each state,
            by enum or acronym; a missing state counts as ``0``.
        points (Sequence[BRMapPoint]): The bubbles.
        levels (int): How many shades the state values split into.
        title (str): The SVG's accessible name.
        value_label (str): Word after each number in the tooltips
            (``"users"``); empty shows the number alone.

    Returns:
        str: An ``<svg>`` element.

    Raises:
        ValueError: When ``levels`` is below 1, or a key of
            ``state_values`` is not a Brazilian state.
    """
    if levels < 1:
        raise ValueError("levels must be at least 1")
    values: dict[UFEnum, int] = {}
    for key, value in state_values.items():
        try:
            values[UFEnum(str(key).strip().upper())] = value
        except ValueError:
            raise ValueError(f"{key!r} is not a Brazilian state") from None
    peak = max(values.values(), default=0)
    suffix = f" {value_label}" if value_label else ""
    parts = [
        f'<svg class="tempest-br-map" viewBox="0 0 {BR_MAP_WIDTH:.0f} '
        f'{br_map_height():.0f}" role="img" aria-label="{escape(title)}" '
        'xmlns="http://www.w3.org/2000/svg">',
        f"<title>{escape(title)}</title>",
    ]
    for uf in _drawing_order():
        value = values.get(uf, 0)
        level = _level(value, peak, levels)
        data = _state_paths()[uf]
        parts.append(
            f'<path class="tempest-br-map__state tempest-br-map__state--l{level}" '
            f'data-uf="{uf.value}" d="{data}">'
            f"<title>{uf.value}: {value}{escape(suffix)}</title></path>"
        )
    drawn = sorted((point for point in points if point.value > 0), key=_by_value)
    top = max((point.value for point in drawn), default=0)
    for point in drawn:
        x, y = project_br(point.coordinate)
        radius = BR_MAP_MIN_RADIUS + (BR_MAP_MAX_RADIUS - BR_MAP_MIN_RADIUS) * (
            math.sqrt(point.value / top) if top else 0.0
        )
        parts.append(
            f'<circle class="tempest-br-map__point" cx="{x:.1f}" cy="{y:.1f}" '
            f'r="{radius:.1f}"><title>{escape(point.label)}: {point.value}'
            f"{escape(suffix)}</title></circle>"
        )
    parts.append("</svg>")
    return "".join(parts)


def _by_value(point: BRMapPoint) -> int:
    """Sort key putting the largest bubble first.

    Args:
        point (BRMapPoint): A bubble.

    Returns:
        int: The negated value.
    """
    return -point.value


__all__: list[str] = [
    "BR_MAP_LEVELS",
    "BR_MAP_MAX_RADIUS",
    "BR_MAP_MIN_RADIUS",
    "BR_MAP_PADDING",
    "BR_MAP_WIDTH",
    "BRMapLegendEntry",
    "BRMapPoint",
    "br_map_height",
    "br_map_legend",
    "br_state_outlines",
    "project_br",
    "render_br_map_svg",
    "uf_at",
]
