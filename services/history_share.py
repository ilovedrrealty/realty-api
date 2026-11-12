"""Explicit property history sharing without granting project access."""
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import aliased

from models.history_share import NetworkHistoryShare as Share, NetworkHistoryShareAction as Receipt
from models.listing import Listing
from models.property_network import (NetworkProject as Project, NetworkProjectMember as Member,
                                     NetworkProjectUpdate as Update, NetworkProperty as Property)
from models.user import User
from services import property_network as network, service_workflow as workflow


ANCHOR_STATES = ("open", "in_progress")
LIVE_STATES = ("pending", "accepted")


def unavailable():
    raise HTTPException(404, "History share is unavailable")


def conflict():
    raise HTTPException(409, "This history share changed or the action is unavailable. Refresh before retrying")


def _verified(user):
    return user is not None and user.status == "active" and user.email_verified is True and user.role != "admin"


def _require_member(user):
    if not _verified(user):
        unavailable()


def _source_ids(kind, identity):
    return (identity, None) if kind == "private" else (None, identity)


def _active_member():
    return and_(Member.active.is_(True), Member.role == "professional")


def _owner_scope(db, owner, property_id, listing_id, anchor_id, *, history_project_ids=()):
    """Call after locking users. Lock source, then all affected projects in ID order."""
    _require_member(owner)
    try:
        source, custodian = workflow.source_row(db, property_id, listing_id, lock=True)
    except HTTPException as error:
        if error.status_code != 404:
            raise
        unavailable()
    if (custodian is None or custodian.id != owner.id or source.status != "active"
            or not workflow.source_authorized(db, owner, source)):
        unavailable()
    wanted = {anchor_id, *history_project_ids}
    projects = db.query(Project).filter(Project.id.in_(wanted), Project.owner_id == owner.id,
        Project.property_id == property_id, Project.listing_id == listing_id).order_by(Project.id).populate_existing().with_for_update().all()
    indexed = {project.id: project for project in projects}
    if set(indexed) != wanted or indexed[anchor_id].status not in ANCHOR_STATES:
        unavailable()
    return indexed


def _completed_updates(db, owner_id, property_id, listing_id):
    return db.query(Update, Project).join(Project, Project.id == Update.project_id).filter(
        Project.owner_id == owner_id, Project.property_id == property_id, Project.listing_id == listing_id,
        Project.status == "completed", Update.kind.in_(("progress", "correction")))


def _summary_date(update, project):
    return {"occurred_on": update.occurred_on.isoformat(), "service_category_ids": project.service_category_ids}


def options(db, user, kind, source_id, anchor_id, page):
    _require_member(user)
    owner = workflow.lock_users(db, user, ())
    property_id, listing_id = _source_ids(kind, source_id)
    _owner_scope(db, owner, property_id, listing_id, anchor_id)
    professionals = db.query(User.user_code, User.display_name).join(Member, Member.user_id == User.id).filter(
        Member.project_id == anchor_id, _active_member(), User.id != owner.id, User.status == "active",
        User.email_verified.is_(True), User.role != "admin", User.user_code > 0).order_by(User.user_code).all()
    updates = _completed_updates(db, owner.id, property_id, listing_id).order_by(Update.occurred_on.desc(), Update.id)
    return {"source_kind": kind, "source_id": str(source_id), "anchor_project_id": str(anchor_id),
        "professionals": [{"user_code": code, "display_name": name} for code, name in professionals],
        **network.page(updates, page, lambda row: {"update_id": str(row[0].id), "project_title": row[1].title,
                                                   **_summary_date(*row)})}


def _live_scope(db):
    """One database snapshot for current custody, identity, assignment and share rows."""
    owner = aliased(User, name="history_owner")
    recipient = aliased(User, name="history_recipient")
    property_current = and_(Property.id == Share.property_id, Property.custodian_id == Share.owner_id,
                            Property.status == "active")
    listing_current = and_(Listing.id == Share.listing_id, Listing.status == "active", or_(
        and_(Listing.owner_id == Share.owner_id, owner.role.in_(("owner", "realtor"))),
        and_(Listing.owner_id.is_(None), Listing.submitted_by == Share.owner_id, owner.role == "owner")))
    return db.query(Share).join(owner, owner.id == Share.owner_id).join(recipient, recipient.id == Share.recipient_id).join(
        Project, Project.id == Share.anchor_project_id).join(Member, Member.id == Share.member_id).outerjoin(
        Property, Property.id == Share.property_id).outerjoin(Listing, Listing.id == Share.listing_id).filter(
        Share.invalidated_at.is_(None), or_(property_current, listing_current), owner.status == "active",
        owner.email_verified.is_(True), owner.role != "admin", recipient.status == "active", recipient.email_verified.is_(True),
        recipient.role != "admin", recipient.user_code > 0, Project.owner_id == Share.owner_id, Project.status.in_(ANCHOR_STATES),
        Project.property_id.is_not_distinct_from(Share.property_id), Project.listing_id.is_not_distinct_from(Share.listing_id),
        Member.project_id == Project.id, Member.user_id == Share.recipient_id, _active_member(),
        Member.reminder_grant_version == Share.member_version)


def _actions(status, view):
    if view == "sent" or status == "accepted":
        return ["revoke"] if status in LIVE_STATES else []
    return ["accept", "decline"] if status == "pending" else []


