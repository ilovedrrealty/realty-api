import csv
import io
import ipaddress
import logging
import socket
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional
from urllib.parse import parse_qsl, unquote, urljoin, urlparse

import filetype
import httpx
from sqlalchemy.orm import Session

from models.listing import Listing
from models.listing_event import ListingEvent
from models.user import User
from models.bulk_import_job import BulkImportJob
from utils.storage import upload_image
from utils.listing_constants import ALL_REGIONS, FEATURES, BASE_TAGS
from utils.location_quality import stage_location_change

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = [
    "title", "property_type", "purpose", "region", "currency", "price",
    "description_en", "co_listing_enabled", "assigned_agent_id",
]

VALID_TYPES = {"villa", "apartment", "condo", "land", "commercial"}
VALID_PURPOSES = {"sale", "rent", "both"}
VALID_CURRENCIES = {"USD", "DOP"}

MAX_PHOTO_SIZE = 20 * 1024 * 1024
MAX_PHOTOS_PER_LISTING = 25
ALLOWED_PHOTO_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic"}


def parse_csv(raw: bytes) -> list[dict]:
    """Parses and structurally validates the uploaded CSV. Raises ValueError
    (caught by the route as a 400) for problems that should block the whole
    import before anything is written to the DB."""
    text = None
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("CSV file must be UTF-8 or Windows-1252 (Excel \"CSV\") encoded")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV file has no header row")

    missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
    if missing:
        raise ValueError(f"Missing required column(s): {', '.join(missing)}")

    rows = list(reader)
    if not rows:
        raise ValueError("CSV file has no data rows")

    seen_refs: set[str] = set()
    for i, row in enumerate(rows, start=2):
        ref = (row.get("source_ref") or "").strip()
        if ref:
            if ref in seen_refs:
                raise ValueError(f"Duplicate source_ref '{ref}' within the file (row {i})")
            seen_refs.add(ref)

    return rows


def _split_pipe(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split("|") if v.strip()]


def _parse_decimal(value: Optional[str]) -> Optional[Decimal]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _parse_int(value: Optional[str]) -> Optional[int]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _parse_location_fields(row: dict) -> tuple[Optional[str], Optional[Decimal], Optional[Decimal], list[str]]:
    """Preserve supplied location fields only; never fetch or geocode a link.

    A Maps link may identify a business, search or viewport, so it is not
    evidence of the property's exact coordinates. Only an explicit CSV pair
    can populate latitude/longitude.
    """
    errors = []
    maps_url = (row.get("maps_url") or "").strip() or None
    if maps_url:
        valid_url = False
        try:
            parsed = urlparse(maps_url)
            host = parsed.hostname
            decoded_path = unquote(parsed.path).lower()
            credential_keys = {"key", "api_key", "access_token", "token"}
            valid_base = (parsed.scheme == "https" and host and len(maps_url) <= 2048
                          and parsed.username is None and parsed.password is None
                          and parsed.port in (None, 443) and "\\" not in maps_url
                          and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in maps_url)
                          and not any(part in {".", ".."} for part in decoded_path.split("/"))
                          and decoded_path != "/maps/api" and not decoded_path.startswith("/maps/api/")
                          and not any(key.strip().lower() in credential_keys for key, _ in parse_qsl(parsed.query, keep_blank_values=True)))
            if valid_base:
                if host in {"google.com", "www.google.com"}:
                    valid_url = parsed.path == "/maps" or parsed.path.startswith("/maps/")
                elif host == "maps.google.com":
                    valid_url = parsed.path in {"", "/", "/maps"} or parsed.path.startswith("/maps/")
                elif host == "maps.app.goo.gl":
                    valid_url = bool(parsed.path.strip("/"))
                elif host == "goo.gl":
                    valid_url = parsed.path.startswith("/maps/") and bool(parsed.path[len("/maps/"):].strip("/"))
        except ValueError:
            pass
        if not valid_url:
            errors.append("maps_url must be a Google Maps HTTPS share link without API credentials (google.com/maps, maps.google.com, maps.app.goo.gl or goo.gl/maps)")

    values = {key: (row.get(key) or "").strip() for key in ("latitude", "longitude")}
    coordinates: dict[str, Optional[Decimal]] = {"latitude": None, "longitude": None}
    if bool(values["latitude"]) != bool(values["longitude"]):
        errors.append("latitude and longitude must be provided together, or both left blank")
    elif values["latitude"]:
        for key, bound in (("latitude", 90), ("longitude", 180)):
            try:
                coordinate = Decimal(values[key])
                if not coordinate.is_finite() or not -bound <= coordinate <= bound:
                    raise ValueError()
                coordinates[key] = coordinate
            except (InvalidOperation, ValueError):
                errors.append(f"{key} must be a finite number between {-bound} and {bound}")
    return maps_url, coordinates["latitude"], coordinates["longitude"], errors


