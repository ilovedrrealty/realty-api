"""Offer and accept private registry custody without sharing historical records."""
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_

from models.property_custody import NetworkPropertyCustodyAction as Receipt, NetworkPropertyCustodyTransfer as Transfer
from models.property_network import NetworkProject as Project, NetworkProperty as Property
from models.service_visit import NetworkServiceWork as Work
from models.service_workflow import NetworkServiceRequest as ServiceRequest
from models.user import User
from services import property_network as network, service_workflow as workflow


PERIOD = timedelta(days=7)
FIELDS = ("title", "location", "address", "property_class", "relationship")
CLOSED_PROJECT = {"completed", "archived"}
TERMINAL_WORK = {"completed", "cancelled"}


def unavailable():
    raise HTTPException(404, "Private property custody record is unavailable")


def conflict():
    raise HTTPException(409, "This custody offer changed or the action is unavailable. Refresh before retrying")


def _member(user):
    return user is not None and user.status == "active" and user.email_verified is True and user.role != "admin"


def _require_member(user):
    if not _member(user):
        unavailable()


def _property(db, identity, *, lock=False):
    query = db.query(Property).filter(Property.id == identity).populate_existing()
    if lock:
        query = query.with_for_update()
    row = query.first()
    if row is None or row.status != "active":
        unavailable()
    return row


def _current_owner(row, user):
    _require_member(user)
    if row.current_custodian_id != user.id:
        unavailable()


def _fields(row):
    return {key: getattr(row, key) for key in FIELDS}


def _dependent_records(db, property_id):
    # All lifecycle mutations share User -> property -> request -> project.
    requests = db.query(ServiceRequest).filter_by(property_id=property_id).order_by(
        ServiceRequest.id).populate_existing().with_for_update().all()
    projects = db.query(Project).filter_by(property_id=property_id).order_by(
        Project.id).populate_existing().with_for_update().all()
    award_ids = {row.id for row in requests if row.status == "awarded" and not row.custody_closed}
    work = {row.request_id: row.status for row in db.query(Work).filter(
        Work.request_id.in_(award_ids)).order_by(Work.request_id).populate_existing().with_for_update().all()}
    project_by_id = {row.id: row for row in projects}
    open_projects = sum(row.status not in CLOSED_PROJECT for row in projects if row.closed_custody_title is None)
    open_requests = sum(row.status == "open" for row in requests if not row.custody_closed)
    unfinished = sum(row.project_id not in project_by_id or project_by_id[row.project_id].status not in CLOSED_PROJECT
                     or work.get(row.id) not in TERMINAL_WORK for row in requests if row.id in award_ids)
    return requests, projects, {"active_projects": open_projects, "open_requests": open_requests, "unfinished_awards": unfinished}


def _pending(db, property_id):
    return db.query(Transfer).filter_by(property_id=property_id, status="pending").populate_existing().first()


def options(db, user, identity):
    _require_member(user)
    actor = workflow.lock_users(db, user, ())
    row = _property(db, identity, lock=True)
    _current_owner(row, actor)
    _, _, blockers = _dependent_records(db, row.id)
    pending = _pending(db, row.id)
    open_id = str(pending.id) if pending is not None and pending.expires_at > network.now() else None
    return {"property_id": str(row.id), "property_version": row.version, "fields": _fields(row), "blockers": blockers,
            "pending_transfer_id": open_id}


def _actions(status, view):
    if status != "pending":
        return []
    return ["cancel"] if view == "sent" else ["accept", "decline"]


def _view(row, view, instant, *, reveal=False):
    status = "expired" if row.status == "pending" and row.expires_at <= instant else row.status
    return {"id": str(row.id), "label": row.label, "view": view, "status": status, "version": row.version,
            "expires_at": row.expires_at, "fields": row.fields if reveal else None,
            "property_id": str(row.property_id) if reveal else None, "actions": _actions(status, view)}


def listed(db, user, view, page):
    _require_member(user)
    instant = network.now()
    column = Transfer.offered_by_id if view == "sent" else Transfer.recipient_id
    query = db.query(Transfer).filter(column == user.id).order_by(Transfer.created_at.desc(), Transfer.id)
    return {"view": view, **network.page(query, page, lambda row: _view(row, view, instant))}


def _find(db, user, identity, *, lock=False):
    query = db.query(Transfer).filter(Transfer.id == identity, or_(
        Transfer.offered_by_id == user.id, Transfer.recipient_id == user.id)).populate_existing()
    row = (query.with_for_update() if lock else query).first()
    if row is None:
        unavailable()
    return row


