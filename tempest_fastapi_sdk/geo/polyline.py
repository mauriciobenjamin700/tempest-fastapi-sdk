"""Encoded polyline codec — the Google/OSRM algorithm, pure Python.

Routing backends return a route's geometry as an *encoded polyline*: a
compact ASCII string of delta-encoded coordinates. These helpers convert
between that string and a list of
:class:`~tempest_fastapi_sdk.geo.Coordinate`, with no dependency and no
network. ``precision=5`` matches Google and OSRM's default; OSRM's
``geometries=polyline6`` uses ``precision=6``.
"""

from __future__ import annotations

from tempest_fastapi_sdk.geo.schemas import Coordinate


def _encode_signed(value: int) -> str:
    """Encode one signed integer to the polyline chunk format."""
    value <<= 1
    if value < 0:
        value = ~value
    chunks: list[str] = []
    while value >= 0x20:
        chunks.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    chunks.append(chr(value + 63))
    return "".join(chunks)


def encode_polyline(points: list[Coordinate], *, precision: int = 5) -> str:
    """Encode coordinates into an encoded-polyline string.

    Args:
        points: The ordered coordinates to encode.
        precision: Decimal digits of precision (5 = Google/OSRM default,
            6 = ``polyline6``).

    Returns:
        The encoded polyline string (``""`` for no points).
    """
    factor = 10**precision
    result: list[str] = []
    prev_lat = 0
    prev_lon = 0
    for point in points:
        lat = round(point.latitude * factor)
        lon = round(point.longitude * factor)
        result.append(_encode_signed(lat - prev_lat))
        result.append(_encode_signed(lon - prev_lon))
        prev_lat = lat
        prev_lon = lon
    return "".join(result)


def decode_polyline(encoded: str, *, precision: int = 5) -> list[Coordinate]:
    """Decode an encoded-polyline string into coordinates.

    Args:
        encoded: The encoded polyline string.
        precision: Decimal digits of precision used when encoding (must
            match the encoder — 5 or 6).

    Returns:
        The decoded coordinates, in order (``[]`` for an empty string).
    """
    factor = 10**precision
    coordinates: list[Coordinate] = []
    index = 0
    lat = 0
    lon = 0
    length = len(encoded)

    while index < length:
        for is_longitude in (False, True):
            shift = 0
            result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if is_longitude:
                lon += delta
            else:
                lat += delta
        coordinates.append(
            Coordinate(latitude=lat / factor, longitude=lon / factor),
        )
    return coordinates


__all__: list[str] = [
    "decode_polyline",
    "encode_polyline",
]
