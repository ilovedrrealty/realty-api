import logging
import filetype
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile, File
from sqlalchemy.orm import Session, aliased
from sqlalchemy import and_, func, or_
from typing import Annotated, List, Literal, Optional
from pydantic import StringConstraints
from datetime import datetime, timezone
from uuid import UUID

logger = logging.getLogger(__name__)

from database import get_db
from models.listing import Listing
from models.listing_edit import ListingEdit
from models.listing_event import ListingEvent
from models.inquiry import Inquiry
from models.lead import Lead
from models.user import User
from models.deal_request import DealRequest
from models.listing_location_review import ListingLocationReview
from schemas.listing import (ListingCreate, ListingUpdate, ListingResponse, ListingPageResponse, ListingFilterOptionsResponse,
    ListingMapResponse, ListingMapPoint, ListingMapArea, ListingMapBounds, PublicListingResponse, PublicListingPageResponse)
from schemas.deal_request import DealRequestCreate
from utils.auth import get_current_user
from utils.permission import require_role
from utils.storage import upload_image, upload_pdf
from utils.share_image import generate_share_image, maybe_regenerate_share_image
from utils.limiter import limiter
from utils.listing_constants import ALL_REGIONS
from utils.listing_search import normalized_location, normalized_location_column, escape_like, search_price_expression
from utils.location_quality import public_location, public_coordinate_expression, stage_location_change

router = APIRouter(prefix="/listings", tags=["listings"])


ALLOWED_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
MAX_FILES = 25


@router.post("/upload-images")
@limiter.limit("20/minute")
async def upload_images(
    request: Request,
    files: List[UploadFile] = File(...),
    user=Depends(require_role("realtor", "admin", "owner")),
):
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_FILES} images allowed")

    urls: list[str] = []
    for f in files:
        data = await f.read()
        if len(data) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail=f"{f.filename} exceeds 20 MB limit")
        detected = filetype.guess(data[:2048])
        if detected is None or detected.mime not in ALLOWED_TYPES:
            raise HTTPException(status_code=415, detail="Invalid file type")
        try:
            url = upload_image(data, detected.mime, str(user.id))
        except Exception:
            logger.exception("Image upload failed for user %s", user.id)
            raise HTTPException(status_code=500, detail="Image upload failed. Please try again.")
        urls.append(url)

    return {"urls": urls}


SORT_COLUMNS = {
    "new":  lambda: Listing.created_at.desc(),
    "low":  lambda: Listing.price.asc(),
    "high": lambda: Listing.price.desc(),
    "roi":  lambda: Listing.roi.desc(),
}

LocationFilter = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
PropertyTypeFilter = Literal["villa", "apartment", "condo", "land", "commercial"]


@router.post("/upload-agreement")
@limiter.limit("10/minute")
async def upload_agreement(
    request: Request,
    file: UploadFile = File(...),
    user=Depends(require_role("realtor", "admin")),
):
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Agreement PDF must be under 10 MB")
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="Only PDF files are accepted")
    try:
        url = upload_pdf(data, str(user.id))
    except Exception:
        logger.exception("PDF upload failed for user %s", user.id)
        raise HTTPException(status_code=500, detail="PDF upload failed. Please try again.")
    return {"url": url}


