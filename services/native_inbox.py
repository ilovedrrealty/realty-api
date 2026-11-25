"""Native private inbox and audited legacy reads. No external delivery path."""
import base64
import binascii
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import and_, func, literal, or_
from sqlalchemy.orm import aliased, load_only

from models.native_inbox import (InboxAudit as Audit, InboxBlock as Block,
    InboxCommand as Command, InboxConversation as Conversation, InboxEntry as Entry,
    InboxPreference as Preference, InboxReadState as ReadState)
from models.property_network import NetworkBlock as SocialBlock, SocialNetworkProfile as SocialProfile
from models.service_conversation import NetworkServiceConversationMessage as AwardMessage
from models.service_negotiation import NetworkServiceNegotiationMessage as NegotiationMessage
from models.user import User

POLICY = "native-inbox-v1"
PAGE_SIZE = 30
MAX_SEQUENCE = 9007199254740991
CHANNELS = {"in_app": True, "manual_whatsapp": True, "manual_call": True,
            "email": False, "gmail": False, "whatsapp_sync": False,
            "sms": False, "telephone_sync": False}
USER_FIELDS = (User.id, User.user_code, User.display_name, User.role, User.status,
               User.email_verified, User.assigned_realtor_id)


def now():
    return datetime.now(timezone.utc)


def unavailable():
    raise HTTPException(404, "Conversation is not available")


def conflict():
    raise HTTPException(409, "This inbox command conflicts with the recorded action. Refresh before retrying.")


def _active(user):
    return user is not None and user.status == "active" and user.email_verified is True


def _eligible(user):
    return _active(user) and user.role in {"realtor", "admin"}


def _users(db, identifiers, *, write=False):
    """One globally ordered lock acquisition; callers never append another User lock."""
    ids = sorted(set(identifiers), key=str)
    rows = db.query(User).options(load_only(*USER_FIELDS)).filter(User.id.in_(ids)).order_by(User.id).populate_existing()
    # Role/assignment updates must wait, while FK KEY SHARE checks may proceed.
    # NO KEY UPDATE avoids a cycle with legacy social-block inserts that lock
    # only their blocker before PostgreSQL checks the target User foreign key.
    rows = rows.with_for_update(read=not write, key_share=write).all()
    return {row.id: row for row in rows}


def _actor(users, actor_id):
    actor = users.get(actor_id)
    if not _active(actor):
        unavailable()
    return actor


def member(user, *, role=None):
    name = (user.display_name or "").strip()
    # A missing name must never fall back to a private address or telephone field.
    if not name or any(ord(c)<32 or 127<=ord(c)<=159 for c in name):
        name = f"Member #{user.user_code}" if user.user_code is not None else "Member"
    return {"id": str(user.id), "user_code": user.user_code, "display_name": name[:100],
            "role": role or user.role}


def _primary(conversation, actor):
    return actor.id in {conversation.user_low_id, conversation.user_high_id}


def _pair(conversation, users):
    left, right = users.get(conversation.user_low_id), users.get(conversation.user_high_id)
    if left is None or right is None:
        unavailable()
    return left, right


def _relationship(subject, peer):
    return _eligible(peer) and (peer.role == "admin" or subject.assigned_realtor_id == peer.id)


def _blocked(db, left_id, right_id):
    for model in (Block, SocialBlock):
        if db.query(model.blocker_id).filter(or_(
            and_(model.blocker_id == left_id, model.blocked_id == right_id),
            and_(model.blocker_id == right_id, model.blocked_id == left_id),
        )).first() is not None:
            return True
    return False


def _visibility(db, user_id):
    preference = db.get(Preference, user_id)
    if preference is not None:
        return preference.visibility, preference.version
    profile = db.get(SocialProfile, user_id)
    return ("members" if profile is not None and profile.visibility == "members" else "private"), 0


def _may_discover(db, actor, recipient):
    if actor.id == recipient.id or not _active(recipient) or _blocked(db, actor.id, recipient.id):
        return False
    if actor.role == "admin" or recipient.role == "admin":
        return True
    if actor.role == "buyer" or recipient.role == "buyer":
        return (actor.role != "buyer" or _relationship(actor, recipient)) and (
            recipient.role != "buyer" or _relationship(recipient, actor))
    return _visibility(db, recipient.id)[0] == "members"


