"""Operational record workflows, exact native-currency statements, no external I/O."""
import calendar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import or_
from models.listing import Listing
from models.property_management import PropertyManagementRecord as Record, PropertyManagementRevision as Revision


def today_dr():
    return datetime.now(timezone(timedelta(hours=-4))).date()


def listings_for(db, user):
    query = db.query(Listing)
    if user.role == "admin":
        return query
    if user.role == "owner":
        return query.filter(or_(Listing.owner_id == user.id, (Listing.owner_id.is_(None) & (Listing.submitted_by == user.id))))
    if user.role == "realtor":
        return query.filter(or_(Listing.assigned_realtor_id == user.id, Listing.owner_id == user.id,
                                Listing.assigned_realtor_id.is_(None) & Listing.owner_id.is_(None) & (Listing.submitted_by == user.id)))
    raise HTTPException(403, "Property management access required")


def property_for(db, user, listing_id, lock=False):
    query = listings_for(db, user).filter(Listing.id == listing_id)
    row = (query.with_for_update() if lock else query).first()
    if row is None:
        raise HTTPException(404, "Managed property not found")
    return row


def record_for(db, user, record_id, lock=False):
    # The property lock serializes records/approvals/lease-overlap checks and
    # prevents disclosure through guessed IDs. No role is selected by the client.
    listing_ids = listings_for(db, user).with_entities(Listing.id)
    row = db.query(Record).filter(Record.id == record_id, Record.listing_id.in_(listing_ids)).first()
    if row is None:
        raise HTTPException(404, "Management record not found")
    property_for(db, user, row.listing_id, lock=lock)
    if lock:
        row = db.query(Record).filter(Record.id == record_id).populate_existing().with_for_update().one()
    return row


def can_approve(user, listing):
    return user.role == "admin" or user.id == listing.owner_id or (user.role == "owner" and listing.owner_id is None and user.id == listing.submitted_by)


