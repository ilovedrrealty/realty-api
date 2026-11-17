from datetime import timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, and_, case, cast, func, select, true
from sqlalchemy.orm import Session

from models.listing import Listing
from models.listing_location_review import ListingLocationReview
from schemas.market_reports import MarketReport, MarketReportGroup
from utils.listing_constants import ALL_REGIONS
from utils.listing_search import normalized_location, normalized_location_column


CURRENCIES = ("USD", "DOP")
LIVING_AREA_TYPES = ("villa", "apartment", "condo")
MAX_STORED_PRICE = Decimal("9999999999.99")
SQFT_TO_SQM = Decimal("0.09290304")
LOCAL_TIME_ZONE = "America/Santo_Domingo"
CANONICAL_LOCATIONS = {normalized_location(label): label for label in ALL_REGIONS}


def canonical_market_location(value: str) -> str | None:
    return CANONICAL_LOCATIONS.get(normalized_location(value))


def _median(value):
    # Opposing orders select the middle one or two native Numeric values.
    # Averaging them preserves sub-cent midpoints without a float conversion.
    lower = func.percentile_disc(0.5).within_group(value.asc())
    upper = func.percentile_disc(0.5).within_group(value.desc())
    return (lower + upper) / 2


def _snapshot(location: str | None):
    living_area = Listing.type.in_(LIVING_AREA_TYPES)
    lot_area = Listing.type == "land"
    # A bounded comparison also excludes Postgres Numeric NaN.
    price = case((Listing.price.between(Decimal("0.01"), MAX_STORED_PRICE), Listing.price))
    area = case((living_area, Listing.area_sqft), (lot_area, Listing.lot_size_sqft))
    basis = case((living_area, "living"), (lot_area, "lot"))
    published = select(
        Listing.type.label("property_type"),
        Listing.currency,
        price.label("price"),
        area.label("area_sqft"),
        basis.label("area_basis"),
    ).where(Listing.status == "active", Listing.transaction.in_(("sale", "both")))
    if location is not None:
        hidden = select(ListingLocationReview.listing_id).where(
            ListingLocationReview.listing_id == Listing.id,
            ListingLocationReview.public_precision == "hidden",
        ).exists()
        published = published.where(normalized_location_column() == normalized_location(location), ~hidden)

    inventory = published.cte("inventory")
    classified = inventory.c.currency.in_(CURRENCIES)
    totals = select(
        func.count().label("total_listings"),
        func.count().filter(classified.is_not(True)).label("unclassified_currency_count"),
    ).select_from(inventory).subquery()
    asking = inventory.c.price
    sqft = inventory.c.area_sqft
    price_per_sqm = case((and_(asking.is_not(None), sqft > 0), asking / (sqft * SQFT_TO_SQM)))
    groups = select(
        inventory.c.property_type,
        inventory.c.currency,
        func.count().label("listing_count"),
        func.count(asking).label("price_count"),
        func.min(asking).label("price_min"),
        func.round(_median(asking), 3).label("price_median"),
        func.max(asking).label("price_max"),
        func.count(price_per_sqm).label("size_count"),
        func.round(_median(price_per_sqm), 2).label("median_price_per_sqm"),
        inventory.c.area_basis,
    ).where(classified).group_by(
        inventory.c.property_type, inventory.c.currency, inventory.c.area_basis,
    ).subquery()
    as_of = func.statement_timestamp(type_=DateTime(timezone=True))
    return select(
        as_of.label("as_of"),
        cast(func.timezone(LOCAL_TIME_ZONE, as_of), Date).label("local_date"),
        *totals.c,
        *groups.c,
    ).select_from(totals.outerjoin(groups, true())).order_by(groups.c.property_type, groups.c.currency)


def public_report(db: Session, location: str | None) -> MarketReport:
    rows = db.execute(_snapshot(location)).mappings().all()
    snapshot = rows[0]
    return MarketReport(
        as_of=snapshot["as_of"].astimezone(timezone.utc),
        local_date=snapshot["local_date"],
        location=location,
        total_listings=snapshot["total_listings"],
        unclassified_currency_count=snapshot["unclassified_currency_count"],
        groups=[MarketReportGroup.model_validate(row) for row in rows if row["property_type"] is not None],
    )