def _may_read(conversation, actor, users):
    if actor.role == "admin":
        return True
    if not _primary(conversation, actor):
        return False
    left, right = _pair(conversation, users)
    if conversation.brokerage_subject_id is not None:
        subject = users[conversation.brokerage_subject_id]
        return actor.id == subject.id or (_active(subject) and _relationship(subject, actor))
    peer = right if actor.id == left.id else left
    return peer.role != "buyer" or (_active(peer) and _relationship(peer, actor))


def _send_reason(db, conversation, users):
    left, right = _pair(conversation, users)
    if not _active(left) or not _active(right):
        return "participant_unavailable"
    if _blocked(db, left.id, right.id):
        return "blocked"
    if conversation.brokerage_subject_id is not None:
        subject = users[conversation.brokerage_subject_id]
        peer = right if subject.id == left.id else left
        if not _relationship(subject, peer):
            return "assignment_changed"
    for subject, peer in ((left, right), (right, left)):
        if subject.role == "buyer" and not _relationship(subject, peer):
            return "buyer_relationship_required"
    return None


def _context(db, actor_id, conversation_id, *, write=False):
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        unavailable()
    identities = {actor_id, conversation.user_low_id, conversation.user_high_id}
    users = _users(db, identities, write=write)
    actor = _actor(users, actor_id)
    conversation = db.query(Conversation).filter_by(id=conversation_id).populate_existing().with_for_update(read=not write).one_or_none()
    if conversation is None or not _may_read(conversation, actor, users):
        unavailable()
    return actor, conversation, users


def _member_query(db, actor):
    query = db.query(User).options(load_only(*USER_FIELDS)).filter(
        User.id != actor.id, User.status == "active", User.email_verified.is_(True))
    blocked = or_(
        db.query(Block.blocker_id).filter(or_(and_(Block.blocker_id == actor.id, Block.blocked_id == User.id),
                                             and_(Block.blocked_id == actor.id, Block.blocker_id == User.id))).exists(),
        db.query(SocialBlock.blocker_id).filter(or_(and_(SocialBlock.blocker_id == actor.id, SocialBlock.blocked_id == User.id),
                                                   and_(SocialBlock.blocked_id == actor.id, SocialBlock.blocker_id == User.id))).exists())
    query = query.filter(~blocked)
    if actor.role == "admin":
        return query
    if actor.role == "buyer":
        return query.filter(or_(User.role == "admin", and_(User.id == actor.assigned_realtor_id,
                                                           User.role.in_(("realtor", "admin")))))
    explicit = db.query(Preference.user_id).filter(Preference.user_id == User.id, Preference.visibility == "members").exists()
    has_preference = db.query(Preference.user_id).filter(Preference.user_id == User.id).exists()
    social = db.query(SocialProfile.user_id).filter(SocialProfile.user_id == User.id, SocialProfile.visibility == "members").exists()
    clauses = [User.role == "admin", and_(User.role != "buyer", or_(explicit, and_(~has_preference, social)))]
    if actor.role == "realtor":
        clauses.append(and_(User.role == "buyer", User.assigned_realtor_id == actor.id))
    return query.filter(or_(*clauses))


def _conversation_query(db, actor, view="mine"):
    query = db.query(Conversation)
    contains = or_(Conversation.user_low_id == actor.id, Conversation.user_high_id == actor.id)
    if view == "oversight":
        if actor.role != "admin":
            unavailable()
        return query
    query = query.filter(contains)
    if actor.role == "admin":
        return query
    low, high, subject = aliased(User), aliased(User), aliased(User)
    query = query.join(low, low.id == Conversation.user_low_id).join(high, high.id == Conversation.user_high_id).outerjoin(
        subject, subject.id == Conversation.brokerage_subject_id)
    staff = actor.role in {"realtor", "admin"}
    assigned_subject = and_(subject.status == "active", subject.email_verified.is_(True),
                            subject.assigned_realtor_id == actor.id, literal(staff))
    origin = and_(Conversation.brokerage_subject_id.isnot(None),
                  or_(Conversation.brokerage_subject_id == actor.id, assigned_subject))
    ordinary = and_(Conversation.brokerage_subject_id.is_(None), or_(
        and_(low.id == actor.id, or_(high.role != "buyer", and_(literal(staff), high.status == "active", high.email_verified.is_(True), high.assigned_realtor_id == actor.id))),
        and_(high.id == actor.id, or_(low.role != "buyer", and_(literal(staff), low.status == "active", low.email_verified.is_(True), low.assigned_realtor_id == actor.id)))))
    return query.filter(or_(origin, ordinary))


