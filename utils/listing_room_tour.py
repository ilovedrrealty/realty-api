"""Bind room tour labels to one exact, already approved public listing gallery."""
import hashlib
import json
import re

from fastapi import HTTPException
from config import settings
from models.listing_edit import ListingEdit
from models.user import User
from schemas.listing_room_tour import RoomPhotoTour


FIELD = "room_photo_tour"
SUPABASE_ORIGIN = re.compile(r"https://[a-z0-9][a-z0-9-]*\.supabase\.co")
OBJECT_PATH = re.compile(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.(?:jpe?g|png|webp|heic)", re.IGNORECASE | re.ASCII)
NON_LISTING_PREFIXES = {"avatars", "documents", "professionals", "share", "projects", "private"}


def gallery_digest(images):
    encoded = json.dumps(images or [], ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def public_listing_image(url):
    origin = settings.supabase_url.rstrip("/")
    bucket = settings.storage_bucket
    if not isinstance(url, str) or SUPABASE_ORIGIN.fullmatch(origin) is None or bucket != "listing-images":
        return False
    prefix = f"{origin}/storage/v1/object/public/{bucket}/"
    if not url.startswith(prefix):
        return False
    # storage3 0.7.7 adds a bare delimiter even with no URL options.
    path = url[len(prefix):].removesuffix("?")
    return (OBJECT_PATH.fullmatch(path) is not None and path.split("/", 1)[0].lower() not in NON_LISTING_PREFIXES)


def eligible_gallery_images(images):
    return [url for url in images or () if public_listing_image(url)]


def validated_tour(value, images):
    tour = RoomPhotoTour.model_validate(value)
    gallery = set(images or ())
    if any(photo.url not in gallery or not public_listing_image(photo.url) for group in tour.groups for photo in group.photos):
        raise ValueError("Every tour photo must be an approved public listing image")
    return tour


def public_room_tour(listing):
    snapshot = getattr(listing, "room_photo_tour_snapshot", None)
    if (listing.status != "active" or not isinstance(snapshot, dict)
            or snapshot.get("gallery_sha256") != gallery_digest(listing.images)):
        return None
    try:
        return validated_tour(snapshot.get("tour"), listing.images)
    except ValueError:
        return None


def changed_gallery(listing, changes):
    return "images" in changes and gallery_digest(changes["images"]) != gallery_digest(listing.images)


def checked_tour_edit(listing, value):
    if value is None:
        return None
    try:
        tour = validated_tour(value, listing.images)
    except ValueError:
        raise HTTPException(422, "Choose unique photos from this listing's approved public gallery",
                            headers={"Cache-Control": "no-store"}) from None
    return {"gallery_sha256": gallery_digest(listing.images), "tour": tour.model_dump(mode="json")}


def is_tour_edit(edit):
    return set(edit.proposed_data or ()) == {FIELD}


def pending_edits(db, listing_id):
    return db.query(ListingEdit).filter(ListingEdit.listing_id == listing_id, ListingEdit.status == "pending")


def invalidate_staged_tours(db, listing_id, author_id=None):
    query = pending_edits(db, listing_id).filter(ListingEdit.room_photo_tour_base_gallery_sha256.is_not(None))
    if author_id is not None:
        query = query.filter(ListingEdit.submitted_by == author_id)
    query.update({ListingEdit.room_photo_tour_base_gallery_sha256: None}, synchronize_session=False)


def invalidate_room_tour(db, listing):
    listing.room_photo_tour_snapshot = None
    invalidate_staged_tours(db, listing.id)


def lock_tour_author(db, user_id):
    return db.query(User).filter(User.id == user_id).populate_existing().with_for_update(key_share=True).first()


def can_edit_room_tour(listing, actor, *, allow_admin=False):
    if actor is None or actor.status != "active" or actor.email_verified is not True:
        return False
    if actor.role == "admin":
        return allow_admin
    return actor.role in {"owner", "realtor"} and actor.id in {listing.submitted_by, listing.assigned_realtor_id}
