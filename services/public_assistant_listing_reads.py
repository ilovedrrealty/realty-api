"""Fresh anonymous public reads for a future assistant, with no model activation.

Reuse the actual public handlers, not their query logic. Only minimized facts
leave this module. All DB/session/handler choices are server-owned, never tool
arguments. These synchronous tools must not run on an async event-loop thread.
Statement and lock bounds are not a claimed absolute orchestration deadline.
"""
from datetime import datetime, timezone
from uuid import UUID

from schemas.listing import ListingFilterOptionsResponse
from schemas.public_assistant_listing import AssistantListingDetail, AssistantListingPage
from services.public_assistant_listing import (
    PublicListingBoundaryError, parse_detail_input, parse_search_input,
    prepare_search, project_detail, project_search,
)


class PublicListingReadError(PublicListingBoundaryError):
    """Finite unavailable/not-public result; no DB or source error is exposed."""


def _new_session():
    # Lazy imports keep the pure input/facts boundary independent of app setup.
    from database import SessionLocal
    return SessionLocal()


def _handlers():
    from routes.listings import get_active_listings, get_listing, get_listing_filter_options
    return get_active_listings, get_listing, get_listing_filter_options


def _run_read(operation):
    from fastapi import HTTPException
    from sqlalchemy import text

    db = None
    result = None
    failure = None
    try:
        db = _new_session()
        if (db.get_bind().dialect.name != "postgresql" or db.in_transaction()
                or db.new or db.dirty or db.deleted):
            raise PublicListingReadError("fresh_public_read_session_required")
        # Applied before any data query, for this transaction only. Existing
        # SessionLocal uses the established bounded pooled connection policy.
        db.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"))
        db.execute(text("SET LOCAL statement_timeout = '3s'"))
        db.execute(text("SET LOCAL lock_timeout = '1s'"))
        with db.no_autoflush:
            result = operation(db)
    except PublicListingBoundaryError as exc:
        failure = exc
    except HTTPException as exc:
        failure = PublicListingReadError("listing_not_available" if exc.status_code == 404 else "public_read_unavailable")
    except Exception:
        failure = PublicListingReadError("public_read_unavailable")
    finally:
        if db is not None:
            for cleanup in (db.rollback, db.close):
                try:
                    cleanup()
                except Exception:
                    if failure is None:
                        failure = PublicListingReadError("public_read_unavailable")
    if failure is not None:
        raise failure from None
    return result


def search_public_listings(raw: str | bytes) -> AssistantListingPage:
    """Read current active inventory using the existing public search contract."""
    request = parse_search_input(raw)

    def read(db):
        from fastapi import Response
        search_handler, _, options_handler = _handlers()
        options = ListingFilterOptionsResponse.model_validate(options_handler(db=db))
        # Validate supplied labels against actual current active options, not a
        # caller/model-provided catalog. No option or response cache is retained.
        prepare_search(request, options)
        response = Response()
        page = search_handler(
            response=response, page=request.page, page_size=request.page_size,
            purpose=request.purpose, type=None, region=None,
            locations=list(request.locations) or None, types=list(request.types) or None,
            price_currency=request.price_currency, usd_to_dop=request.usd_to_dop,
            rental_period=request.rental_period, min_price=request.min_price,
            max_price=request.max_price, beds=request.beds, min_roi=None,
            features=None, sort=request.sort, exclude_ids=None,
            include_aggregates=False, shape=None, db=db,
        )
        if response.headers.get("cache-control") != "no-store":
            raise PublicListingReadError("public_no_store_required")
        return project_search(request, page, projected_at=datetime.now(timezone.utc))

    return _run_read(read)


def read_public_listing(raw: str | bytes) -> AssistantListingDetail:
    """Recheck the exact listing's current public availability on every call."""
    request = parse_detail_input(raw)

    def read(db):
        from fastapi import Response
        _, detail_handler, _ = _handlers()
        response = Response()
        listing = detail_handler(listing_id=UUID(request.listing_id), response=response, db=db)
        if response.headers.get("cache-control") != "no-store":
            raise PublicListingReadError("public_no_store_required")
        return project_detail(request, listing, projected_at=datetime.now(timezone.utc))

    return _run_read(read)