def _unread(db, conversation, actor):
    if not _primary(conversation, actor):
        return 0
    state = db.get(ReadState, (conversation.id, actor.id))
    boundary = state.last_read_sequence if state else 0
    return db.query(Entry.id).filter(Entry.conversation_id == conversation.id, Entry.sequence > boundary, Entry.author_id != actor.id).count()


def _summary(db, conversation, actor, users, *, review=False):
    observer = not _primary(conversation, actor)
    reason = _send_reason(db, conversation, users)
    if observer and not review:
        reason = "review_required"
    preview = None
    if not observer:
        latest = db.query(Entry.body).filter_by(conversation_id=conversation.id).order_by(Entry.sequence.desc()).first()
        preview = latest[0][:160] if latest else None
    return {"id": str(conversation.id), "participants": [member(user) for user in _pair(conversation, users)],
            "brokerage_subject_id": str(conversation.brokerage_subject_id) if conversation.brokerage_subject_id else None,
            "created_at": conversation.created_at, "last_entry_at": conversation.last_entry_at,
            "last_sequence": conversation.last_sequence, "last_entry_preview": preview,
            "unread_count": _unread(db, conversation, actor), "is_observer": observer,
            "can_send": reason is None, "read_only_reason": reason,
            "policy_version": conversation.policy_version, "admin_oversight": True}


def policy(db, user):
    _actor(_users(db, {user.id}), user.id)
    return {"policy_version": POLICY, "admin_oversight": True, "channels": dict(CHANNELS)}


def preferences(db, user):
    actor = _actor(_users(db, {user.id}), user.id)
    visibility, version = _visibility(db, actor.id)
    return {"visibility": visibility, "version": version, "policy_version": POLICY, "admin_oversight": True}


def recipients(db, user, query, page):
    if any(ord(c)<32 or 127<=ord(c)<=159 for c in query):
        raise HTTPException(422, "Check the recipient search")
    # Candidate metadata never grants access. Recheck every returned member after ordered locks.
    candidate = _member_query(db, user)
    query = query.strip()
    if query:
        choice = User.display_name.icontains(query, autoescape=True)
        if query.isascii() and query.isdecimal() and len(query) <= 12:
            choice = or_(choice, User.user_code == int(query))
        candidate = candidate.filter(choice)
    rows = candidate.order_by(func.lower(User.display_name), User.id).offset((page-1)*PAGE_SIZE).limit(PAGE_SIZE).all()
    users = _users(db, {user.id, *(row.id for row in rows)})
    actor = _actor(users, user.id)
    items = []
    for row in rows:
        recipient = users.get(row.id)
        if recipient is None or not _may_discover(db, actor, recipient):
            continue
        relationship = "admin" if recipient.role == "admin" else "assigned_agent" if actor.role == "buyer" else (
            "assigned_buyer" if recipient.role == "buyer" and recipient.assigned_realtor_id == actor.id else "member")
        items.append({**member(recipient), "relationship": relationship})
    total_query = _member_query(db, actor)
    if query:
        total_query = total_query.filter(choice)
    return {"items": items, "total": total_query.count(), "page": page, "page_size": PAGE_SIZE}


def blocks(db, user, page):
    rows = db.query(Block).filter_by(blocker_id=user.id).order_by(Block.created_at.desc(), Block.blocked_id).offset((page-1)*PAGE_SIZE).limit(PAGE_SIZE).all()
    users = _users(db, {user.id, *(row.blocked_id for row in rows)})
    actor = _actor(users, user.id)
    return {"items": [member(users[row.blocked_id]) for row in rows if row.blocked_id in users],
            "total": db.query(Block).filter_by(blocker_id=actor.id).count(), "page": page, "page_size": PAGE_SIZE}


def conversations(db, user, view, page):
    query = _conversation_query(db, user, view)
    rows = query.order_by(func.coalesce(Conversation.last_entry_at, Conversation.created_at).desc(), Conversation.id.desc()).offset((page-1)*PAGE_SIZE).limit(PAGE_SIZE).all()
    identities = {user.id}
    for row in rows:
        identities.update((row.user_low_id, row.user_high_id))
    users = _users(db, identities)
    actor = _actor(users, user.id)
    if view == "oversight" and actor.role != "admin":
        unavailable()
    items = []
    for row in rows:
        db.refresh(row)
        if _may_read(row, actor, users):
            items.append(_summary(db, row, actor, users))
    return {"items": items, "total": _conversation_query(db, actor, view).count(), "page": page, "page_size": PAGE_SIZE}


