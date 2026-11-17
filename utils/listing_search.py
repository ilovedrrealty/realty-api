"""Read-only search expressions; native listing prices are never changed."""

from decimal import Decimal
from math import cos, degrees, radians, sin
import unicodedata
from sqlalchemy import Double, and_, case, cast, func, literal
from sqlalchemy.types import UserDefinedType

from models.listing import Listing
from models.listing_location_review import ListingLocationReview
from utils.location_quality import public_coordinate_expression
from utils.search_geometry import EARTH_RADIUS_KM, MAX_MAP_LATITUDE, MERCATOR_RADIUS_M, Polygon, Radius


# PostgreSQL's built-in translate needs no extension or database migration.
# Include uppercase letters too for databases using the C locale.
ACCENTED = "áéíóúüñàèìòùâêîôûäëïöÿçÁÉÍÓÚÜÑÀÈÌÒÙÂÊÎÔÛÄËÏÖŸÇ"
UNACCENTED = "aeiouunaeiouaeiouaeioycaeiouunaeiouaeiouaeioyc"
LOCATION_TRANSLATION = str.maketrans(ACCENTED, UNACCENTED)


def normalized_location(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split()).lower().translate(LOCATION_TRANSLATION)


def normalized_location_column():
    compact = func.btrim(func.regexp_replace(Listing.location, r"\s+", " ", "g"))
    return func.translate(func.lower(func.normalize(compact)), ACCENTED, UNACCENTED)


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_price_expression(price_currency: str, usd_to_dop: float, rental_period: str, purpose: str | None):
    """One comparable amount for filtering, sorting, aggregates and the response.

    Rentals use only a declared rate for the chosen period, never generic price.
    A 'both' listing uses its rental rate when searching rent and its sale price
    otherwise. The supplied exchange rate is an estimate, not a stored update.
    """
    rental_rate = Listing.price_per_day if rental_period == "day" else Listing.price_per_month
    valid_rental_rate = case((rental_rate > 0, rental_rate), else_=None)
    native_price = valid_rental_rate if purpose == "rent" else case(
        (Listing.transaction == "rent", valid_rental_rate), else_=Listing.price,
    )
    rate = Decimal(str(usd_to_dop))
    usd_price = native_price if price_currency == "USD" else native_price * rate
    dop_price = native_price / rate if price_currency == "USD" else native_price
    return case(
        (Listing.currency == "USD", usd_price),
        (Listing.currency == "DOP", dop_price),
        else_=None,
    )


class _PolygonType(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw):
        return "POLYGON"


def spatially_usable():
    """A location review is required, including an exact current public projection."""
    return and_(ListingLocationReview.listing_id.is_not(None), public_coordinate_expression())


def _safe_coordinate(column, bound):
    # SQL may evaluate math before WHERE checks location eligibility.
    return case((column.between(-bound, bound), cast(column, Double)), else_=0.0)


def spatial_membership(shape: Radius | Polygon):
    if isinstance(shape, Radius):
        # Haversine without the final inverse sine: both sides stay in its
        # monotonic domain. Longitude differences naturally wrap at ±180°.
        angle = shape.km / EARTH_RADIUS_KM + 1e-14
        lat = func.radians(_safe_coordinate(Listing.latitude, 90))
        lng = func.radians(_safe_coordinate(Listing.longitude, 180))
        center_lat, center_lng = radians(shape.latitude), radians(shape.longitude)
        north_south = func.power(func.sin((lat - center_lat) / 2.0), 2.0)
        east_west = func.cos(lat) * cos(center_lat) * func.power(func.sin((lng - center_lng) / 2.0), 2.0)
        latitude_span = degrees(angle)
        return and_(
            Listing.latitude.between(shape.latitude - latitude_span, shape.latitude + latitude_span),
            north_south + east_west <= sin(angle / 2.0) ** 2,
        )

    # Multiplying by the Mercator radius gives the native PostgreSQL polygon
    # operator metre-scale coordinates, including on-edge matches.
    longitude = func.radians(_safe_coordinate(Listing.longitude, 180))
    latitude = func.radians(_safe_coordinate(Listing.latitude, MAX_MAP_LATITUDE))
    point = func.point(longitude * MERCATOR_RADIUS_M, func.asinh(func.tan(latitude)) * MERCATOR_RADIUS_M)
    vertices = ",".join(f"({x * MERCATOR_RADIUS_M},{y * MERCATOR_RADIUS_M})" for x, y in shape.projected)
    polygon = cast(literal(f"({vertices})"), _PolygonType())
    return and_(Listing.latitude.between(shape.south, shape.north), Listing.longitude.between(shape.west, shape.east),
                point.bool_op("<@")(polygon))
