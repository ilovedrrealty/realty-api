"""Admin-only location review. Nothing here sends messages or contacts providers."""
import csv
import io
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import and_, case, func, not_, or_
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from database import get_db
from models.listing import Listing
from models.listing_location_review import ListingLocationReview as Review, ListingLocationReviewRevision as Revision
from schemas.location_review import SaveLocationReview, LocationReviewDetail, LocationReviewPage, LocationReviewSummary
from utils.permission import require_admin
from utils.limiter import limiter
from utils.listing_search import escape_like
from utils.request_security import require_json_mutation
from utils.location_quality import coordinate_pair, public_coordinate_expression, public_location, projection_mismatch, add_revision

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
ListingStatus = Literal["active", "pending_approval", "draft", "rejected", "archived", "all"]
ReviewFilter = Literal["all", "missing", "unreviewed", "needs_review", "verified", "private"]


class PrivateLocationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request):
            try:
                require_json_mutation(request)
                if int(request.headers.get("content-length", "0")) > 16 * 1024:
                    raise HTTPException(413, "Location review exceeds the size limit")
                consumed, receive = 0, request.receive
                async def bounded_receive():
                    nonlocal consumed
                    message = await receive()
                    consumed += len(message.get("body", b""))
                    if consumed > 16 * 1024:
                        raise HTTPException(413, "Location review exceeds the size limit")
                    return message
                request = Request(request.scope, receive=bounded_receive)
                await request.body()
                response = await original(request)
                response.headers.update(NO_STORE)
                return response
            except StarletteHTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers={**(exc.headers or {}), **NO_STORE})
            except (RequestValidationError, ValueError):
                # Do not echo private source references, notes or URL parameters.
                return JSONResponse(status_code=422, content={"detail": "Check the location, source and confirmation fields"}, headers=NO_STORE)
            except SQLAlchemyError:
                return JSONResponse(status_code=503, content={"detail": "Location reviews are temporarily unavailable. Reload before retrying a save."}, headers=NO_STORE)
        return handler


router = APIRouter(prefix="/admin/location-review", tags=["admin-location-review"], route_class=PrivateLocationRoute)


def base_query(db, listing_status="active", q=None):
    query = db.query(Listing, Review).outerjoin(Review, Review.listing_id == Listing.id)
    if listing_status != "all":
        query = query.filter(Listing.status == listing_status)
    if q:
        pattern = "%" + escape_like(q.strip()) + "%"
        query = query.filter(or_(Listing.title.ilike(pattern, escape="\\"), Listing.location.ilike(pattern, escape="\\"), Listing.source_ref.ilike(pattern, escape="\\")))
    return query


def expressions():
    has_coordinates = or_(
        and_(Review.listing_id.is_not(None), Review.latitude.between(-90,90), Review.longitude.between(-180,180)),
        and_(Review.listing_id.is_(None), Listing.latitude.between(-90,90), Listing.longitude.between(-180,180)))
    has_coordinates = func.coalesce(has_coordinates, False)
    mismatch = and_(Review.listing_id.is_not(None), or_(
        and_(Review.review_status == "verified", Review.public_precision == "exact", or_(
            Listing.latitude.is_distinct_from(Review.latitude), Listing.longitude.is_distinct_from(Review.longitude))),
        and_(or_(Review.review_status != "verified", Review.public_precision != "exact"), or_(
            Listing.latitude.is_not(None), Listing.longitude.is_not(None), Listing.maps_url.is_not(None)))))
    needs = or_(Review.review_status == "needs_review", mismatch)
    verified = and_(Review.review_status == "verified", has_coordinates, not_(mismatch))
    return has_coordinates, func.coalesce(needs, False), func.coalesce(verified, False)


def filter_review(query, review_filter):
    has_coordinates, needs, verified = expressions()
    filters = {"missing": not_(has_coordinates), "needs_review": needs, "verified": verified,
        "unreviewed": not_(or_(needs, verified)),
        "private": and_(has_coordinates, Review.public_precision.in_(["area", "hidden"]))}
    return query.filter(filters[review_filter]) if review_filter != "all" else query


def row_data(listing, review):
    latitude = review.latitude if review else listing.latitude
    longitude = review.longitude if review else listing.longitude
    mismatch = projection_mismatch(listing, review)
    status = review.review_status if review else "unreviewed"
    if mismatch or status == "verified" and not coordinate_pair(latitude, longitude):
        status = "needs_review"
    return {"listing_id": listing.id, "title": listing.title, "location": listing.location,
        "source_ref": listing.source_ref, "listing_status": listing.status, "listing_updated_at": listing.updated_at,
        "version": review.version if review else 0, "review_status": status,
        "public_precision": review.public_precision if review else "exact",
        "has_coordinates": bool(coordinate_pair(latitude, longitude)),
        "public_mapped": public_location(listing, review)["latitude"] is not None,
        "projection_mismatch": mismatch, "reviewed_at": review.reviewed_at if review else None}


def detail_data(db, listing, review):
    history = db.query(Revision).filter(Revision.listing_id == listing.id).order_by(Revision.version.desc()).limit(20).all()
    return {**row_data(listing, review), "latitude": review.latitude if review else listing.latitude,
        "longitude": review.longitude if review else listing.longitude, "maps_url": review.maps_url if review else listing.maps_url,
        "source_type": review.source_type if review else None, "source_reference": (review.source_reference or "") if review else "",
        "review_note": (review.review_note or "") if review else "", "reviewed_by": review.reviewed_by if review else None,
        "history": [{"version": item.version, "action": item.action, "actor_id": str(item.actor_id) if item.actor_id else None,
            "created_at": item.created_at.isoformat(), "snapshot": item.snapshot} for item in history]}