def conversation(db, user, conversation_id):
    actor, row, users = _context(db, user.id, conversation_id)
    return _summary(db, row, actor, users)


def unread(db, user):
    # Aggregate within the same scoped query; never count another member's inbox.
    actor = _actor(_users(db, {user.id}), user.id)
    scoped = _conversation_query(db, actor).with_entities(Conversation.id).subquery()
    states = aliased(ReadState)
    counts = db.query(Entry.conversation_id, func.count(Entry.id).label("n")).join(scoped, Entry.conversation_id == scoped.c.id).outerjoin(
        states, and_(states.conversation_id == Entry.conversation_id, states.user_id == actor.id)).filter(
        Entry.author_id != actor.id, Entry.sequence > func.coalesce(states.last_read_sequence, 0)).group_by(Entry.conversation_id).subquery()
    return {"conversations": db.query(counts.c.n).count(), "entries": int(db.query(func.coalesce(func.sum(counts.c.n), 0)).scalar())}


def _source(kind, identifier, recipient_id=None):
    return {"kind": kind, "id": str(identifier), "recipient_id": str(recipient_id) if recipient_id else None}


def _cursor(source, position):
    payload = {"source": source, **position}
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).decode().rstrip("=")


def _position(source, cursor):
    if cursor is None:
        return None
    try:
        if len(cursor)>512 or not cursor.isascii():
            raise ValueError()
        data = json.loads(base64.b64decode(cursor + "="*((-len(cursor))%4), altchars=b"-_", validate=True))
        native = source["kind"] == "native"
        keys = {"source", "sequence"} if native else {"source", "at", "id"}
        if not isinstance(data, dict) or set(data)!=keys or data["source"]!=source:
            raise ValueError()
        position = {key: value for key, value in data.items() if key != "source"}
        if _cursor(source, position) != cursor:
            raise ValueError()
        if native:
            value = data["sequence"]
            if type(value) is not int or not 1<=value<=MAX_SEQUENCE:
                raise ValueError()
            return value
        if not isinstance(data["at"], str) or not isinstance(data["id"], str):
            raise ValueError()
        stamp, identifier = datetime.fromisoformat(data["at"]), UUID(data["id"])
        if stamp.tzinfo is None or stamp.utcoffset() is None or str(identifier)!=data["id"]:
            raise ValueError()
        return stamp.astimezone(timezone.utc), identifier
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, binascii.Error, RecursionError):
        raise HTTPException(422, "Check the history cursor") from None


def _entry(db, row, *, legacy=False):
    author = db.query(User).options(load_only(*USER_FIELDS)).filter_by(id=row.sender_id if legacy else row.author_id).first()
    if author is None:
        unavailable()
    return {"id": str(row.id), "sequence": None if legacy else row.sequence,
            "kind": "message" if legacy else row.kind, "body": row.body,
            "author": member(author, role=None if legacy else row.author_role),
            "context_role": row.sender_role if legacy else None, "created_at": row.created_at,
            "occurred_at": None if legacy else row.occurred_at,
            "reported_direction": None if legacy else row.reported_direction,
            "call_outcome": None if legacy else row.call_outcome,
            "delivery_state": "legacy_recorded" if legacy else "stored_in_app" if row.kind == "message" else "manual_record"}


def _native_page(db, actor, row, users, cursor, review=None):
    source = _source("native", row.id)
    before = _position(source, cursor)
    query = db.query(Entry).filter_by(conversation_id=row.id)
    total = query.count()
    if before is not None:
        query = query.filter(Entry.sequence < before)
    fetched = query.order_by(Entry.sequence.desc()).limit(PAGE_SIZE+1).all()
    rows = fetched[:PAGE_SIZE]
    summary = _summary(db, row, actor, users, review=review is not None)
    return {"source": source, "conversation": summary, "items": [_entry(db, item) for item in rows],
            "total": total, "cursor": cursor, "next_cursor": _cursor(source, {"sequence": rows[-1].sequence}) if len(fetched)>PAGE_SIZE else None,
            "page_size": PAGE_SIZE, "read_only": not summary["can_send"], "read_only_reason": summary["read_only_reason"],
            "policy_version": POLICY, "admin_oversight": True,
            "review_id": str(review.id) if review else None, "review_expires_at": review.expires_at if review else None}