def _apply_listing_filters(
    query,
    purpose: Optional[str] = None,
    type: Optional[str] = None,
    region: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    beds: Optional[int] = None,
    min_roi: Optional[float] = None,
    features: Optional[List[str]] = None,
    locations: Optional[List[str]] = None,
    types: Optional[List[str]] = None,
    search_price=None,
):
    """Shared by the item query, the total count, and the aggregate (median/avg)
    queries so they can never drift out of sync with each other."""
    query = query.filter(Listing.status == "active")
    if purpose in ("sale", "rent"):
        query = query.filter(Listing.transaction.in_((purpose, "both")))
    elif purpose == "investment":
        query = query.filter(Listing.roi >= 7)
    if types:
        query = query.filter(Listing.type.in_(types))
    elif type and type != "All":
        query = query.filter(Listing.type == type.lower())
    if locations:
        query = query.filter(normalized_location_column().in_({normalized_location(value) for value in locations}))
    elif region:
        query = query.filter(normalized_location_column().like(f"%{escape_like(normalized_location(region))}%", escape="\\"))
    if beds is not None:
        query = query.filter(Listing.bedrooms >= beds)
    if min_roi:
        query = query.filter(Listing.roi >= min_roi)
    if search_price is not None:
        if min_price is not None:
            query = query.filter(search_price >= min_price)
        if max_price is not None:
            query = query.filter(search_price <= max_price)
    else:
        # Preserve pricing semantics for clients that have not opted into the
        # explicit currency/period contract.
        price_conditions = [Listing.price >= (min_price or 0)]
        if max_price is not None:
            price_conditions.append(Listing.price <= max_price)
        query = query.filter(or_(Listing.transaction == "rent", and_(*price_conditions)))
    if features:
        query = query.filter(Listing.features.op("@>")(features))
    return query


def public_responses(db, rows):
    """Batch evidence lookup; expose policy/result only, never private evidence."""
    if not rows:
        return []
    reviews = {row.listing_id: row for row in db.query(ListingLocationReview).filter(ListingLocationReview.listing_id.in_([item.id for item in rows])).all()}
    return [PublicListingResponse.model_validate(item).model_copy(update=public_location(item, reviews.get(item.id))) for item in rows]


@router.get("", response_model=PublicListingPageResponse)
def get_active_listings(
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=60),
    purpose: Optional[str] = Query(None, description="sale | rent | investment (omit for all)"),
    type: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    locations: Optional[List[LocationFilter]] = Query(None, max_length=60, description="Repeat for multiple exact locations; overrides legacy region"),
    types: Optional[List[PropertyTypeFilter]] = Query(None, max_length=5, description="Repeat for multiple property types; overrides legacy type"),
    price_currency: Optional[Literal["USD", "DOP"]] = Query(None, description="Opt into comparable search prices in this currency"),
    usd_to_dop: float = Query(59.5, ge=1, le=1000, allow_inf_nan=False, description="Estimated DOP per USD; default 59.5, not a guaranteed exchange rate"),
    rental_period: Literal["month", "day"] = Query("month", description="Rental rate period used for comparable search prices"),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    beds: Optional[int] = Query(None, ge=0),
    min_roi: Optional[float] = Query(None, ge=0),
    features: Optional[List[str]] = Query(None),
    sort: str = Query("new"),
    exclude_ids: Optional[List[UUID]] = Query(None),
    include_aggregates: bool = Query(True),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    search_price = search_price_expression(price_currency, usd_to_dop, rental_period, purpose) if price_currency else None
    base = _apply_listing_filters(
        db.query(Listing), purpose=purpose, type=type, region=region,
        min_price=min_price, max_price=max_price, beds=beds, min_roi=min_roi, features=features,
        locations=locations, types=types, search_price=search_price,
    )
    if exclude_ids:
        base = base.filter(~Listing.id.in_(exclude_ids))

    total = base.with_entities(func.count(Listing.id)).scalar() or 0

    order_by = SORT_COLUMNS.get(sort, SORT_COLUMNS["new"])()
    if search_price is not None and sort in ("low", "high"):
        order_by = (search_price.asc() if sort == "low" else search_price.desc()).nullslast()
    page_query = base.order_by(order_by, Listing.id.desc()).offset((page - 1) * page_size).limit(page_size)
    if search_price is not None:
        rows = page_query.add_columns(search_price.label("search_price")).all()
        items = [item.model_copy(update={
            "search_price": amount,
            "search_price_currency": price_currency,
            "search_price_period": rental_period if purpose == "rent" or listing.transaction == "rent" else None,
        }) for item, (listing, amount) in zip(public_responses(db, [row[0] for row in rows]), rows)]
    else:
        items = public_responses(db, page_query.all())

    median_price = None
    avg_roi = None
    if include_aggregates:
        sale_base = base if search_price is not None and purpose == "rent" else base.filter(Listing.transaction != "rent")
        median_expression = search_price if search_price is not None else Listing.price
        median_price = sale_base.with_entities(
            func.percentile_cont(0.5).within_group(median_expression.asc())
        ).scalar()
        avg_roi = base.filter(Listing.roi > 0).with_entities(func.avg(Listing.roi)).scalar()

    return PublicListingPageResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        median_price=float(median_price) if median_price is not None else None,
        avg_roi=float(avg_roi) if avg_roi is not None else None,
    )