@router.get("/summary", response_model=LocationReviewSummary)
def summary(listing_status: ListingStatus = "active", q: str | None = Query(None, max_length=100),
            user=Depends(require_admin), db: Session = Depends(get_db)):
    has_coordinates, needs, verified = expressions()
    count = lambda expr: func.coalesce(func.sum(case((expr, 1), else_=0)), 0)
    result = base_query(db, listing_status, q).with_entities(func.count(Listing.id), count(has_coordinates), count(verified),
        count(needs), count(public_coordinate_expression()), count(and_(has_coordinates, Review.public_precision.in_(["area", "hidden"]))),
        count(func.length(func.trim(func.coalesce(Review.source_reference, ""))) >= 3)).one()
    total, available, verified_count, needs_count, mapped, private, sources = map(int, result)
    return {"total": total, "coordinate_available": available, "missing_coordinates": total-available,
        "verified": verified_count, "needs_review": needs_count, "unreviewed": total-verified_count-needs_count,
        "public_mapped": mapped, "private_positions": private, "with_source_reference": sources}


@router.get("", response_model=LocationReviewPage)
def queue(listing_status: ListingStatus = "active", review_filter: ReviewFilter = "missing",
          q: str | None = Query(None, max_length=100), limit: int = Query(25, ge=1, le=100),
          offset: int = Query(0, ge=0, le=10000), user=Depends(require_admin), db: Session = Depends(get_db)):
    query = filter_review(base_query(db, listing_status, q), review_filter)
    total = query.count()
    rows = query.order_by(Listing.location, Listing.title, Listing.id).offset(offset).limit(limit).all()
    return {"items": [row_data(*row) for row in rows], "total": total, "limit": limit, "offset": offset}


def csv_cell(value):
    text = "" if value is None else str(value)
    return "'"+text if text[:1] in {"\t", "\r", "\n"} or text.lstrip(" \t\r\n")[:1] in {"=", "+", "-", "@"} else text


@router.get("/request-worksheet")
def request_worksheet(listing_status: ListingStatus = "active", q: str | None = Query(None, max_length=100),
                      user=Depends(require_admin), db: Session = Depends(get_db)):
    # A source-request worksheet only, never an automatic review/import action.
    query = filter_review(base_query(db, listing_status, q), "missing")
    total = query.count()
    if total > 5000:
        raise HTTPException(413, "Narrow the worksheet to 5000 listings or fewer")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["listing_id", "source_ref", "title", "recorded_area", "latitude", "longitude", "maps_url", "source_reference"])
    for listing, _ in query.order_by(Listing.id).all():
        writer.writerow([str(listing.id), csv_cell(listing.source_ref), csv_cell(listing.title), csv_cell(listing.location), "", "", "", ""])
    payload = ("\ufeff"+stream.getvalue()).encode("utf-8")
    if len(payload) > 2 * 1024 * 1024:
        raise HTTPException(413, "Narrow the worksheet to a smaller result set")
    return Response(content=payload, media_type="text/csv",
        headers={**NO_STORE, "Content-Disposition": 'attachment; filename="property-location-request.csv"'})


@router.get("/{listing_id}", response_model=LocationReviewDetail)
def detail(listing_id: UUID, user=Depends(require_admin), db: Session = Depends(get_db)):
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(404, "Listing not found")
    return detail_data(db, listing, db.get(Review, listing_id))


@router.patch("/{listing_id}", response_model=LocationReviewDetail)
@limiter.limit("30/minute")
def save(request: Request, response: Response, listing_id: UUID, body: SaveLocationReview,
         user=Depends(require_admin), db: Session = Depends(get_db)):
    # Lock order is always Listing then Review. Generic location edits use the
    # same lock, preventing stale browser reviews from overwriting newer edits.
    listing = db.query(Listing).filter(Listing.id == listing_id).with_for_update().first()
    if listing is None:
        raise HTTPException(404, "Listing not found")
    review = db.get(Review, listing_id)
    if (review.version if review else 0) != body.expected_version or listing.updated_at != body.expected_listing_updated_at:
        raise HTTPException(409, "This listing or review changed. Reload it before saving.")
    if review is None:
        review = Review(listing_id=listing.id, version=1)
        db.add(review)
    else:
        review.version += 1
    for key in ("latitude", "longitude", "maps_url", "review_status", "public_precision", "source_type", "source_reference", "review_note"):
        setattr(review, key, getattr(body, key))
    now = datetime.now(timezone.utc)
    review.reviewed_at = now
    review.reviewed_by = user.id
    review.updated_at = listing.updated_at = now
    publish = body.review_status == "verified" and body.public_precision == "exact"
    listing.latitude = body.latitude if publish else None
    listing.longitude = body.longitude if publish else None
    # Publish a canonical destination from the confirmed pair, never a viewport
    # or an evidence link containing the private source-reference context.
    listing.maps_url = f"https://www.google.com/maps/search/?api=1&query={body.latitude},{body.longitude}" if publish else None
    db.flush()
    add_revision(db, review, user.id, "location_reviewed")
    db.commit()
    db.refresh(listing); db.refresh(review)
    return detail_data(db, listing, review)
