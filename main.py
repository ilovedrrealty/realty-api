import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from config import settings
from database import SessionLocal
from utils.limiter import limiter

# Import all models so Base.metadata knows about them before create_all
import models  # noqa: F401

from routes import auth, listings, upgrade_requests, admin, leads, inquiries, saved_homes, bookings, chat, webhooks, stats, website_settings, professionals

logger = logging.getLogger(__name__)


async def _cleanup_pending_users() -> None:
    """Delete expired pending_users rows every hour to prevent unbounded growth."""
    from models.pending_user import PendingUser
    while True:
        await asyncio.sleep(3600)
        try:
            db = SessionLocal()
            deleted = db.query(PendingUser).filter(PendingUser.expires_at < datetime.now(timezone.utc)).delete()
            db.commit()
            db.close()
            if deleted:
                logger.info("Cleaned up %d expired pending_users rows", deleted)
        except Exception:
            logger.exception("pending_users cleanup failed")


async def _payout_cron() -> None:
    """
    Runs daily at 00:01 UTC.
    Releases payouts to owners the day after check-out for confirmed bookings
    where payment was captured and payout is still pending.
    """
    import services.paypal as paypal_svc
    from datetime import date, timedelta
    from models.booking import Booking
    from models.listing import Listing
    from models.user import User
    from utils.email import send_owner_payout_released_email, send_admin_payout_failed_email

    while True:
        # Sleep until next 00:01 UTC
        now = datetime.now(timezone.utc)
        tomorrow = now.replace(hour=0, minute=1, second=0, microsecond=0)
        if tomorrow <= now:
            tomorrow += timedelta(days=1)
        await asyncio.sleep((tomorrow - now).total_seconds())

        if not settings.paypal_client_id:
            continue

        db = SessionLocal()
        try:
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            due = (
                db.query(Booking, Listing)
                .join(Listing, Listing.id == Booking.listing_id)
                .filter(
                    Booking.check_out == yesterday,
                    Booking.payment_status == "captured",
                    Booking.payout_status == "pending",
                    Booking.status == "confirmed",
                )
                .all()
            )
            for booking, listing in due:
                # Resolve payout email: listing-level override → owner user → submitter user
                payout_email = listing.owner_paypal_email or None
                owner_user = None
                if not payout_email:
                    owner_id = listing.owner_id or listing.submitted_by
                    owner_user = db.query(User).filter(User.id == owner_id).first() if owner_id else None
                    payout_email = owner_user.paypal_email if owner_user else None
                if not payout_email:
                    logger.warning("Payout skipped: no PayPal email for listing %s (booking %s)", listing.id, booking.id)
                    continue
                total = float(booking.total_price) if booking.total_price else 0
                fee = round(total * settings.platform_fee_pct, 2)
                payout = round(total - fee, 2)
                try:
                    paypal_svc.create_payout(payout_email, payout, str(booking.id))
                    booking.platform_fee = fee
                    booking.payout_amount = payout
                    booking.payout_status = "paid"
                    logger.info("Payout released for booking %s → %s ($%.2f after $%.2f fee)", booking.id, payout_email, payout, fee)
                    if owner_user and owner_user.email:
                        try:
                            send_owner_payout_released_email(
                                to_email=owner_user.email,
                                owner_name=owner_user.display_name or owner_user.email,
                                listing_title=listing.title or "listing",
                                check_in=str(booking.check_in),
                                check_out=str(booking.check_out),
                                payout_amount=payout,
                            )
                        except Exception:
                            logger.exception("Failed to send payout released email for booking %s", booking.id)
                except Exception:
                    booking.payout_status = "failed"
                    logger.exception("Payout failed for booking %s", booking.id)
                    if settings.notify_email:
                        try:
                            send_admin_payout_failed_email(
                                to_email=settings.notify_email,
                                listing_title=listing.title or "listing",
                                booking_id=str(booking.id),
                                payout_email=payout_email,
                                payout_amount=payout,
                            )
                        except Exception:
                            logger.exception("Failed to send payout failure email for booking %s", booking.id)
            db.commit()
        except Exception:
            logger.exception("Payout cron error")
        finally:
            db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_task = asyncio.create_task(_cleanup_pending_users())
    payout_task = asyncio.create_task(_payout_cron())
    yield
    cleanup_task.cancel()
    payout_task.cancel()


app = FastAPI(title="I Love DR Realty API", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_cors_origins = settings.origins_list
if any(o.strip() in ("*", "") for o in _cors_origins):
    raise RuntimeError(
        "ALLOWED_ORIGINS must not contain '*' when allow_credentials=True. "
        "Set explicit origins in the ALLOWED_ORIGINS environment variable."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if settings.environment == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

app.include_router(auth.router)
app.include_router(listings.router)
app.include_router(upgrade_requests.router)
app.include_router(admin.router)
app.include_router(leads.router)
app.include_router(inquiries.router)
app.include_router(saved_homes.router)
app.include_router(bookings.router)
app.include_router(chat.router)
app.include_router(webhooks.router)
app.include_router(stats.router)
app.include_router(professionals.router)
app.include_router(website_settings.router)


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


@app.get("/health")
@app.post("/health")
@app.post("/")
def health():
    return {"status": "ok"}