def detail(db, user, identity):
    _require_member(user)
    hint = _find(db, user, identity)
    actor = workflow.lock_users(db, user, ())
    _require_member(actor)
    source = db.query(Property).filter_by(id=hint.property_id).populate_existing().with_for_update().one()
    transfer = _find(db, actor, identity)
    offered = transfer.offered_by_id == actor.id
    reveal = offered or (transfer.status == "accepted" and source.current_custodian_id == actor.id
                         and source.custody_version == transfer.custody_version + 1)
    return _view(transfer, "sent" if offered else "received", network.now(), reveal=reveal)


def _receipt(row):
    return {"id": str(row.transfer_id), "operation": row.operation, "status": row.status, "version": row.version}


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


def _save(db, actor, submission_id, transfer, operation, digest):
    db.flush()
    receipt = Receipt(actor_id=actor.id, submission_id=submission_id, transfer_id=transfer.id, operation=operation,
        status=transfer.status, version=transfer.version, creation_sha256=digest)
    db.add(receipt)
    result = _receipt(receipt)
    db.commit()
    return result


def offer(db, user, body):
    _require_member(user)
    digest = workflow.fingerprint("property_custody_offer", body.property_id, body)
    previous = _replay(db, user, body.submission_id, digest)
    if previous is not None:
        return previous
    recipient = db.query(User).filter_by(user_code=body.recipient_user_code).first()
    if recipient is None or recipient.id == user.id:
        unavailable()
    actor = workflow.lock_users(db, user, {recipient.id})
    _require_member(actor)
    previous = _replay(db, actor, body.submission_id, digest)
    if previous is not None:
        return previous
    _require_member(recipient)
    if recipient.user_code != body.recipient_user_code:
        unavailable()
    row = _property(db, body.property_id, lock=True)
    _current_owner(row, actor)
    if body.expected_property_version != row.version:
        conflict()
    _, _, blockers = _dependent_records(db, row.id)
    instant = network.now()
    old = _pending(db, row.id)
    if any(blockers.values()) or (old is not None and old.expires_at > instant):
        conflict()
    if old is not None:
        old.status, old.version, old.finished_at = "expired", 2, instant
        network.audit(db, actor, "property_custody", old.id, "expired")
        db.flush()
    transfer = Transfer(id=uuid4(), property_id=row.id, offered_by_id=actor.id, recipient_id=recipient.id,
        property_version=row.version, custody_version=row.custody_version, label=body.label, fields=_fields(row),
        prior_notes=row.notes, status="pending", version=1, created_at=instant, expires_at=instant + PERIOD)
    db.add(transfer)
    network.audit(db, actor, "property_custody", transfer.id, "offered")
    return _save(db, actor, body.submission_id, transfer, "offer", digest)


def _accept(db, row, transfer, instant):
    _, _, blockers = _dependent_records(db, row.id)
    if (row.status != "active" or row.current_custodian_id != transfer.offered_by_id
            or row.version != transfer.property_version or row.custody_version != transfer.custody_version
            or _fields(row) != transfer.fields or row.notes != transfer.prior_notes or any(blockers.values())):
        conflict()
    if db.query(Property).filter(Property.current_custodian_id == transfer.recipient_id).count() >= network.MAX_REGISTRY_PROPERTIES:
        conflict()
    transfer.status, transfer.version, transfer.finished_at = "accepted", 2, instant
    db.flush()
    row.current_custodian_id = transfer.recipient_id
    row.custody_version += 1
    row.version += 1
    row.notes = ""
    row.updated_at = instant


def change(db, user, identity, body):
    _require_member(user)
    digest = workflow.fingerprint("property_custody_action", identity, body)
    previous = _replay(db, user, body.submission_id, digest)
    if previous is not None:
        return previous
    hint = _find(db, user, identity)
    actor = workflow.lock_users(db, user, {hint.offered_by_id, hint.recipient_id})
    _require_member(actor)
    previous = _replay(db, actor, body.submission_id, digest)
    if previous is not None:
        return previous
    row = db.query(Property).filter_by(id=hint.property_id).populate_existing().with_for_update().one()
    transfer = _find(db, actor, identity, lock=True)
    instant = network.now()
    view = "sent" if transfer.offered_by_id == actor.id else "received"
    if (transfer.version != body.expected_version or transfer.expires_at <= instant
            or body.operation not in _actions(transfer.status, view)):
        conflict()
    if body.operation == "accept":
        _require_member(db.get(User, transfer.offered_by_id))
        _accept(db, row, transfer, instant)
    else:
        transfer.status = "cancelled" if body.operation == "cancel" else "declined"
        transfer.version, transfer.finished_at = 2, instant
    network.audit(db, actor, "property_custody", transfer.id, transfer.status)
    return _save(db, actor, body.submission_id, transfer, body.operation, digest)