def entries(db, user, conversation_id, cursor):
    actor, row, users = _context(db, user.id, conversation_id)
    if not _primary(row, actor):
        unavailable()
    return _native_page(db, actor, row, users, cursor)


def _legacy_query(db, source):
    model = AwardMessage if source["kind"] == "service_award" else NegotiationMessage
    query = db.query(model).filter(model.request_id == UUID(source["id"]))
    if model is NegotiationMessage:
        query = query.filter(model.recipient_id == UUID(source["recipient_id"]))
    return query, model


def _legacy_page(db, review, cursor):
    source = _source(review.source_kind, review.source_id, review.recipient_id)
    before = _position(source, cursor)
    query, model = _legacy_query(db, source)
    total = query.count()
    if before:
        stamp, identifier = before
        query = query.filter(or_(model.created_at < stamp, and_(model.created_at == stamp, model.id < identifier)))
    fetched = query.order_by(model.created_at.desc(), model.id.desc()).limit(PAGE_SIZE+1).all()
    rows = fetched[:PAGE_SIZE]
    return {"source": source, "conversation": None, "items": [_entry(db, item, legacy=True) for item in rows],
            "total": total, "cursor": cursor,
            "next_cursor": _cursor(source, {"at": rows[-1].created_at.isoformat(), "id": str(rows[-1].id)}) if len(fetched)>PAGE_SIZE else None,
            "page_size": PAGE_SIZE, "read_only": True, "read_only_reason": "legacy_read_only",
            "policy_version": review.policy_version, "admin_oversight": True,
            "review_id": str(review.id), "review_expires_at": review.expires_at}


def _digest(action, body, context):
    normalized = {"policy": POLICY, "action": action, "context": context,
                  "body": body.model_dump(mode="json", exclude={"submission_id"})}
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _receipt(command):
    return {"submission_id": str(command.submission_id), "action": command.action, "target_id": str(command.target_id)}


def _previous(db, actor, body, action, context):
    command = db.get(Command, (actor.id, body.submission_id))
    digest = _digest(action, body, context)
    if command is not None and (command.action != action or command.payload_sha256 != digest):
        conflict()
    return command, digest


def _command(db, actor, body, action, digest, target_id, conversation_id=None, stamp=None):
    command = Command(actor_id=actor.id, submission_id=body.submission_id, action=action,
                      payload_sha256=digest, target_id=target_id, conversation_id=conversation_id, created_at=stamp or now())
    db.add(command)
    db.flush()
    return command


def _audit(db, actor, command, *, identifier=None, source=None, reason=None, expires_at=None,
           flag=None, visibility=None, sequence=None, review_id=None):
    audit = Audit(id=identifier or uuid4(), actor_id=actor.id, submission_id=command.submission_id,
                  actor_role=actor.role, policy_version=POLICY, action=command.action, target_id=command.target_id,
                  conversation_id=command.conversation_id, source_kind=source["kind"] if source else None,
                  source_id=UUID(source["id"]) if source else None,
                  recipient_id=UUID(source["recipient_id"]) if source and source["recipient_id"] else None,
                  reason=reason, expires_at=expires_at, result_flag=flag, result_visibility=visibility,
                  result_sequence=sequence, review_id=review_id, created_at=command.created_at)
    db.add(audit)
    db.flush()
    return audit


def save_preferences(db, user, body):
    actor = _actor(_users(db, {user.id}, write=True), user.id)
    previous, digest = _previous(db, actor, body, "visibility_saved", {})
    if previous:
        return _receipt(previous)
    row = db.get(Preference, actor.id)
    if body.expected_version != (row.version if row else 0):
        conflict()
    if row is None:
        row = Preference(user_id=actor.id, visibility=body.visibility, version=1)
        db.add(row)
    else:
        row.visibility = body.visibility
        row.version += 1
    db.flush()
    command = _command(db, actor, body, "visibility_saved", digest, actor.id)
    _audit(db, actor, command, visibility=body.visibility)
    db.commit()
    return _receipt(command)