@router.get("/map", response_model=ListingMapResponse)
def get_listing_map(
    response: Response,
    purpose: Optional[str] = Query(None), type: Optional[str] = Query(None), region: Optional[str] = Query(None),
    locations: Optional[List[LocationFilter]] = Query(None, max_length=60),
    types: Optional[List[PropertyTypeFilter]] = Query(None, max_length=5),
    price_currency: Optional[Literal["USD", "DOP"]] = Query(None),
    usd_to_dop: float = Query(59.5, ge=1, le=1000, allow_inf_nan=False),
    rental_period: Literal["month", "day"] = Query("month"),
    min_price: Optional[float] = Query(None, ge=0, allow_inf_nan=False),
    max_price: Optional[float] = Query(None, ge=0, allow_inf_nan=False),
    beds: Optional[int] = Query(None, ge=0), min_roi: Optional[float] = Query(None, ge=0, allow_inf_nan=False),
    features: Optional[List[str]] = Query(None), exclude_ids: Optional[List[UUID]] = Query(None),
    limit: int = Query(5000, ge=1, le=5000),
    south: Optional[float] = Query(None, ge=-90, le=90, allow_inf_nan=False),
    north: Optional[float] = Query(None, ge=-90, le=90, allow_inf_nan=False),
    west: Optional[float] = Query(None, ge=-180, le=180, allow_inf_nan=False),
    east: Optional[float] = Query(None, ge=-180, le=180, allow_inf_nan=False),
    db: Session = Depends(get_db),
):
    """All matching public inventory, independent of card pagination.

    Exact points are bounded and whitelisted. Missing coordinates become counts
    by recorded area, never guessed property positions. Bounds refine exact
    points only; totals and area summaries always describe the full filter.
    """
    response.headers["Cache-Control"] = "no-store"
    box = (south, north, west, east)
    if any(value is not None for value in box) and (any(value is None for value in box) or south > north):
        raise HTTPException(422, "Provide a complete valid map viewport")
    search_price = search_price_expression(price_currency, usd_to_dop, rental_period, purpose) if price_currency else None
    base = _apply_listing_filters(db.query(Listing.id), purpose=purpose, type=type, region=region,
        min_price=min_price, max_price=max_price, beds=beds, min_roi=min_roi, features=features,
        locations=locations, types=types, search_price=search_price)
    base = base.outerjoin(ListingLocationReview, ListingLocationReview.listing_id == Listing.id)
    if exclude_ids:
        base = base.filter(~Listing.id.in_(exclude_ids))
    total = base.with_entities(func.count(Listing.id)).scalar() or 0
    valid = public_coordinate_expression()
    mapped = base.filter(valid)
    count, min_lat, min_lng, max_lat, max_lng = mapped.with_entities(func.count(Listing.id),
        func.min(Listing.latitude), func.min(Listing.longitude), func.max(Listing.latitude), func.max(Listing.longitude)).one()
    count = int(count or 0)
    # SQL NOT(NULL) is unknown; explicitly include partial and missing pairs.
    missing = base.filter(or_(Listing.latitude.is_(None), Listing.longitude.is_(None), ~valid))
    location_key = normalized_location_column()
    groups = missing.filter(or_(ListingLocationReview.listing_id.is_(None), ListingLocationReview.public_precision != "hidden")).with_entities(func.min(func.btrim(Listing.location)), func.count(Listing.id)).group_by(location_key)
    area_rows = groups.order_by(func.count(Listing.id).desc(), location_key).limit(200).all()
    areas = [ListingMapArea(location=area, count=amount) for area, amount in area_rows if area]
    grouped = sum(area.count for area in areas)
    visible = mapped
    if south is not None:
        visible = visible.filter(Listing.latitude.between(south, north))
        visible = visible.filter(Listing.longitude.between(west, east) if west <= east else or_(Listing.longitude >= west, Listing.longitude <= east))
    viewport_total = visible.with_entities(func.count(Listing.id)).scalar() or 0
    price = search_price if search_price is not None else Listing.price
    rows = visible.with_entities(Listing.id, Listing.title, Listing.location, Listing.latitude, Listing.longitude,
        price, Listing.currency, Listing.transaction, ListingLocationReview.review_status).order_by(Listing.id).limit(limit).all()
    items = [ListingMapPoint(id=row[0], title=row[1], location=row[2], latitude=row[3], longitude=row[4], price=row[5],
        currency=price_currency or row[6], transaction=row[7],
        price_period=rental_period if search_price is not None and (purpose == "rent" or row[7] == "rent") else None,
        location_verified=row[8] == "verified") for row in rows]
    return ListingMapResponse(items=items, total=total, mapped_total=count, unmapped_total=total-count,
        areas=areas, ungrouped_total=max(0, total-count-grouped), viewport_total=viewport_total,
        returned=len(items), limit=limit, truncated=viewport_total > limit,
        bounds=ListingMapBounds(south=min_lat, west=min_lng, north=max_lat, east=max_lng) if count else None)