def view(row):
    return {
        "id": str(row.id), "listing_id": str(row.listing_id), "kind": row.kind,
        "title": row.title, "status": row.status, "data": row.data, "version": row.version,
        "due_date": row.due_date, "currency": row.currency,
        "amount": str(row.amount) if row.amount is not None else None,
        "approval_status": row.approval_status,
        "approval_limit": str(row.approval_limit) if row.approval_limit is not None else None,
        "approved_at": row.approved_at, "origin_record_id": str(row.origin_record_id) if row.origin_record_id else None,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


def audit(db, row, user, action, comment=""):
    from fastapi.encoders import jsonable_encoder
    db.add(Revision(record_id=row.id, version=row.version, action=action, actor_id=user.id,
                    snapshot={"record": jsonable_encoder(view(row)), "comment": comment}))


def _d(value):
    return date.fromisoformat(value) if isinstance(value, str) else value


def _money(value):
    return Decimal(str(value or "0"))


def validate_relations(db, row, body):
    if body.kind == "lease":
        if body.status in ("ended", "terminated") and not body.data.get("end_date"):
            raise HTTPException(422, "Record the lease end date")
        if body.status == "active":
            start = _d(body.data["start_date"])
            end = _d(body.data.get("end_date")) or date.max
            unit = body.data.get("unit_reference", "").casefold()
            others = db.query(Record).filter(Record.listing_id == body.listing_id, Record.kind == "lease", Record.status == "active")
            if row is not None:
                others = others.filter(Record.id != row.id)
            for other in others.limit(5001):
                data = other.data
                if data.get("unit_reference", "").casefold() == unit and _d(data["start_date"]) <= end and (_d(data.get("end_date")) or date.max) >= start:
                    raise HTTPException(409, "An active lease overlaps for this property/unit")
    if body.kind == "ledger":
        if body.status == "recorded" and _d(body.data["transaction_date"]) > today_dr():
            raise HTTPException(422, "Use Planned for future transactions")
        lease_id = body.data.get("lease_id")
        if lease_id:
            lease = db.query(Record).filter(Record.id == UUID(lease_id), Record.listing_id == body.listing_id, Record.kind == "lease").first()
            if lease is None:
                raise HTTPException(422, "Choose a lease belonging to this property")
            if body.data["currency"] != lease.data["currency"]:
                raise HTTPException(422, "Lease payments must use the lease currency")
        if body.data.get("rent_period") and (body.data["category"] != "rent" or not lease_id):
            raise HTTPException(422, "A rent period requires a linked rent entry")
    if body.origin_record_id:
        origin = db.query(Record).filter(Record.id == body.origin_record_id, Record.listing_id == body.listing_id).first()
        if origin is None or (row is not None and origin.id == row.id):
            raise HTTPException(422, "Choose a source record belonging to this property")
        if row is None and (body.kind != "maintenance" or origin.kind != "inspection"):
            raise HTTPException(422, "Only inspection follow-up work orders can set a source record")


def assign_fields(row, body):
    row.title, row.status, row.data = body.title, body.status, body.data
    data = body.data
    raw_date = data.get("transaction_date") if body.kind == "ledger" else data.get("end_date") if body.kind == "lease" else data.get("due_date")
    row.due_date = _d(raw_date) if raw_date else None
    row.currency = data.get("currency")
    amount = data.get("amount") if body.kind == "ledger" else data.get("rent") if body.kind == "lease" else data.get("estimated_cost")
    row.amount = _money(amount) if amount is not None else None


def maintenance_rules(row, body, user, listing, old=None, previous_title=None):
    if body.kind != "maintenance":
        return
    required = body.data["requires_approval"]
    privileged = can_approve(user, listing)
    old_required = old.get("requires_approval", True) if old else True
    if not privileged and not required and old_required:
        raise HTTPException(403, "Only the owner or Admin can waive approval")
    materially_changed = old is None or previous_title != body.title or any(old.get(k) != body.data.get(k) for k in ("currency", "vendor_name", "vendor_phone", "category", "notes", "requires_approval")) or _money(old.get("estimated_cost") if old else None) != _money(body.data.get("estimated_cost"))
    exceeds_limit = row.approval_limit is not None and _money(body.data.get("actual_cost")) > row.approval_limit
    if not required:
        row.approval_status, row.approval_limit = "not_required", None
        row.approved_by, row.approved_at = None, None
    elif materially_changed or exceeds_limit:
        row.approval_status, row.approval_limit = "pending", None
        row.approved_by, row.approved_at = None, None
        if old is not None and row.status in ("approved", "in_progress", "completed"):
            row.status = "awaiting_approval"
    if required and row.status in ("approved", "in_progress", "completed") and row.approval_status != "approved":
        raise HTTPException(409, "Owner approval is required before work proceeds")


def create(db, user, body):
    listing = property_for(db, user, body.listing_id, lock=True)
    if db.query(Record).filter(Record.listing_id == listing.id).count() >= 5000:
        raise HTTPException(409, "This property has reached its operational record limit")
    validate_relations(db, None, body)
    row = Record(listing_id=listing.id, kind=body.kind, version=1, created_by=user.id, updated_by=user.id,
                 approval_status="not_required", origin_record_id=body.origin_record_id)
    assign_fields(row, body)
    maintenance_rules(row, body, user, listing)
    if row.kind == "task":
        row.recurrence_anchor_day = row.due_date.day
    db.add(row); db.flush()
    audit(db, row, user, "created")
    db.commit(); db.refresh(row)
    return view(row)


def update(db, user, record_id, body):
    row = record_for(db, user, record_id, lock=True)
    if row.listing_id != body.listing_id or row.kind != body.kind or row.origin_record_id != body.origin_record_id:
        raise HTTPException(422, "A record cannot move to another property or type")
    if row.version != body.expected_version:
        raise HTTPException(409, "This record changed. Reload before saving")
    listing = property_for(db, user, row.listing_id)
    validate_relations(db, row, body)
    previous, previous_title = dict(row.data), row.title
    assign_fields(row, body)
    maintenance_rules(row, body, user, listing, previous, previous_title)
    if row.kind == "task" and previous.get("due_date") != row.data.get("due_date"):
        row.recurrence_anchor_day = row.due_date.day
    row.version += 1
    row.updated_by, row.updated_at = user.id, datetime.now(timezone.utc)
    audit(db, row, user, "updated")
    db.commit(); db.refresh(row)
    return view(row)


def approve(db, user, record_id, body):
    row = record_for(db, user, record_id, lock=True)
    listing = property_for(db, user, row.listing_id)
    if not can_approve(user, listing):
        raise HTTPException(403, "Owner or Admin approval required")
    if row.kind != "maintenance" or not row.data["requires_approval"] or row.status in ("completed", "cancelled"):
        raise HTTPException(409, "This work order does not await an approval decision")
    if row.version != body.expected_version:
        raise HTTPException(409, "This record changed. Reload before approving")
    minimum = max(_money(row.data.get("estimated_cost")), _money(row.data.get("actual_cost")))
    limit = body.limit if body.limit is not None else minimum
    if body.decision == "approved" and limit < minimum:
        raise HTTPException(422, "Approval must cover the recorded estimate and actual cost")
    row.approval_status = body.decision
    row.approval_limit = limit if body.decision == "approved" else None
    row.approved_by, row.approved_at = user.id, datetime.now(timezone.utc)
    row.status = "approved" if body.decision == "approved" else "awaiting_approval"
    row.version += 1; row.updated_by = user.id; row.updated_at = datetime.now(timezone.utc)
    audit(db, row, user, "approval_" + body.decision, body.comment)
    db.commit(); db.refresh(row)
    return view(row)


def add_months(value, months, anchor):
    month = value.year * 12 + value.month - 1 + months
    year, index = divmod(month, 12)
    return date(year, index + 1, min(anchor, calendar.monthrange(year, index + 1)[1]))


def schedule_next(db, user, record_id, body):
    row = record_for(db, user, record_id, lock=True)
    if row.version != body.expected_version:
        raise HTTPException(409, "This record changed. Reload before scheduling")
    if row.kind != "task" or row.status != "completed" or row.data["recurrence"] == "none":
        raise HTTPException(409, "Complete a recurring task before scheduling its next occurrence")
    recurrence = row.data["recurrence"]
    try:
        next_date = row.due_date + timedelta(days=7) if recurrence == "weekly" else add_months(row.due_date, {"monthly": 1, "quarterly": 3, "yearly": 12}[recurrence], row.recurrence_anchor_day or row.due_date.day)
    except (ValueError, OverflowError):
        raise HTTPException(422, "The next occurrence exceeds the supported date range")
    # A manually rescheduled child remains this occurrence's child. Repeating
    # the parent action must return it rather than create another due-date copy.
    existing = db.query(Record).filter(Record.origin_record_id == row.id, Record.kind == "task").first()
    if existing is not None:
        return view(existing)
    if db.query(Record).filter(Record.listing_id == row.listing_id).count() >= 5000:
        raise HTTPException(409, "This property has reached its operational record limit")
    data = {**row.data, "due_date": next_date.isoformat()}
    next_row = Record(listing_id=row.listing_id, kind="task", title=row.title, status="open", data=data,
                      due_date=next_date, origin_record_id=row.id, recurrence_anchor_day=row.recurrence_anchor_day,
                      version=1, approval_status="not_required", created_by=user.id, updated_by=user.id)
    db.add(next_row); db.flush(); audit(db, next_row, user, "recurring_task_created")
    db.commit(); db.refresh(next_row)
    return view(next_row)


def statement(db, user, listing_id, start, end, as_of=None):
    listing = property_for(db, user, listing_id)
    as_of = as_of or today_dr()
    if end < start or (end - start).days > 366:
        raise HTTPException(422, "Choose a statement period of at most 366 days")
    rows = db.query(Record).filter(Record.listing_id == listing.id).order_by(Record.created_at, Record.id).limit(5001).all()
    if len(rows) > 5000:
        raise HTTPException(409, "Narrow the property record set before generating a statement")
    counts = {kind: sum(r.kind == kind for r in rows) for kind in ("lease", "maintenance", "inspection", "ledger", "task")}
    totals, entries, paid = {}, [], {}
    for row in rows:
        if row.kind != "ledger" or row.status != "recorded":
            continue
        data = row.data; amount = _money(data["amount"])
        sign = Decimal("1") if data["direction"] == "income" else Decimal("-1")
        tx_date = _d(data["transaction_date"])
        def currency_bucket():
            return totals.setdefault(data["currency"], {"income": Decimal("0"), "expenses": Decimal("0"), "net": Decimal("0"), "deposit_cash": Decimal("0"), "deposit_balance": Decimal("0")})
        if data["category"] == "deposit" and tx_date <= min(end, as_of):
            currency_bucket()["deposit_balance"] += sign * amount
        if data.get("lease_id") and data["category"] == "rent" and tx_date <= as_of:
            period = _d(data.get("rent_period") or data["transaction_date"]).replace(day=1).isoformat()
            key = (data["lease_id"], period)
            paid[key] = paid.get(key, Decimal("0")) + sign * amount
        if not start <= tx_date <= end:
            continue
        entries.append(view(row))
        bucket = currency_bucket()
        if data["category"] == "deposit":
            bucket["deposit_cash"] += sign * amount
        else:
            bucket["income" if sign > 0 else "expenses"] += amount
            bucket["net"] += sign * amount
    rent_due = []
    for row in rows:
        if row.kind != "lease" or row.status == "draft":
            continue
        data = row.data; lease_start = _d(data["start_date"])
        lease_end = _d(data.get("end_date")) or date.max
        first = _d(data.get("first_due_date")) or lease_start
        month = start.replace(day=1)
        while month <= end:
            due = date(month.year, month.month, min(data["due_day"], calendar.monthrange(month.year, month.month)[1]))
            initial_month = first.replace(day=1) == month
            if initial_month:
                due = first
            if first <= due <= lease_end and start <= due <= end:
                expected = _money(data.get("first_rent")) if initial_month and data.get("first_rent") is not None else _money(data["rent"])
                received = paid.get((str(row.id), month.isoformat()), Decimal("0"))
                rent_due.append({"lease_id": str(row.id), "title": row.title, "due_date": due, "currency": data["currency"],
                                 "expected": str(expected), "recorded_received": str(received),
                                 "outstanding": str(max(Decimal("0"), expected - received)), "overdue": due < as_of and expected > received})
            if month.year == end.year and month.month == end.month:
                break
            month = add_months(month, 1, 1)
    open_work = [view(r) for r in rows if r.kind == "maintenance" and r.status not in ("completed", "cancelled")]
    due_items = [view(r) for r in rows if r.kind in ("task", "inspection") and r.status not in ("completed", "cancelled") and r.due_date and r.due_date <= as_of + timedelta(days=30)]
    renewals = [view(r) for r in rows if r.kind == "lease" and r.status == "active" and r.due_date and r.due_date <= as_of + timedelta(days=60)]
    entries.sort(key=lambda r: (r["due_date"], r["id"]))
    return {"property": {"id": str(listing.id), "title": listing.title, "location": listing.location},
            "period": {"start": start, "end": end, "as_of": as_of, "timezone": "America/Santo_Domingo"},
            "counts": counts, "totals": {c: {k: str(v) for k, v in bucket.items()} for c, bucket in totals.items()},
            "entries": entries, "rent_due": rent_due, "open_work_orders": open_work, "due_items": due_items, "renewals": renewals,
            "can_approve": can_approve(user, listing),
            "note": "Manual operational records; currencies remain separate. Refundable deposits are excluded from operating income. No payments are collected or bank balances reconciled by this workspace."}