MAX_PHOTO_REDIRECTS = 3


def _is_safe_photo_url(url: str) -> bool:
    """Rejects anything that isn't a plain http(s) URL resolving to a public
    address, so a CSV's photo_urls column can't be used to make the server
    fetch internal services (cloud metadata endpoints, internal admin ports,
    loopback, etc.)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        addr_infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return False
    for info in addr_infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_unspecified or ip.is_reserved
        ):
            return False
    return True


def _download_photo(url: str) -> Optional[tuple[bytes, str]]:
    for _ in range(MAX_PHOTO_REDIRECTS + 1):
        if not _is_safe_photo_url(url):
            logger.warning("Bulk import: rejected photo URL %s", url)
            return None
        try:
            resp = httpx.get(url, timeout=20.0, follow_redirects=False)
        except Exception:
            logger.warning("Bulk import: failed to download photo %s", url)
            return None
        if resp.is_redirect:
            location = resp.headers.get("location")
            if not location:
                return None
            url = urljoin(url, location)
            continue
        try:
            resp.raise_for_status()
        except Exception:
            logger.warning("Bulk import: failed to download photo %s", url)
            return None
        data = resp.content
        if len(data) > MAX_PHOTO_SIZE:
            return None
        detected = filetype.guess(data[:2048])
        if detected is None or detected.mime not in ALLOWED_PHOTO_TYPES:
            return None
        return data, detected.mime
    return None


def _import_row(db: Session, row: dict, admin_user_id) -> dict:
    source_ref = (row.get("source_ref") or "").strip() or None

    if source_ref:
        existing = db.query(Listing).filter(Listing.source_ref == source_ref).first()
        if existing:
            return {"source_ref": source_ref, "status": "skipped", "message": "source_ref already imported"}

    title = (row.get("title") or "").strip()
    property_type = (row.get("property_type") or "").strip().lower()
    purpose = (row.get("purpose") or "").strip().lower()
    region = (row.get("region") or "").strip()
    currency = (row.get("currency") or "USD").strip().upper()
    price = _parse_decimal(row.get("price"))
    agent_code_raw = (row.get("assigned_agent_id") or "").strip()

    maps_url, latitude, longitude, errors = _parse_location_fields(row)
    if not title:
        errors.append("title is required")
    if property_type not in VALID_TYPES:
        errors.append(f"property_type '{property_type}' must be one of {sorted(VALID_TYPES)}")
    if purpose not in VALID_PURPOSES:
        errors.append(f"purpose '{purpose}' must be one of {sorted(VALID_PURPOSES)}")
    if not region:
        errors.append("region is required")
    if price is None:
        errors.append("price is required and must be numeric")
    if currency not in VALID_CURRENCIES:
        errors.append(f"currency '{currency}' must be USD or DOP")

    realtor = None
    if not agent_code_raw:
        errors.append("assigned_agent_id is required")
    else:
        try:
            agent_code = int(agent_code_raw)
            realtor = db.query(User).filter(User.user_code == agent_code, User.role.in_(["realtor", "admin"])).first()
        except ValueError:
            realtor = None
        if realtor is None:
            errors.append(f"assigned_agent_id '{agent_code_raw}' does not match a realtor/admin user")

    if errors:
        return {"source_ref": source_ref, "status": "failed", "message": "; ".join(errors)}

    warnings = []
    if region not in ALL_REGIONS:
        warnings.append(f"region '{region}' is not in the known region list")

    features = _split_pipe(row.get("features"))
    unknown_features = [f for f in features if f not in FEATURES]
    if unknown_features:
        warnings.append(f"unrecognized feature(s): {', '.join(unknown_features)}")

    tags = _split_pipe(row.get("listing_tags"))
    unknown_tags = [t for t in tags if t not in BASE_TAGS]
    if unknown_tags:
        warnings.append(f"unrecognized tag(s): {', '.join(unknown_tags)}")

    co_listing_enabled = (row.get("co_listing_enabled") or "").strip().lower() in ("yes", "true", "1")

    images: list[str] = []
    for url in _split_pipe(row.get("photo_urls"))[:MAX_PHOTOS_PER_LISTING]:
        downloaded = _download_photo(url)
        if downloaded is None:
            warnings.append(f"photo failed to download: {url}")
            continue
        data, mime = downloaded
        try:
            images.append(upload_image(data, mime, str(admin_user_id)))
        except Exception:
            logger.exception("Bulk import: failed to upload photo for source_ref %s", source_ref)
            warnings.append(f"photo failed to upload: {url}")

    external_agent_email = (row.get("external_agent_email") or "").strip() or None

    listing = Listing(
        title=title,
        description=(row.get("description_en") or "").strip() or None,
        description_es=(row.get("description_es") or "").strip() or None,
        type=property_type,
        transaction=purpose,
        price=price,
        location=region,
        bedrooms=_parse_int(row.get("bedrooms")),
        bathrooms=_parse_decimal(row.get("bathrooms")),
        area_sqft=_parse_int(row.get("living_area_sqft")),
        lot_size_sqft=_parse_int(row.get("lot_size_sqft")),
        features=features,
        tags=tags,
        images=images,
        status="active",
        submitted_by=admin_user_id,
        assigned_realtor_id=realtor.id,
        co_listing_enabled=co_listing_enabled,
        co_listing_brokerage=(row.get("external_brokerage") or "").strip() or None,
        co_listing_agent_name=(row.get("external_agent_name") or "").strip() or None,
        # co_listing_agent_contact is the field the dashboard actually displays
        # ("Agent Contact"); co_listing_agent_email is kept in sync alongside it
        # for anything that later wants the email specifically.
        co_listing_agent_contact=external_agent_email,
        co_listing_agent_email=external_agent_email,
        co_listing_commission_split=_parse_decimal(row.get("commission_split_pct")),
        co_listing_notes=(row.get("co_listing_notes") or "").strip() or None,
        co_listing_status=(row.get("co_listing_status") or "").strip() or None,
        source_ref=source_ref,
        currency=currency,
    )
    db.add(listing)
    db.flush()
    stage_location_change(db, listing, {"maps_url": maps_url, "latitude": latitude, "longitude": longitude},
                          admin_user_id, new_submission=True)
    db.add(ListingEvent(listing_id=listing.id, event_type="submitted", actor_id=admin_user_id, note="Bulk CSV import"))

    return {
        "source_ref": source_ref,
        "status": "succeeded",
        "listing_id": str(listing.id),
        "message": "; ".join(warnings) if warnings else None,
    }


def run_bulk_import(job_id, rows: list[dict], admin_user_id, session_factory) -> None:
    """Executed via FastAPI BackgroundTasks after the request has already
    returned, so it opens its own DB session rather than reusing the request's
    (which is closed by then)."""
    db: Session = session_factory()
    try:
        job = db.query(BulkImportJob).filter(BulkImportJob.id == job_id).first()
        if not job:
            return
        job.status = "running"
        db.commit()

        results = []
        for i, row in enumerate(rows, start=2):
            try:
                outcome = _import_row(db, row, admin_user_id)
                db.commit()
            except Exception:
                logger.exception("Bulk import: row %s crashed", i)
                db.rollback()
                outcome = {"source_ref": (row.get("source_ref") or "").strip() or None, "status": "failed", "message": "Unexpected server error"}
            outcome["row"] = i
            results.append(outcome)

            job.processed_rows += 1
            if outcome["status"] == "succeeded":
                job.succeeded_count += 1
            elif outcome["status"] == "skipped":
                job.skipped_count += 1
            else:
                job.failed_count += 1
            job.results = results
            db.commit()

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        logger.exception("Bulk import job %s failed", job_id)
        db.rollback()
        job = db.query(BulkImportJob).filter(BulkImportJob.id == job_id).first()
        if job:
            job.status = "failed"
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