@router.get("/filter-options", response_model=ListingFilterOptionsResponse)
def get_listing_filter_options(db: Session = Depends(get_db)):
    """Compact inventory facets; no listing/contact details or client page scan."""
    location_key = normalized_location_column()
    rows = (
        db.query(location_key, func.min(func.btrim(Listing.location)), func.count(Listing.id))
        .filter(Listing.status == "active", func.btrim(Listing.location) != "")
        .group_by(location_key)
        .all()
    )
    canonical_names = {normalized_location(value): value for value in ALL_REGIONS}
    locations = [
        {"value": canonical_names.get(key, label), "count": count}
        for key, label, count in sorted(rows, key=lambda row: row[0])
    ]
    type_rows = (
        db.query(Listing.type, func.count(Listing.id))
        .filter(Listing.status == "active")
        .group_by(Listing.type)
        .all()
    )
    return {"locations": locations, "types": [{"value": value, "count": count} for value, count in sorted(type_rows)]}


def _my_listings_base_query(db: Session, user):
    Submitter = aliased(User)
    leads_subq = (
        db.query(Lead.property_id, func.count(Lead.id).label("cnt"))
        .group_by(Lead.property_id)
        .subquery()
    )
    # Owners see listings where they are submitted_by (legacy) OR owner_id (new flow)
    if user.role == "admin":
        filter_cond = or_(Listing.submitted_by == user.id, Listing.owner_id == user.id, Listing.assigned_realtor_id == user.id)
    elif user.role == "owner":
        filter_cond = or_(Listing.submitted_by == user.id, Listing.owner_id == user.id)
    else:
        filter_cond = or_(Listing.submitted_by == user.id, Listing.assigned_realtor_id == user.id)
    return (
        db.query(Listing, leads_subq.c.cnt, Submitter)
        .outerjoin(leads_subq, leads_subq.c.property_id == Listing.id)
        .outerjoin(Submitter, Submitter.id == Listing.submitted_by)
        .filter(filter_cond)
    )