def save_block(db, user, target_id, body):
    users = _users(db, {user.id, target_id}, write=True)
    actor = _actor(users, user.id)
    target = users.get(target_id)
    if target is None or target_id == actor.id:
        unavailable()
    previous, digest = _previous(db, actor, body, "block_saved", {"target_id": str(target_id)})
    if previous:
        return _receipt(previous)
    row = db.get(Block, (actor.id, target_id))
    pair = sorted((actor.id, target_id), key=str)
    existing = db.query(Conversation).filter_by(user_low_id=pair[0], user_high_id=pair[1]).first()
    if row is None and not (existing and _may_read(existing, actor, users)) and not _may_discover(db, actor, target):
        unavailable()
    if body.blocked and row is None:
        db.add(Block(blocker_id=actor.id, blocked_id=target_id, created_at=now()))
    elif not body.blocked and row is not None:
        db.delete(row)
    db.flush()
    command = _command(db, actor, body, "block_saved", digest, target_id)
    _audit(db, actor, command, flag=body.blocked)
    db.commit()
    return _receipt(command)


def open_conversation(db, user, body):
    users = _users(db, {user.id, body.recipient_id}, write=True)
    actor = _actor(users, user.id)
    recipient = users.get(body.recipient_id)
    if recipient is None or recipient.id == actor.id:
        unavailable()
    previous, digest = _previous(db, actor, body, "conversation_opened", {})
    pair = sorted((actor.id, recipient.id), key=str)
    row = db.query(Conversation).filter_by(user_low_id=pair[0], user_high_id=pair[1]).populate_existing().with_for_update().first()
    if previous:
        if row is None or previous.target_id != row.id or not _may_read(row, actor, users):
            unavailable()
        return _receipt(previous)
    if row is None:
        if not _may_discover(db, actor, recipient):
            unavailable()
        buyer = next((member for member in (actor, recipient) if member.role == "buyer"), None)
        row = Conversation(id=uuid4(), user_low_id=pair[0], user_high_id=pair[1], created_by_id=actor.id,
                           brokerage_subject_id=buyer.id if buyer else None, policy_version=POLICY,
                           last_sequence=0, last_entry_at=None, created_at=now())
        db.add(row)
        db.flush()
    elif not _may_read(row, actor, users):
        unavailable()
    command = _command(db, actor, body, "conversation_opened", digest, row.id, row.id)
    _audit(db, actor, command)
    db.commit()
    return _receipt(command)


def _review(db, actor, identifier, expected_source=None):
    if actor.role != "admin":
        unavailable()
    review = db.query(Audit).filter_by(id=identifier, actor_id=actor.id, action="review_opened").first()
    if review is None:
        unavailable()
    if expected_source is not None and _source(review.source_kind, review.source_id, review.recipient_id) != expected_source:
        unavailable()
    if review.expires_at <= now():
        raise HTTPException(410, "This audited review has expired. Open a new review to continue.")
    return review


def post_entry(db, user, conversation_id, body):
    actor, row, users = _context(db, user.id, conversation_id, write=True)
    previous, digest = _previous(db, actor, body, "entry_created", {"conversation_id": str(conversation_id)})
    if previous:
        return _receipt(previous)
    review = None
    if body.review_id is not None:
        review = _review(db, actor, body.review_id, _source("native", row.id))
    if not _primary(row, actor) and review is None:
        unavailable()
    if _send_reason(db, row, users) is not None:
        raise HTTPException(409, "This conversation is currently read-only. Refresh its current access.")
    stamp = now()
    if body.occurred_at is not None and body.occurred_at > stamp+timedelta(minutes=5):
        raise HTTPException(422, "Check the manual record time")
    if row.last_sequence >= MAX_SEQUENCE:
        raise HTTPException(409, "This conversation cannot accept another entry")
    row.last_sequence += 1
    row.last_entry_at = stamp
    entry_id = uuid4()
    command = _command(db, actor, body, "entry_created", digest, entry_id, row.id, stamp)
    entry = Entry(id=entry_id, conversation_id=row.id, sequence=row.last_sequence, author_id=actor.id,
                  submission_id=body.submission_id, author_role=actor.role, kind=body.kind, body=body.body,
                  occurred_at=body.occurred_at, reported_direction=body.reported_direction,
                  call_outcome=body.call_outcome, created_at=stamp)
    db.add(entry)
    db.flush()
    _audit(db, actor, command, sequence=entry.sequence, review_id=review.id if review else None)
    db.commit()
    return _receipt(command)


