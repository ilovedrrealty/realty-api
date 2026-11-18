"""Bounded public spatial criteria, using Web Mercator polygons and spherical radii."""

from dataclasses import dataclass
from decimal import Decimal
from math import asinh, fsum, radians, tan
import re
from sys import float_info


MAX_SHAPE_LENGTH = 600
EARTH_RADIUS_KM = 6371.0088
MERCATOR_RADIUS_M = 6378137
MAX_MAP_LATITUDE = 85
NUMBER = re.compile(r"-?[0-9]+(?:\.[0-9]{1,6})?")
ROUNDING_TOLERANCE = 8 * float_info.epsilon
INVALID_SHAPE = "Provide one valid radius or simple drawn area"

Point = tuple[float, float]


@dataclass(frozen=True)
class Radius:
    latitude: float
    longitude: float
    km: float


@dataclass(frozen=True)
class Polygon:
    projected: tuple[Point, ...]
    south: Decimal
    north: Decimal
    west: Decimal
    east: Decimal


def _cross_products(origin: Point, start: Point, end: Point) -> Point:
    x, y = origin
    return ((start[0] - x) * (end[1] - y), (start[1] - y) * (end[0] - x))


def _turn(start: Point, end: Point, point: Point) -> int:
    left, right = _cross_products(start, end, point)
    cross = left - right
    if abs(cross) <= ROUNDING_TOLERANCE * (abs(left) + abs(right)):
        return 0
    return 1 if cross > 0 else -1


def _on_bounds(start: Point, end: Point, point: Point) -> bool:
    return all(min(a, b) <= c <= max(a, b) for a, b, c in zip(start, end, point))


def _intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    abc, abd = _turn(a, b, c), _turn(a, b, d)
    cda, cdb = _turn(c, d, a), _turn(c, d, b)
    return (abc * abd < 0 and cda * cdb < 0
            or abc == 0 and _on_bounds(a, b, c)
            or abd == 0 and _on_bounds(a, b, d)
            or cda == 0 and _on_bounds(c, d, a)
            or cdb == 0 and _on_bounds(c, d, b))


def _simple_path(points: tuple[Point, ...]) -> bool:
    anchor = points[0]
    products = [_cross_products(anchor, start, end) for start, end in zip(points[1:], points[2:])]
    twice_area = fsum(left - right for left, right in products)
    if abs(twice_area) <= ROUNDING_TOLERANCE * fsum(abs(left) + abs(right) for left, right in products):
        return False
    count = len(points)
    for index, point in enumerate(points):
        previous, following = points[index - 1], points[(index + 1) % count]
        if _turn(previous, point, following) == 0 and not _on_bounds(previous, following, point):
            return False
        # Adjacent edges may meet at their shared endpoint. All other edges
        # must be disjoint, including endpoints lying on a nonadjacent edge.
        for next_index in range(index + 2, count if index else count - 1):
            if _intersect(point, following, points[next_index], points[(next_index + 1) % count]):
                return False
    return True


def parse_shape(value: str) -> Radius | Polygon:
    if len(value) > MAX_SHAPE_LENGTH:
        raise ValueError(INVALID_SHAPE)
    kind, *tokens = value.split(",")
    if not (kind == "radius" and len(tokens) == 3
            or kind == "polygon" and 6 <= len(tokens) <= 48 and len(tokens) % 2 == 0):
        raise ValueError(INVALID_SHAPE)
    if not all(NUMBER.fullmatch(token) for token in tokens):
        raise ValueError(INVALID_SHAPE)
    values = [Decimal(token) for token in tokens]
    pairs = tuple(zip(values[::2], values[1::2]))
    if any(abs(lat) > MAX_MAP_LATITUDE or abs(lng) > 180 for lat, lng in pairs):
        raise ValueError(INVALID_SHAPE)
    if kind == "radius":
        if not Decimal("0.1") <= values[2] <= 500:
            raise ValueError(INVALID_SHAPE)
        return Radius(*(float(number) for number in values))
    latitudes, longitudes = zip(*pairs)
    south, north, west, east = min(latitudes), max(latitudes), min(longitudes), max(longitudes)
    if len(set(pairs)) != len(pairs) or east - west >= 180:
        raise ValueError(INVALID_SHAPE)
    projected = tuple((radians(float(lng)), asinh(tan(radians(float(lat))))) for lat, lng in pairs)
    if not _simple_path(projected):
        raise ValueError(INVALID_SHAPE)
    return Polygon(projected, south, north, west, east)