def _my_listings_pending_sets(db: Session, user):
    pending_deal_ids = {
        r.listing_id
        for r in db.query(DealRequest.listing_id)
        .filter(DealRequest.requested_by == user.id, DealRequest.status == "pending")
        .all()
    }
    pending_edit_ids = {
        r.listing_id
        for r in db.query(ListingEdit.listing_id)
        .filter(ListingEdit.submitted_by == user.id, ListingEdit.status == "pending")
        .all()
    }
    return pending_deal_ids, pending_edit_ids


def _to_my_listing_response(row, pending_deal_ids, pending_edit_ids) -> ListingResponse:
    l, cnt, submitter = row
    return ListingResponse(
        **{c.key: getattr(l, c.key) for c in Listing.__table__.columns},
        leads_count=cnt or 0,
        has_pending_deal_request=l.id in pending_deal_ids,
        has_pending_edit=l.id in pending_edit_ids,
        submitted_by_name=submitter.display_name if submitter else None,
    )


@router.get("/mine", response_model=List[ListingResponse])
def get_my_listings(user=Depends(require_role("realtor", "owner", "admin")), db: Session = Depends(get_db)):
    rows = _my_listings_base_query(db, user).all()
    pending_deal_ids, pending_edit_ids = _my_listings_pending_sets(db, user)
    return [_to_my_listing_response(row, pending_deal_ids, pending_edit_ids) for row in rows]