def _view(share, view, instant, *, detail=False):
    status = "expired" if share.status in LIVE_STATES and share.expires_at <= instant else share.status
    items = None
    if detail and (view == "sent" or status == "accepted"):
        items = [{field: item[field] for field in ("occurred_on", "service_category_ids", "summary")} for item in share.items]
    return {"id": str(share.id), "label": share.label, "view": view, "status": status, "expires_at": share.expires_at,
            "version": share.version, "item_count": len(share.items), "items": items, "actions": _actions(status, view)}


def _received(instant):
    return and_(Share.status.in_(LIVE_STATES), Share.expires_at > instant)


def listed(db, user, view, page):
    _require_member(user)
    instant = network.now()
    query = _live_scope(db)
    if view == "sent":
        query = query.filter(Share.owner_id == user.id)
    else:
        query = query.filter(Share.recipient_id == user.id, _received(instant))
    return {"view": view, **network.page(query.order_by(Share.created_at.desc(), Share.id), page,
                                          lambda row: _view(row, view, instant))}


def detail(db, user, identity):
    _require_member(user)
    instant = network.now()
    share = _live_scope(db).filter(Share.id == identity, or_(Share.owner_id == user.id,
        and_(Share.recipient_id == user.id, _received(instant)))).first()
    if share is None:
        unavailable()
    return _view(share, "sent" if share.owner_id == user.id else "received", instant, detail=True)


def _receipt(row):
    return {"id": str(row.share_id), "operation": row.operation, "status": row.status, "version": row.version}


def own_submission(db, user, submission_id):
    _require_member(user)
    row = db.get(Receipt, (user.id, submission_id))
    if row is None:
        unavailable()
    return _receipt(row)


def _replay(db, user, key, digest):
    row = db.get(Receipt, (user.id, key))
    if row is not None and row.creation_sha256 != digest:
        conflict()
    return _receipt(row) if row is not None else None


def _save(db, user, body, share, operation, digest):
    # Database guards see the current state before they validate its receipt.
    db.flush()
    receipt = Receipt(actor_id=user.id, submission_id=body.submission_id, share_id=share.id, operation=operation,
                      status=share.status, version=share.version, creation_sha256=digest)
    db.add(receipt)
    result = _receipt(receipt)
    db.commit()
    return result


def create(db, user, body):
    _require_member(user)
    digest = workflow.fingerprint("history_share_create", body.source_id, body)
    prior = _replay(db, user, body.submission_id, digest)
    if prior is not None:
        return prior
    recipient = db.query(User).filter(User.user_code == body.recipient_user_code).first()
    if recipient is None or recipient.id == user.id:
        unavailable()
    owner = workflow.lock_users(db, user, (recipient.id,))
    _require_member(owner)
    prior = _replay(db, owner, body.submission_id, digest)
    if prior is not None:
        return prior
    _require_member(recipient)
    if recipient.user_code != body.recipient_user_code:
        unavailable()

    property_id, listing_id = _source_ids(body.source_kind, body.source_id)
    keys = {item.update_id for item in body.items}
    project_ids = {project_id for project_id, in db.query(Update.project_id).filter(Update.id.in_(keys))}
    _owner_scope(db, owner, property_id, listing_id, body.anchor_project_id, history_project_ids=project_ids)
    member = db.query(Member).filter(Member.project_id == body.anchor_project_id, Member.user_id == recipient.id,
                                    _active_member()).populate_existing().first()
    rows = {update.id: (update, project) for update, project in _completed_updates(db, owner.id, property_id, listing_id)
            .filter(Update.id.in_(keys)).populate_existing().all()}
    if member is None or set(rows) != keys:
        unavailable()
    instant = network.now()
    share = Share(id=uuid4(), owner_id=owner.id, recipient_id=recipient.id, property_id=property_id, listing_id=listing_id,
                  anchor_project_id=body.anchor_project_id, member_id=member.id, member_version=member.reminder_grant_version,
                  label=body.label, items=[{"update_id": str(item.update_id), **_summary_date(*rows[item.update_id]),
                                          "summary": item.summary} for item in body.items],
                  status="pending", version=1, created_at=instant, expires_at=instant + timedelta(days=body.expires_in_days))
    db.add(share)
    return _save(db, owner, body, share, "create", digest)


def change(db, user, identity, body):
    _require_member(user)
    digest = workflow.fingerprint("history_share_action", identity, body)
    prior = _replay(db, user, body.submission_id, digest)
    if prior is not None:
        return prior
    hint = db.query(Share).filter(Share.id == identity, or_(Share.owner_id == user.id, Share.recipient_id == user.id)).first()
    if hint is None:
        unavailable()
    actor = workflow.lock_users(db, user, (hint.owner_id, hint.recipient_id))
    _require_member(actor)
    prior = _replay(db, actor, body.submission_id, digest)
    if prior is not None:
        return prior
    owner = db.get(User, hint.owner_id)
    _owner_scope(db, owner, hint.property_id, hint.listing_id, hint.anchor_project_id)
    share = _live_scope(db).filter(Share.id == identity).populate_existing().with_for_update(of=Share).first()
    if share is None:
        unavailable()
    instant = network.now()
    sender = actor.id == share.owner_id
    if not sender and (share.status not in LIVE_STATES or share.expires_at <= instant):
        unavailable()
    if (share.version != body.expected_version or share.expires_at <= instant
            or body.operation not in _actions(share.status, "sent" if sender else "received")):
        conflict()
    share.status = {"accept": "accepted", "decline": "declined", "revoke": "revoked"}[body.operation]
    share.version += 1
    return _save(db, actor, body, share, body.operation, digest)
