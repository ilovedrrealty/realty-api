"""Native scoped transaction rooms, participation and closing coordination."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, or_, tuple_

from models.lead import Lead
from models.listing import Listing
from models.transaction_document import TransactionDocument as Document, TransactionDocumentVersion as DocumentVersion
from models.transaction_room import (
    TransactionMilestone as Milestone,
    TransactionRoom as Room,
    TransactionRoomEvent as Event,
    TransactionRoomGrant as Grant,
    TransactionTask as Task,
)
from models.user import User
from services.property_management import today_dr


LEAD_TYPES = ("buyer_interest", "property_inquiry")
MANAGERS = {"admin", "realtor"}
PROFESSIONAL_ACCOUNTS = ("buyer", "owner", "realtor")
CURRENT_GRANT_STATES = ("pending", "active")
INVITATION_EVENTS = ("professional_invited", "professional_accepted", "professional_revoked")
MAX_ROOM_ITEMS = 100


def _missing():
    return HTTPException(404, "Transaction room not found.")


def _verified(query):
    return query.filter(User.status == "active", User.email_verified.is_(True))


def _actor(db, actor):
    current = _verified(db.query(User)).filter(User.id == actor.id).populate_existing().one_or_none()
    if current is None:
        raise _missing()
    return current


def _anchored(room, lead):
    return (room.status != "retired" and lead.type in LEAD_TYPES and room.buyer_id is not None
            and room.buyer_id == lead.from_user_id and room.listing_id == lead.property_id)


def _lead_anchor():
    return and_(
        Room.status != "retired", Lead.type.in_(LEAD_TYPES),
        Room.buyer_id == Lead.from_user_id, Room.listing_id == Lead.property_id,
    )


def _grant_access(db, actor):
    return db.query(Grant.id).filter(
        Grant.room_id == Room.id, Grant.recipient_id == actor.id, Grant.state == "active",
    ).exists()


def _rooms_for(db, actor):
    query = db.query(Room, Lead).join(Lead, Room.lead_id == Lead.id)
    if actor.role == "admin":
        return query
    scope = [_grant_access(db, actor)] if actor.role in PROFESSIONAL_ACCOUNTS else []
    if actor.role == "realtor":
        scope.append(Lead.assigned_realtor_id == actor.id)
    if actor.role == "buyer":
        scope.append(Room.buyer_id == actor.id)
    if not scope:
        raise _missing()
    return query.filter(_lead_anchor(), or_(*scope))


def _role(db, actor, room, lead):
    if actor.role == "admin":
        return "admin"
    if not _anchored(room, lead):
        raise _missing()
    if actor.role == "realtor" and lead.assigned_realtor_id == actor.id:
        return "realtor"
    if actor.role == "buyer" and room.buyer_id == actor.id:
        return "buyer"
    if (actor.role in PROFESSIONAL_ACCOUNTS and db.query(Grant.id).filter(
            Grant.room_id == room.id, Grant.recipient_id == actor.id, Grant.state == "active",
    ).first()):
        return "professional"
    raise _missing()


def _locked_context(db, lead_id, room_id, *, read=False):
    # Lead mutations and the database change trigger use this same lock order.
    lead = db.query(Lead).filter(Lead.id == lead_id).populate_existing().with_for_update(read=read).one_or_none()
    room = db.query(Room).filter(
        Room.id == room_id, Room.lead_id == lead_id,
    ).populate_existing().with_for_update(read=read).one_or_none()
    if lead is None or room is None:
        raise _missing()
    return room, lead


def room_for(db, actor, room_id, *, permission="read", lock=False, stabilize=False):
    """Scope a room for another transaction operation. Write scopes lock Lead then Room."""
    if permission not in {"read", "manage", "contribute"}:
        raise ValueError("Unknown transaction room permission")
    actor = _actor(db, actor)
    scoped = _rooms_for(db, actor).filter(Room.id == room_id).populate_existing().one_or_none()
    if scoped is None:
        raise _missing()
    room, lead = scoped
    if lock or permission != "read" or stabilize:
        room, lead = _locked_context(db, lead.id, room.id, read=stabilize and not lock and permission == "read")
        actor = _actor(db, actor)
    role = _role(db, actor, room, lead)
    if permission != "read":
        if room.status != "open" or not _anchored(room, lead):
            raise HTTPException(409, "This transaction room is read-only.")
        if permission == "manage" and role not in MANAGERS:
            raise HTTPException(403, "Transaction room management access is required.")
    return room, role


def require_version(room, expected_version):
    if room.version != expected_version:
        raise HTTPException(409, "The transaction room changed. Refresh and try again.")


def record_event(db, room, actor, role, action, target_id=None):
    """Advance the locked room version and append a safe, caller-owned audit event."""
    room.version += 1
    db.add(Event(
        room_id=room.id, sequence=room.version, action=action, actor_role=role,
        actor_id=actor.id if actor is not None else None, target_id=target_id,
        created_at=datetime.now(timezone.utc),
    ))


def _summary(room, role):
    return dict(id=room.id, lead_id=room.lead_id, listing_id=room.listing_id, title=room.title,
                status=room.status, version=room.version, role=role)


def list_rooms(db, actor, page, page_size):
    actor = _actor(db, actor)
    rooms = _rooms_for(db, actor).order_by(Room.created_at.desc(), Room.id).offset(
        (page - 1) * page_size,
    ).limit(page_size).populate_existing().all()
    items = []
    for room, lead in rooms:
        try:
            role = _role(db, actor, room, lead)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            continue
        items.append(_summary(room, role))
    return {"items": items}


def _eligible_lead(db, actor, lead_id, *, lock=False):
    actor = _actor(db, actor)
    query = db.query(Lead).filter(Lead.id == lead_id, Lead.type.in_(LEAD_TYPES))
    if actor.role == "realtor":
        query = query.filter(Lead.assigned_realtor_id == actor.id)
    elif actor.role != "admin":
        raise _missing()
    if lock:
        query = query.with_for_update()
    lead = query.populate_existing().one_or_none()
    if lead is None:
        raise _missing()
    return lead


def _verify_creation_context(db, lead):
    users = _verified(db.query(User)).filter(User.id.in_((lead.from_user_id, lead.assigned_realtor_id))).all()
    roles = {user.id: user.role for user in users}
    sale = db.query(Listing.id).filter(
        Listing.id == lead.property_id, Listing.status == "active", Listing.transaction.in_(("sale", "both")),
    ).first()
    if not sale or roles.get(lead.from_user_id) != "buyer" or roles.get(lead.assigned_realtor_id) != "realtor":
        raise _missing()


def for_lead(db, actor, lead_id):
    lead = _eligible_lead(db, actor, lead_id)
    existing = db.query(Room.id).filter(Room.lead_id == lead.id).scalar()
    if existing is not None:
        room, role = room_for(db, actor, existing)
        return {"room": _summary(room, role)}
    _verify_creation_context(db, lead)
    return {"room": None}


def create(db, actor, lead_id, body):
    lead = _eligible_lead(db, actor, lead_id, lock=True)
    actor = _actor(db, actor)
    if actor.role != "admin" and not (actor.role == "realtor" and lead.assigned_realtor_id == actor.id):
        raise _missing()
    if db.query(Room.id).filter(Room.lead_id == lead.id).first():
        raise HTTPException(409, "This lead already has a transaction room.")
    _verify_creation_context(db, lead)
    room = Room(
        id=uuid4(), lead_id=lead.id, listing_id=lead.property_id, buyer_id=lead.from_user_id,
        title=body.title, status="open", version=1, created_at=datetime.now(timezone.utc),
    )
    db.add(room)
    role = actor.role
    db.flush()
    # The room is inserted before its first event, which has a foreign key to it.
    db.add(Event(room_id=room.id, sequence=1, action="room_created", actor_role=role,
                 actor_id=actor.id, created_at=room.created_at))
    return _commit_detail(db, room, role, actor.id)


def _member(user, role, state="active", grant=None, *, show_code=False):
    return dict(
        user_id=user.id if user else None, grant_id=grant.id if grant and show_code else None,
        member_code=user.user_code if user and show_code else None,
        display_name=(user.display_name if user else None) or "Transaction participant", role=role, state=state,
    )


def _members(db, room, role):
    if room.status == "retired":
        return []
    lead = db.query(Lead).filter(Lead.id == room.lead_id).populate_existing().one()
    if not _anchored(room, lead):
        return []
    staff = role in MANAGERS
    base_roles = {room.buyer_id: "buyer", lead.assigned_realtor_id: "realtor"}
    base_users = _verified(db.query(User)).filter(User.id.in_(base_roles)).populate_existing().all()
    members = [_member(user, user.role, show_code=staff) for user in base_users
               if user.role == base_roles.get(user.id)]
    grants = db.query(Grant, User).outerjoin(
        User, and_(User.id == Grant.recipient_id, User.status == "active", User.email_verified.is_(True),
                   User.role.in_(PROFESSIONAL_ACCOUNTS)),
    ).filter(Grant.room_id == room.id)
    if not staff:
        grants = grants.filter(Grant.state == "active", User.id.is_not(None))
    for grant, user in grants.order_by(Grant.invited_at, Grant.id).populate_existing().all():
        if grant.state == "active" and (user is None or user.id in base_roles):
            continue
        members.append(_member(user, "professional", grant.state, grant, show_code=staff))
    return members


def document_grant_id(db, room_id, actor_id):
    return db.query(Grant.id).filter(
        Grant.room_id == room_id, Grant.recipient_id == actor_id, Grant.state == "active",
    ).scalar()


def may_download_document(version, role, grant_id):
    if version.status == "reserved":
        return False
    if role in MANAGERS:
        return True
    return version.status == "approved" and (
        (role == "buyer" and version.share_with_buyer)
        or (role == "professional" and grant_id is not None and version.professional_grant_id == grant_id)
    )


def _visible_task_evidence(db, room_id, tasks, role, actor_id, current_members):
    linked = {(row.evidence_document_id, row.evidence_version_id) for row in tasks if row.evidence_version_id is not None}
    if not linked:
        return {}
    grant_id = document_grant_id(db, room_id, actor_id) if role == "professional" else None
    versions = db.query(DocumentVersion, Document.title).join(Document, and_(
        Document.room_id == DocumentVersion.room_id, Document.id == DocumentVersion.document_id,
    )).filter(DocumentVersion.room_id == room_id,
              tuple_(DocumentVersion.document_id, DocumentVersion.id).in_(linked)).populate_existing().all()
    readable = {
        (version.document_id, version.id): dict(document_id=version.document_id, version_id=version.id,
                                               title=title, number=version.number)
        for version, title in versions if may_download_document(version, role, grant_id)
    }
    evidence = {task.id: readable[(task.evidence_document_id, task.evidence_version_id)].copy()
                for task in tasks if (task.evidence_document_id, task.evidence_version_id) in readable}
    completed = [task.id for task in tasks if task.status == "done" and task.id in evidence]
    receipts = {}
    if completed:
        receipts = {
            target_id: (created_at, name if role in MANAGERS or reviewer_id in current_members else None)
            for target_id, created_at, reviewer_id, name in db.query(
                Event.target_id, Event.created_at, Event.actor_id, User.display_name,
            ).outerjoin(User, User.id == Event.actor_id).filter(
                Event.room_id == room_id, Event.action == "task_evidence_reviewed", Event.target_id.in_(completed),
            ).order_by(Event.sequence).all()
        }
    for task_id, item in evidence.items():
        item["reviewed_at"], item["reviewer_name"] = receipts.get(task_id, (None, None))
    return evidence


def _detail(db, room, role, actor_id):
    members = _members(db, room, role)
    current_members = {member["user_id"] for member in members if member["state"] == "active"}
    tasks = db.query(Task).filter(Task.room_id == room.id).order_by(Task.due_on.nulls_last(), Task.title, Task.id).all()
    evidence = _visible_task_evidence(db, room.id, tasks, role, actor_id, current_members)
    milestones = db.query(Milestone).filter(Milestone.room_id == room.id).order_by(
        Milestone.target_on.nulls_last(), Milestone.title, Milestone.id,
    ).all()
    return dict(
        **_summary(room, role), members=members,
        tasks=[dict(id=row.id, title=row.title, due_on=row.due_on, status=row.status, version=row.version,
                    assignee_user_id=row.assignee_user_id if row.assignee_user_id in current_members else None,
                    requires_evidence_review=row.evidence_version_id is not None
                    and (role in MANAGERS or actor_id == row.assignee_user_id),
                    evidence=evidence.get(row.id))
               for row in tasks],
        milestones=[dict(id=row.id, title=row.title, target_on=row.target_on, actual_on=row.actual_on,
                         version=row.version) for row in milestones],
    )


def _commit_detail(db, room, role, actor_id):
    db.flush()
    detail = _detail(db, room, role, actor_id)
    db.commit()
    return detail


def read(db, actor, room_id):
    room, role = room_for(db, actor, room_id, stabilize=True)
    return _detail(db, room, role, actor.id)


def history(db, actor, room_id, before, limit):
    room, role = room_for(db, actor, room_id, stabilize=True)
    events = db.query(Event).filter(Event.room_id == room.id)
    if role not in MANAGERS:
        own_grants = db.query(Grant.id).filter(Grant.room_id == room.id, Grant.recipient_id == actor.id)
        events = events.filter(
            ~Event.action.startswith("document_", autoescape=True),
            or_(~Event.action.in_(INVITATION_EVENTS), Event.target_id.in_(own_grants.statement)),
        )
    if before is not None:
        events = events.filter(Event.sequence < before)
    return {"items": [
        dict(sequence=row.sequence, action=row.action, actor_role=row.actor_role,
             target_id=row.target_id, created_at=row.created_at)
        for row in events.order_by(Event.sequence.desc()).limit(limit).all()
    ]}


def _limit_room_items(db, model, room):
    if db.query(model.id).filter(model.room_id == room.id).count() >= MAX_ROOM_ITEMS:
        raise HTTPException(409, "This transaction room has reached its item limit.")


def invite(db, actor, room_id, body):
    room, role = room_for(db, actor, room_id, permission="manage")
    require_version(room, body.expected_version)
    lead = db.query(Lead).filter(Lead.id == room.lead_id).one()
    recipient = _verified(db.query(User)).filter(
        User.user_code == body.member_code, User.role.in_(PROFESSIONAL_ACCOUNTS),
    ).populate_existing().one_or_none()
    if recipient is None or recipient.id in {room.buyer_id, lead.assigned_realtor_id, actor.id}:
        raise HTTPException(404, "Eligible participant not found.")
    if db.query(Grant.id).filter(
        Grant.room_id == room.id, Grant.recipient_id == recipient.id, Grant.state.in_(CURRENT_GRANT_STATES),
    ).first():
        raise HTTPException(409, "This participant already has a current invitation.")
    _limit_room_items(db, Grant, room)
    grant = Grant(id=uuid4(), room_id=room.id, recipient_id=recipient.id, state="pending",
                  invited_at=datetime.now(timezone.utc))
    db.add(grant)
    record_event(db, room, actor, role, "professional_invited", grant.id)
    return _commit_detail(db, room, role, actor.id)


def _pending_for(db, actor):
    return db.query(Grant, Room).join(Room, Grant.room_id == Room.id).join(Lead, Room.lead_id == Lead.id).filter(
        Grant.recipient_id == actor.id, Grant.state == "pending", Room.status == "open", _lead_anchor(),
        Room.buyer_id != actor.id, Lead.assigned_realtor_id.is_distinct_from(actor.id),
    )


def pending(db, actor, page, page_size):
    actor = _actor(db, actor)
    if actor.role not in PROFESSIONAL_ACCOUNTS:
        return {"items": []}
    records = _pending_for(db, actor).order_by(Grant.invited_at.desc(), Grant.id).offset(
        (page - 1) * page_size,
    ).limit(page_size).all()
    return {"items": [{"id": grant.id, "invited_at": grant.invited_at} for grant, _ in records]}


def accept(db, actor, grant_id):
    actor = _actor(db, actor)
    location = _pending_for(db, actor).filter(Grant.id == grant_id).one_or_none()
    if actor.role not in PROFESSIONAL_ACCOUNTS or location is None:
        raise _missing()
    _, previous_room = location
    room, lead = _locked_context(db, previous_room.lead_id, previous_room.id)
    actor = _actor(db, actor)
    grant = db.query(Grant).filter(
        Grant.id == grant_id, Grant.room_id == room.id, Grant.recipient_id == actor.id, Grant.state == "pending",
    ).populate_existing().one_or_none()
    if (grant is None or not _anchored(room, lead) or room.status != "open"
            or actor.role not in PROFESSIONAL_ACCOUNTS or actor.id in {room.buyer_id, lead.assigned_realtor_id}):
        raise _missing()
    grant.state, grant.accepted_at = "active", datetime.now(timezone.utc)
    record_event(db, room, actor, "professional", "professional_accepted", grant.id)
    return _commit_detail(db, room, "professional", actor.id)


def revoke(db, actor, room_id, grant_id, body):
    room, role = room_for(db, actor, room_id, permission="manage")
    require_version(room, body.expected_version)
    grant = db.query(Grant).filter(
        Grant.id == grant_id, Grant.room_id == room.id, Grant.state.in_(CURRENT_GRANT_STATES),
    ).one_or_none()
    if grant is None:
        raise _missing()
    grant.state, grant.revoked_at = "revoked", datetime.now(timezone.utc)
    if grant.recipient_id is not None:
        db.query(Task).filter(Task.room_id == room.id, Task.assignee_user_id == grant.recipient_id).update(
            {Task.assignee_user_id: None, Task.version: Task.version + 1}, synchronize_session="fetch",
        )
    record_event(db, room, actor, role, "professional_revoked", grant.id)
    return _commit_detail(db, room, role, actor.id)


def _check_assignee(db, room, user_id):
    if user_id is not None and user_id not in {
        member["user_id"] for member in _members(db, room, "admin") if member["state"] == "active"
    }:
        raise HTTPException(422, "Choose an active room participant.")


def _check_evidence_access(db, room, reviewer_id, document_id, version_id):
    member = next((entry for entry in _members(db, room, "admin")
                   if entry["role"] == "professional" and entry["state"] == "active"
                   and entry["user_id"] == reviewer_id), None)
    version = db.query(DocumentVersion).filter(
        DocumentVersion.room_id == room.id, DocumentVersion.document_id == document_id, DocumentVersion.id == version_id,
    ).populate_existing().one_or_none()
    if member is None or version is None or not may_download_document(version, "professional", member["grant_id"]):
        raise HTTPException(422, "Choose a document version already shared with the named room professional.")


def create_task(db, actor, room_id, body):
    room, role = room_for(db, actor, room_id, permission="manage")
    require_version(room, body.expected_version)
    _check_assignee(db, room, body.assignee_user_id)
    _limit_room_items(db, Task, room)
    task = Task(id=uuid4(), room_id=room.id, title=body.title, assignee_user_id=body.assignee_user_id,
                due_on=body.due_on, status="open", version=1)
    if body.evidence is not None:
        _check_evidence_access(db, room, body.assignee_user_id, body.evidence.document_id, body.evidence.version_id)
        task.evidence_document_id = body.evidence.document_id
        task.evidence_version_id = body.evidence.version_id
    db.add(task)
    record_event(db, room, actor, role, "task_created", task.id)
    return _commit_detail(db, room, role, actor.id)


def update_task(db, actor, room_id, task_id, body):
    room, role = room_for(db, actor, room_id, permission="contribute")
    require_version(room, body.expected_version)
    task = db.query(Task).filter(Task.id == task_id, Task.room_id == room.id).one_or_none()
    if task is None:
        raise _missing()
    changes = body.model_dump(exclude={"expected_version"}, exclude_unset=True)
    if role not in MANAGERS and (
        task.assignee_user_id != actor.id or task.status not in {"open", "in_progress"}
        or changes.keys() != {"status"} or body.status not in {"open", "in_progress", "done"}
    ):
        raise HTTPException(403, "This task update requires transaction room management access.")
    if "assignee_user_id" in changes:
        _check_assignee(db, room, body.assignee_user_id)
    evidence_before = task.evidence_document_id, task.evidence_version_id
    evidence = evidence_before
    if "evidence" in changes:
        changes.pop("evidence")
        evidence = (body.evidence.document_id, body.evidence.version_id) if body.evidence is not None else (None, None)
        if evidence_before[1] is not None and evidence[1] is None:
            raise HTTPException(422, "Cancel the task to retain its linked document.")
    reviewer_id = changes.get("assignee_user_id", task.assignee_user_id)
    scope_changed = evidence != evidence_before or reviewer_id != task.assignee_user_id
    if task.status == "done" and (
        (evidence_before[1] and changes.get("status", "done") != "done")
        or ((evidence_before[1] or evidence[1]) and scope_changed)
    ):
        raise HTTPException(409, "Create another task to review a document for completed work.")
    action = "task_updated"
    if evidence[1]:
        finishing = changes.get("status") == "done" and task.status != "done"
        if finishing and role != "professional":
            raise HTTPException(403, "The named professional must complete this document review.")
        if scope_changed or (role == "professional" and changes.get("status") in {"in_progress", "done"}):
            _check_evidence_access(db, room, reviewer_id, *evidence)
        if finishing:
            action = "task_evidence_reviewed"
    task.evidence_document_id, task.evidence_version_id = evidence
    for field, value in changes.items():
        setattr(task, field, value)
    task.version += 1
    record_event(db, room, actor, role, action, task.id)
    return _commit_detail(db, room, role, actor.id)


def save_milestone(db, actor, room_id, body, milestone_id=None):
    room, role = room_for(db, actor, room_id, permission="manage")
    require_version(room, body.expected_version)
    if body.actual_on is not None and body.actual_on > today_dr():
        raise HTTPException(422, "The actual date cannot be in the future.")
    if milestone_id is None:
        _limit_room_items(db, Milestone, room)
        milestone = Milestone(id=uuid4(), room_id=room.id, version=0)
        db.add(milestone)
    else:
        milestone = db.query(Milestone).filter(
            Milestone.id == milestone_id, Milestone.room_id == room.id,
        ).one_or_none()
        if milestone is None:
            raise _missing()
    milestone.title, milestone.target_on, milestone.actual_on = body.title, body.target_on, body.actual_on
    milestone.version += 1
    action = "milestone_created" if milestone_id is None else "milestone_updated"
    record_event(db, room, actor, role, action, milestone.id)
    return _commit_detail(db, room, role, actor.id)


def close(db, actor, room_id, body):
    room, role = room_for(db, actor, room_id, permission="manage")
    require_version(room, body.expected_version)
    room.status = "closed"
    db.query(Grant).filter(Grant.room_id == room.id, Grant.state == "pending").update(
        {Grant.state: "revoked", Grant.revoked_at: datetime.now(timezone.utc)}, synchronize_session="fetch",
    )
    record_event(db, room, actor, role, "room_closed")
    return _commit_detail(db, room, role, actor.id)