def mark_read(db, user, conversation_id, body):
    actor, row, users = _context(db, user.id, conversation_id, write=True)
    if not _primary(row, actor):
        unavailable()
    previous, digest = _previous(db, actor, body, "read_marked", {"conversation_id": str(conversation_id)})
    if previous:
        return _receipt(previous)
    if body.last_read_sequence > row.last_sequence:
        raise HTTPException(422, "Read cursor exceeds the recorded conversation")
    state = db.get(ReadState, (row.id, actor.id))
    if state is None:
        state = ReadState(conversation_id=row.id, user_id=actor.id,
                          last_read_sequence=body.last_read_sequence, updated_at=now())
        db.add(state)
    elif body.last_read_sequence > state.last_read_sequence:
        state.last_read_sequence = body.last_read_sequence
        state.updated_at = now()
    db.flush()
    command = _command(db, actor, body, "read_marked", digest, row.id, row.id)
    _audit(db, actor, command, sequence=state.last_read_sequence)
    db.commit()
    return _receipt(command)


def legacy_histories(db, user, page):
    actor = _actor(_users(db, {user.id}), user.id)
    if actor.role != "admin":
        unavailable()
    awarded = db.query(literal("service_award").label("kind"), AwardMessage.request_id.label("source_id"),
                       literal(None).cast(NegotiationMessage.recipient_id.type).label("recipient_id"),
                       func.count(AwardMessage.id).label("total_messages"), func.max(AwardMessage.created_at).label("last_entry_at")).group_by(AwardMessage.request_id)
    negotiations = db.query(literal("service_negotiation").label("kind"), NegotiationMessage.request_id.label("source_id"),
                            NegotiationMessage.recipient_id.label("recipient_id"), func.count(NegotiationMessage.id).label("total_messages"),
                            func.max(NegotiationMessage.created_at).label("last_entry_at")).group_by(NegotiationMessage.request_id, NegotiationMessage.recipient_id)
    sources = awarded.union_all(negotiations).subquery()
    rows = db.query(sources).order_by(sources.c.last_entry_at.desc(), sources.c.kind, sources.c.source_id, sources.c.recipient_id).offset((page-1)*PAGE_SIZE).limit(PAGE_SIZE).all()
    return {"items": [{"source": _source(row.kind, row.source_id, row.recipient_id),
                       "total_messages": row.total_messages, "last_entry_at": row.last_entry_at} for row in rows],
            "total": db.query(sources).count(), "page": page, "page_size": PAGE_SIZE,
            "policy_version": POLICY, "admin_oversight": True}


def open_review(db, user, body):
    source = body.source.model_dump(mode="json")
    if source["kind"] == "native":
        actor, conversation, users = _context(db, user.id, body.source.id, write=True)
    else:
        actor = _actor(_users(db, {user.id}, write=True), user.id)
        conversation = None
    if actor.role != "admin":
        unavailable()
    previous, digest = _previous(db, actor, body, "review_opened", {})
    if previous:
        return _receipt(previous)
    if source["kind"] != "native":
        query, model = _legacy_query(db, source)
        if query.with_entities(model.id).first() is None:
            unavailable()
    stamp, review_id = now(), uuid4()
    command = _command(db, actor, body, "review_opened", digest, review_id,
                       conversation.id if conversation else None, stamp)
    _audit(db, actor, command, identifier=review_id, source=source, reason=body.reason,
           expires_at=stamp+timedelta(minutes=10))
    db.commit()
    return _receipt(command)


def review_entries(db, user, review_id, cursor):
    review = db.query(Audit).filter_by(id=review_id, actor_id=user.id, action="review_opened").first()
    if review is None:
        unavailable()
    if review.source_kind == "native":
        actor, conversation, users = _context(db, user.id, review.source_id)
        review = _review(db, actor, review_id)
        return _native_page(db, actor, conversation, users, cursor, review)
    actor = _actor(_users(db, {user.id}), user.id)
    review = _review(db, actor, review_id)
    return _legacy_page(db, review, cursor)


def command_receipt(db, user, submission_id):
    command = db.get(Command, (user.id, submission_id))
    if command is None:
        unavailable()
    if command.conversation_id is not None:
        actor, conversation, users = _context(db, user.id, command.conversation_id)
    else:
        actor = _actor(_users(db, {user.id}), user.id)
    if command.action == "review_opened" and actor.role != "admin":
        unavailable()
    return _receipt(command)
