"""Read-only search expressions; native listing prices are never changed."""

from decimal import Decimal
import unicodedata
from sqlalchemy import case, func

from models.listing import Listing


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
