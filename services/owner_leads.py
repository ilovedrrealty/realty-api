"""One read-only snapshot of current property inquiry scope and pagination."""
from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.orm import aliased
from models.lead import Lead
from models.listing import Listing
from models.user import User

OPEN_STATUSES = ("new", "assigned", "schedule", "contacted")
KNOWN_STATUSES = (*OPEN_STATUSES, "closed")
PUBLIC_FIELDS = ("id", "property_id", "property_title", "name", "email", "phone", "message", "status",
                 "created_at", "assigned_at", "contacted_at", "closed_at", "assigned_realtor_id", "assigned_realtor_name")


def owner_lead_page(db, user, *, page=1, state="all", property_id=None):
    agent = aliased(User)
    query = select(Lead.id, Lead.property_id, Listing.title.label("property_title"), Lead.name,
        Lead.email, Lead.phone, Lead.message, Lead.status, Lead.created_at, Lead.assigned_at,
        Lead.contacted_at, Lead.closed_at, Lead.assigned_realtor_id,
        agent.display_name.label("assigned_realtor_name")).select_from(Lead).join(
            Listing, Listing.id == Lead.property_id).outerjoin(agent, agent.id == Lead.assigned_realtor_id
        ).where(Lead.type == "property_inquiry")
    if user.role != "admin":
        # A former submitter must not retain access after a real owner transfer.
        query = query.where(or_(Listing.owner_id == user.id,
            and_(Listing.owner_id.is_(None), Listing.submitted_by == user.id)))
    if property_id is not None:
        # A foreign and an unknown property produce the same empty scoped feed.
        query = query.where(Listing.id == property_id)
    scoped = query.cte("owner_inquiry_scope")
    summary = select(func.count().label("all_total"),
        func.count().filter(scoped.c.status.in_(OPEN_STATUSES)).label("open_total"),
        func.count().filter(scoped.c.status == "closed").label("closed_total")).select_from(scoped).cte("inquiry_totals")
    filtered_query = select(scoped)
    if state == "open":
        filtered_query = filtered_query.where(scoped.c.status.in_(OPEN_STATUSES))
    elif state == "closed":
        filtered_query = filtered_query.where(scoped.c.status == "closed")
    filtered = filtered_query.cte("filtered_inquiries")
    total = select(func.count()).select_from(filtered).scalar_subquery()
    paged = select(filtered).order_by(filtered.c.created_at.desc(), filtered.c.id).offset((page-1)*50).limit(50).cte("inquiry_page")
    statement = select(summary, total.label("total"), paged).select_from(summary.outerjoin(paged, true())).order_by(
        paged.c.created_at.desc(), paged.c.id)
    # One statement keeps page, total and status counters on one MVCC snapshot.
    rows = db.execute(statement).mappings().all()
    first = rows[0]
    counts = {key: int(first[key]) for key in ("all_total", "open_total", "closed_total")}
    counts["unknown_total"] = counts["all_total"]-counts["open_total"]-counts["closed_total"]
    items = []
    for row in rows:
        if row["id"] is None:
            continue
        item = {key: row[key] for key in PUBLIC_FIELDS}
        item["status"] = item["status"] if item["status"] in KNOWN_STATUSES else "unknown"
        items.append(item)
    return {"items": items, "total": int(first["total"]), "page": page, "page_size": 50,
            "state": state, "summary": counts}
