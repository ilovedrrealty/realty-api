"""One read-only PostgreSQL statement gives each report a single MVCC snapshot.

Only counts leave the server. In particular, this report does not use booking
amounts as revenue: order creation dates are not capture dates, and the booking
table is not an accounting ledger with a captured currency/date for each event.
"""
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from schemas.analytics import AdminAnalyticsResponse


ANALYTICS_SQL = text("""
WITH inquiry_window AS (
    SELECT created_at, type, status, contacted_at, ghl_contact_id, ghl_sync_error,
           COALESCE(tracking @> jsonb_build_object('internal_test', true), false) AS internal_test,
           COALESCE(jsonb_typeof(tracking->'internal_test') = 'boolean', false) AS tracking_known
    FROM leads
    WHERE created_at >= :start_at AND created_at < :end_at
), inquiries AS (
    SELECT * FROM inquiry_window WHERE NOT internal_test
), booking_window AS (
    SELECT created_at, status FROM bookings
    WHERE created_at >= :start_at AND created_at < :end_at
), user_daily AS (
    SELECT (created_at AT TIME ZONE 'UTC')::date AS day, count(*) AS n
    FROM users WHERE created_at >= :start_at AND created_at < :end_at
    GROUP BY 1
), inquiry_daily AS (
    SELECT (created_at AT TIME ZONE 'UTC')::date AS day, count(*) AS n
    FROM inquiries GROUP BY 1
), booking_daily AS (
    SELECT (created_at AT TIME ZONE 'UTC')::date AS day, count(*) AS n
    FROM booking_window GROUP BY 1
), days AS (
    SELECT CAST(:first_day AS date) + series.i AS day
    FROM generate_series(0, CAST(:days AS integer) - 1) AS series(i)
), account_snapshot AS (
    SELECT count(*) AS total,
           count(*) FILTER (WHERE status = 'active') AS active,
           count(*) FILTER (WHERE created_at >= :start_at AND created_at < :end_at) AS created,
           jsonb_build_object(
               'buyer', count(*) FILTER (WHERE role = 'buyer'),
               'owner', count(*) FILTER (WHERE role = 'owner'),
               'realtor', count(*) FILTER (WHERE role = 'realtor'),
               'admin', count(*) FILTER (WHERE role = 'admin'),
               'other', count(*) FILTER (WHERE role NOT IN ('buyer','owner','realtor','admin') OR role IS NULL)
           ) AS roles
    FROM users
), listing_snapshot AS (
    SELECT count(*) AS total,
           count(*) FILTER (WHERE status = 'active') AS active,
           count(*) FILTER (WHERE created_at >= :start_at AND created_at < :end_at) AS created,
           jsonb_build_object(
               'active', count(*) FILTER (WHERE status = 'active'),
               'pending_approval', count(*) FILTER (WHERE status = 'pending_approval'),
               'draft', count(*) FILTER (WHERE status = 'draft'),
               'rejected', count(*) FILTER (WHERE status = 'rejected'),
               'archived', count(*) FILTER (WHERE status = 'archived'),
               'other', count(*) FILTER (WHERE status NOT IN ('active','pending_approval','draft','rejected','archived') OR status IS NULL)
           ) AS statuses,
           jsonb_build_object(
               'sale', count(*) FILTER (WHERE status = 'active' AND transaction = 'sale'),
               'rent', count(*) FILTER (WHERE status = 'active' AND transaction = 'rent'),
               'both', count(*) FILTER (WHERE status = 'active' AND transaction = 'both'),
               'other', count(*) FILTER (WHERE status = 'active' AND (transaction NOT IN ('sale','rent','both') OR transaction IS NULL))
           ) AS purposes,
           jsonb_build_object(
               'villa', count(*) FILTER (WHERE status = 'active' AND type = 'villa'),
               'apartment', count(*) FILTER (WHERE status = 'active' AND type = 'apartment'),
               'condo', count(*) FILTER (WHERE status = 'active' AND type = 'condo'),
               'land', count(*) FILTER (WHERE status = 'active' AND type = 'land'),
               'commercial', count(*) FILTER (WHERE status = 'active' AND type = 'commercial'),
               'other', count(*) FILTER (WHERE status = 'active' AND (type NOT IN ('villa','apartment','condo','land','commercial') OR type IS NULL))
           ) AS types
    FROM listings
), inquiry_summary AS (
    SELECT count(*) AS received,
           count(*) FILTER (WHERE NOT tracking_known) AS unknown_tracking,
           count(*) FILTER (WHERE contacted_at >= created_at AND contacted_at < :end_at) AS contact_recorded,
           count(*) FILTER (WHERE ghl_contact_id IS NOT NULL AND btrim(ghl_contact_id) <> '') AS crm_linked,
           count(*) FILTER (WHERE ghl_sync_error IS NOT NULL AND btrim(ghl_sync_error) <> '') AS crm_errors,
           jsonb_build_object(
               'buyer_interest', count(*) FILTER (WHERE type = 'buyer_interest'),
               'seller_interest', count(*) FILTER (WHERE type = 'seller_interest'),
               'rental_interest', count(*) FILTER (WHERE type = 'rental_interest'),
               'property_inquiry', count(*) FILTER (WHERE type = 'property_inquiry'),
               'booking', count(*) FILTER (WHERE type = 'booking'),
               'other', count(*) FILTER (WHERE type NOT IN ('buyer_interest','seller_interest','rental_interest','property_inquiry','booking') OR type IS NULL)
           ) AS types,
           jsonb_build_object(
               'new', count(*) FILTER (WHERE status = 'new'),
               'assigned', count(*) FILTER (WHERE status = 'assigned'),
               'contacted', count(*) FILTER (WHERE status = 'contacted'),
               'closed', count(*) FILTER (WHERE status = 'closed'),
               'other', count(*) FILTER (WHERE status NOT IN ('new','assigned','contacted','closed') OR status IS NULL)
           ) AS statuses
    FROM inquiries
), booking_summary AS (
    SELECT count(*) AS total,
           jsonb_build_object(
               'pending', count(*) FILTER (WHERE status = 'pending'),
               'confirmed', count(*) FILTER (WHERE status = 'confirmed'),
               'cancelled', count(*) FILTER (WHERE status = 'cancelled'),
               'other', count(*) FILTER (WHERE status NOT IN ('pending','confirmed','cancelled') OR status IS NULL)
           ) AS statuses
    FROM booking_window
)
SELECT jsonb_build_object(
    'accounts', a.total, 'active_accounts', a.active, 'accounts_by_role', a.roles,
    'listing_records', l.total, 'active_listings', l.active,
    'listings_by_status', l.statuses, 'active_listings_by_purpose', l.purposes,
    'active_listings_by_type', l.types
) AS snapshot,
jsonb_build_object(
    'accounts_created', a.created, 'listings_created', l.created,
    'inquiries', jsonb_build_object(
        'received', q.received,
        'excluded_internal_tests', (SELECT count(*) FROM inquiry_window WHERE internal_test),
        'unknown_tracking', q.unknown_tracking, 'contact_recorded', q.contact_recorded,
        'crm_linked', q.crm_linked, 'crm_errors', q.crm_errors,
        'by_type', q.types, 'current_status', q.statuses
    ),
    'booking_records_created', b.total, 'booking_current_status', b.statuses
) AS period,
(SELECT jsonb_agg(jsonb_build_object(
    'date', d.day, 'accounts_created', COALESCE(u.n, 0),
    'inquiries_received', COALESCE(i.n, 0),
    'booking_records_created', COALESCE(k.n, 0)
) ORDER BY d.day)
FROM days d
LEFT JOIN user_daily u ON u.day = d.day
LEFT JOIN inquiry_daily i ON i.day = d.day
LEFT JOIN booking_daily k ON k.day = d.day) AS daily
FROM account_snapshot a CROSS JOIN listing_snapshot l
CROSS JOIN inquiry_summary q CROSS JOIN booking_summary b
""")


def build_admin_analytics(db: Session, days: int = 30, *, now: datetime | None = None) -> AdminAnalyticsResponse:
    if days not in (7, 30, 90):
        raise ValueError("Unsupported analytics window")
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("Analytics clock must include a timezone")
    observed_at = observed_at.astimezone(timezone.utc)
    first_day = observed_at.date() - timedelta(days=days - 1)
    start_at = datetime.combine(first_day, time.min, tzinfo=timezone.utc)
    row = db.execute(ANALYTICS_SQL, {
        "days": days, "first_day": first_day,
        "start_at": start_at, "end_at": observed_at,
    }).mappings().one()
    return AdminAnalyticsResponse(
        generated_at=observed_at,
        window={"days": days, "start_at": start_at, "end_at": observed_at, "timezone": "UTC"},
        snapshot=row["snapshot"], period=row["period"], daily=row["daily"],
    )
