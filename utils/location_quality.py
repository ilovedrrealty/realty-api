"""Location evidence, safe projections and invalidation without network access."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import parse_qs, urlsplit
from fastapi import HTTPException
from sqlalchemy import and_, or_, not_
from models.listing import Listing
from models.listing_location_review import ListingLocationReview, ListingLocationReviewRevision

LOCATION_FIELDS = frozenset({"latitude", "longitude", "maps_url", "location"})


def coordinate_pair(latitude, longitude):
    try:
        lat, lng = Decimal(str(latitude)), Decimal(str(longitude))
        return lat.is_finite() and lng.is_finite() and -90 <= lat <= 90 and -180 <= lng <= 180
    except (InvalidOperation, ValueError, TypeError):
        return False


def safe_maps_url(value):
    if not value:
        return True
    # Reuse the reviewed import boundary (allowlisted HTTPS Maps shares, no keys).
    from utils.bulk_import import _parse_location_fields
    return not _parse_location_fields({"maps_url": value})[3]


def map_destination_coordinates(value):
    """Explicit destination only. /@ viewport coordinates are never evidence."""
    if not value or not safe_maps_url(value):
        return None
    url = urlsplit(value)
    query = parse_qs(url.query)
    candidates = [v for key in ("query", "q", "destination") for v in query.get(key, [])]
    pairs = []
    for candidate in candidates:
        match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", candidate)
        if match and coordinate_pair(*match.groups()):
            pairs.append(tuple(Decimal(x) for x in match.groups()))
    for match in re.finditer(r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)", value):
        if coordinate_pair(*match.groups()):
            pairs.append(tuple(Decimal(x) for x in match.groups()))
    return pairs[0] if pairs and len(set(pairs)) == 1 else None


def validate_review_input(body):
    if "\x00" in body.source_reference or "\x00" in body.review_note:
        raise ValueError("Text fields cannot contain null characters")
    if (body.latitude is None) != (body.longitude is None):
        raise ValueError("Provide both coordinates or neither")
    if body.latitude is not None and not coordinate_pair(body.latitude, body.longitude):
        raise ValueError("Coordinates must be a finite valid pair")
    if not safe_maps_url(body.maps_url):
        raise ValueError("Use an allowlisted Google Maps HTTPS share without credentials")
    if body.review_status == "verified":
        if not body.confirmed_property or body.latitude is None or not body.source_type or len(body.source_reference) < 3:
            raise ValueError("Confirm the actual property position and provide source evidence")
        if body.source_type == "map_destination":
            destination = map_destination_coordinates(body.maps_url)
            if destination is None or any(abs(a-b) > Decimal("0.00001") for a, b in zip(destination, (body.latitude, body.longitude))):
                raise ValueError("The Maps destination must identify the supplied property coordinates, not a viewport")


def public_coordinate_expression():
    valid = and_(Listing.latitude.between(-90, 90), Listing.longitude.between(-180, 180))
    approved = and_(ListingLocationReview.review_status == "verified", ListingLocationReview.public_precision == "exact",
        Listing.latitude == ListingLocationReview.latitude, Listing.longitude == ListingLocationReview.longitude)
    return and_(valid, or_(ListingLocationReview.listing_id.is_(None), approved))


def projection_mismatch(listing, review):
    if review is None:
        return False
    if review.review_status == "verified" and review.public_precision == "exact":
        return not coordinate_pair(listing.latitude, listing.longitude) or listing.latitude != review.latitude or listing.longitude != review.longitude
    return any(value is not None for value in (listing.latitude, listing.longitude, listing.maps_url))


def public_location(listing, review):
    precision = review.public_precision if review else "exact"
    mapped = coordinate_pair(listing.latitude, listing.longitude) and (review is None or (
        review.review_status == "verified" and precision == "exact" and not projection_mismatch(listing, review)))
    return {"latitude": float(listing.latitude) if mapped else None, "longitude": float(listing.longitude) if mapped else None,
        "maps_url": listing.maps_url if mapped and safe_maps_url(listing.maps_url) else None,
        "location_precision": precision if precision != "exact" else ("exact" if mapped else "area"),
        "location_verified": bool(mapped and review and review.review_status == "verified")}


def review_snapshot(review):
    return {"version": review.version, "review_status": review.review_status, "public_precision": review.public_precision,
        "latitude": str(review.latitude) if review.latitude is not None else None,
        "longitude": str(review.longitude) if review.longitude is not None else None,
        "maps_url": review.maps_url, "source_type": review.source_type, "source_reference": review.source_reference,
        "review_note": review.review_note, "reviewed_by": str(review.reviewed_by) if review.reviewed_by else None,
        "reviewed_at": review.reviewed_at.isoformat() if review.reviewed_at else None}


def add_revision(db, review, actor_id, action):
    db.add(ListingLocationReviewRevision(listing_id=review.listing_id, version=review.version,
        actor_id=actor_id, action=action, snapshot=review_snapshot(review)))


def _same_location_value(key, value, previous):
    if key in {"latitude", "longitude"} and value is not None and previous is not None:
        try:
            return Decimal(str(value)) == Decimal(str(previous))
        except (InvalidOperation, ValueError, TypeError):
            return False
    return value == previous


def stage_location_change(db, listing, changes, actor_id, *, new_submission=False):
    """Call with Listing row locked, before applying a generic edit.

    New/edit/imported coordinates are candidate evidence, never an Admin review.
    Public fields remain empty after an edit until that candidate is confirmed.
    An existing area/hidden choice survives all generic edit paths. For a new
    listing, pass supplied location fields separately while Listing's public
    fields are still empty; the review and listing must commit together.
    """
    review = db.get(ListingLocationReview, listing.id)
    if new_submission and (review is not None or any(getattr(listing, key) is not None for key in ("latitude", "longitude", "maps_url"))):
        raise ValueError("New location evidence requires an empty public projection")
    current = review if review else listing
    def unchanged(key):
        previous = getattr(listing if key == "location" else current, key)
        if _same_location_value(key, changes[key], previous):
            return True
        # A normal exact-listing edit can round-trip the canonical public link
        # instead of the private evidence URL without invalidating its review.
        return key == "maps_url" and changes[key] is not None and review is not None and (
            review.review_status == "verified" and review.public_precision == "exact"
            and not projection_mismatch(listing, review) and changes[key] == listing.maps_url)
    if not any(key in changes and not unchanged(key) for key in LOCATION_FIELDS):
        if review is not None:
            # Unchanged private candidate values are not generic public writes.
            # Keep the already approved projection/precision exactly as stored.
            for key in ("latitude", "longitude", "maps_url"):
                changes.pop(key, None)
        return False
    candidate_latitude = changes.get("latitude", current.latitude)
    candidate_longitude = changes.get("longitude", current.longitude)
    if any(key in changes for key in ("latitude", "longitude")) and (
            (candidate_latitude is not None or candidate_longitude is not None) and not coordinate_pair(candidate_latitude, candidate_longitude)):
        raise HTTPException(422, "Provide a complete finite coordinate pair within latitude/longitude bounds")
    if changes.get("maps_url") and not safe_maps_url(changes["maps_url"]):
        raise HTTPException(422, "Use an allowlisted Google Maps HTTPS share without credentials")
    if review is None:
        review = ListingLocationReview(listing_id=listing.id, version=1, public_precision="area" if new_submission else "exact")
        db.add(review)
        previous = {"latitude": listing.latitude, "longitude": listing.longitude, "maps_url": listing.maps_url}
    else:
        review.version += 1
        previous = {"latitude": review.latitude, "longitude": review.longitude, "maps_url": review.maps_url}
    for key in ("latitude", "longitude", "maps_url"):
        value = changes.get(key, previous[key])
        setattr(review, key, Decimal(str(value)) if key in {"latitude", "longitude"} and value is not None else value)
    if not safe_maps_url(review.maps_url):
        review.maps_url = None
    if not coordinate_pair(review.latitude, review.longitude):
        review.latitude = review.longitude = None
    review.review_status = "unreviewed"
    review.source_type = "listing_submission" if new_submission else "listing_edit"
    review.source_reference = None
    review.review_note = ("New listing supplied location evidence; confirm the property position before publishing a point."
                          if new_submission else "Location fields changed; confirm the property position before publishing a point.")
    review.reviewed_at = review.reviewed_by = None
    review.updated_at = datetime.now(timezone.utc)
    changes.update(latitude=None, longitude=None, maps_url=None)
    db.flush()
    add_revision(db, review, actor_id, "listing_location_submitted" if new_submission else "listing_location_changed")
    return True