@router.get("/mine/page", response_model=ListingPageResponse)
def get_my_listings_page(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    pending_review: Optional[bool] = Query(None, description="Only listings with a pending deal request or edit"),
    user=Depends(require_role("realtor", "owner", "admin")),
    db: Session = Depends(get_db),
):
    """Paginated/filtered variant of GET /mine, used by the realtor/owner "My
    Listings" tables. GET /mine itself stays unparameterized and unpaginated —
    the home-dashboard widgets rely on it returning the complete list."""
    query = _my_listings_base_query(db, user)
    if status:
        query = query.filter(Listing.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Listing.title.ilike(like), Listing.location.ilike(like)))

    pending_deal_ids, pending_edit_ids = _my_listings_pending_sets(db, user)
    if pending_review:
        review_ids = pending_deal_ids | pending_edit_ids
        if not review_ids:
            return ListingPageResponse(items=[], total=0, page=page, page_size=page_size)
        query = query.filter(Listing.id.in_(review_ids))

    total = query.with_entities(func.count(Listing.id)).scalar() or 0
    rows = query.order_by(Listing.updated_at.desc(), Listing.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [_to_my_listing_response(row, pending_deal_ids, pending_edit_ids) for row in rows]
    return ListingPageResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/deal", response_model=List[PublicListingResponse])
def get_deal_listings(response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return public_responses(db, db.query(Listing).filter(Listing.is_deal == True, Listing.status == "active").all())


@router.post("", response_model=ListingResponse, status_code=201)
def create_listing(body: ListingCreate, user=Depends(require_role("realtor", "admin", "owner")), db: Session = Depends(get_db)):
    if user.role == "owner" and body.transaction != "rent":
        raise HTTPException(status_code=422, detail="Owners may only submit rental listings.")
    extra: dict = {}
    if user.role == "owner":
        extra["owner_id"] = user.id
    listing = Listing(
        **body.model_dump(),
        submitted_by=user.id,
        status="active" if user.role == "admin" else "pending_approval",
        **extra,
    )
    db.add(listing)
    db.flush()  # get listing.id before commit
    db.add(ListingEvent(listing_id=listing.id, event_type="submitted", actor_id=user.id))
    try:
        listing.share_image_url = generate_share_image(listing)
    except Exception:
        logger.exception("Share image generation failed for listing %s", listing.id)
    db.commit()
    db.refresh(listing)
    return listing


@router.get("/{listing_id}", response_model=PublicListingResponse)
def get_listing(listing_id: UUID, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    AssignedRealtor = aliased(User)
    row = (
        db.query(Listing, User, AssignedRealtor)
        .join(User, User.id == Listing.submitted_by)
        .outerjoin(AssignedRealtor, AssignedRealtor.id == Listing.assigned_realtor_id)
        .filter(Listing.id == listing_id, Listing.status == "active")
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Listing not found", headers={"Cache-Control": "no-store"})
    listing, submitter, assigned_realtor = row
    # The assigned realtor (if any) takes over as the public "Listed by" contact.
    contact = assigned_realtor or submitter
    return public_responses(db, [listing])[0].model_copy(update={"submitted_by_name": contact.display_name})


@router.post("/{listing_id}/deal-request", status_code=201)
def submit_deal_request(
    listing_id: UUID,
    body: DealRequestCreate,
    user=Depends(require_role("realtor", "owner", "admin")),
    db: Session = Depends(get_db),
):
    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if listing.submitted_by != user.id and listing.assigned_realtor_id != user.id and not (user.role == "admin" and listing.owner_id == user.id):
        raise HTTPException(status_code=403, detail="Not your listing")
    if listing.status != "active":
        raise HTTPException(status_code=400, detail="Listing must be active to submit a deal request")
    if listing.is_deal:
        raise HTTPException(status_code=409, detail="Listing is already the deal of the week")
    existing = db.query(DealRequest).filter(
        DealRequest.listing_id == listing_id,
        DealRequest.requested_by == user.id,
        DealRequest.status == "pending",
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="You already have a pending deal request for this listing")
    db.add(DealRequest(
        listing_id=listing_id,
        requested_by=user.id,
        discount_value=body.discount_value,
        discount_type=body.discount_type,
        message=body.message,
    ))
    db.commit()
    return {"message": "Deal request submitted successfully"}


@router.post("/{listing_id}/view", status_code=204)
@limiter.limit("5/minute")
def record_view(request: Request, listing_id: UUID, db: Session = Depends(get_db)):
    listing = db.query(Listing).filter(Listing.id == listing_id, Listing.status == "active").first()
    if listing:
        listing.view_count += 1
        db.commit()


@router.put("/{listing_id}", response_model=ListingResponse)
def update_listing(listing_id: UUID, body: ListingUpdate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    listing = db.query(Listing).filter(Listing.id == listing_id).with_for_update().first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    if listing.submitted_by != user.id and listing.assigned_realtor_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    update_data = body.model_dump(exclude_unset=True)
    if user.role == "owner" and update_data.get("transaction") and update_data["transaction"] != "rent":
        raise HTTPException(status_code=422, detail="Owners may only submit rental listings.")

    # Active listings edited by realtors go into a pending-edit queue instead of
    # updating in place so the live listing is not disrupted until admin approves.
    if listing.status == "active" and user.role != "admin":
        # Supersede any existing pending edit for this listing
        db.query(ListingEdit).filter(
            ListingEdit.listing_id == listing.id,
            ListingEdit.status == "pending",
        ).delete(synchronize_session=False)

        edit = ListingEdit(
            listing_id=listing.id,
            submitted_by=user.id,
            proposed_data=body.model_dump(exclude_unset=True, mode='json'),
        )
        db.add(edit)
        db.add(ListingEvent(listing_id=listing.id, event_type="edit_submitted", actor_id=user.id))
        db.commit()
        db.refresh(listing)
        return listing

    was_rejected = listing.status == "rejected"
    changed = body.model_dump(exclude_none=True)
    stage_location_change(db, listing, changed, user.id)
    for field, value in changed.items():
        setattr(listing, field, value)
    if was_rejected:
        listing.status = "pending_approval"
        listing.rejection_reason = None
    listing.updated_at = datetime.now(timezone.utc)
    maybe_regenerate_share_image(listing, changed.keys())
    db.commit()
    db.refresh(listing)
    return listing


@router.delete("/{listing_id}", status_code=204)
def archive_listing(listing_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    listing = db.query(Listing).filter(Listing.id == listing_id).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    if listing.submitted_by != user.id and listing.assigned_realtor_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    listing.status = "archived"
    db.commit()
