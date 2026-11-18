"""Explicit group grants and retained group content; no project or provider actions."""
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, NAMESPACE_URL, uuid5
from fastapi import HTTPException
from sqlalchemy import and_, or_
from models.user import User
from models.property_network import NetworkBlock
from models.social_groups import (NetworkGroup as Group, NetworkGroupMembership as Membership,
    NetworkGroupInviteLink as Link, NetworkGroupPost as Post, NetworkGroupComment as Comment,
    NetworkGroupMedia as Media, NetworkGroupAction as Action)
from utils import private_network_media as storage

PAGE_SIZE = 30
MAX_OWNED_GROUPS = 50
MAX_GROUP_MEMBERS = 1000
MAX_LIVE_LINKS = 20
MAX_PHOTOS_PER_USER = 5000


def now():
    return datetime.now(timezone.utc)


def unavailable():
    raise HTTPException(404, "Group record not found")


def conflict():
    raise HTTPException(409, "This group record changed. Reload before saving")


def eligible(user):
    if user is None or user.status != "active" or user.email_verified is not True or not user.user_code:
        raise HTTPException(403, "An active verified member account is required")
    return user


def _member(db, group, user_id):
    return db.query(Membership).filter_by(group_id=group.id, user_id=user_id).first()


def _state(row):
    if row is None:
        return "none"
    return "expired" if row.state == "invited" and row.invite_expires_at <= now() else row.state


def _blocked(db, first, second):
    return first != second and db.query(NetworkBlock).filter(or_(
        and_(NetworkBlock.blocker_id == first, NetworkBlock.blocked_id == second),
        and_(NetworkBlock.blocker_id == second, NetworkBlock.blocked_id == first))).first() is not None


def _excluded(db, user):
    return db.query(NetworkBlock.blocked_id).filter_by(blocker_id=user.id).union(
        db.query(NetworkBlock.blocker_id).filter_by(blocked_id=user.id))


def _role(db, user, group):
    if group.owner_id == user.id:
        return "owner"
    member = _member(db, group, user.id)
    return member.role if member and member.state == "active" else None


def _owner_active(db, group):
    owner = db.get(User, group.owner_id)
    return owner is not None and owner.status == "active" and owner.email_verified is True


def _can_moderate(db, user, group):
    return user.role == "admin" or _role(db, user, group) in {"owner", "admin", "moderator"}


def _can_manage(db, user, group):
    return _role(db, user, group) in {"owner", "admin"}


def _require_write(db, group):
    if group.status != "active" or not _owner_active(db, group):
        raise HTTPException(409, "This group is closed for new activity")


def _require_member(db, user, group, *, allow_closed=False):
    if not allow_closed:
        _require_write(db, group)
    row = _member(db, group, user.id)
    if not _role(db, user, group) or (row and row.state == "blocked") or _blocked(db, user.id, group.owner_id):
        unavailable()


def group_for(db, user, identity):
    eligible(user)
    group = db.get(Group, identity)
    if group is None:
        unavailable()
    member = _member(db, group, user.id)
    if user.role != "admin" and ((member and member.state == "blocked") or _blocked(db, user.id, group.owner_id)):
        unavailable()
    pending = _state(member) in {"requested", "invited"}
    registered_header = group.status == "active" and group.visibility == "network" and _owner_active(db, group)
    if user.role != "admin" and not (_role(db, user, group) or pending or registered_header):
        unavailable()
    return group


def _locked(db, user, identity, target_ids=()):
    # Lock users in UUID order before the group and its child records. No new
    # property/project grants or cross-domain locks are acquired here.
    hint = db.get(Group, identity)
    if hint is None:
        unavailable()
    ids = set(target_ids) | {user.id, hint.owner_id}
    rows = db.query(User).filter(User.id.in_(ids)).order_by(User.id).populate_existing().with_for_update(key_share=True).all()
    indexed = {row.id: row for row in rows}
    if set(indexed) != ids:
        unavailable()
    user = eligible(indexed[user.id])
    group = db.query(Group).filter_by(id=identity).populate_existing().with_for_update().one()
    return user, group


def _version(row, expected):
    if (row.version if row else 0) != expected:
        conflict()


def _digest(body, operation, target):
    values = body.model_dump(mode="json") if hasattr(body, "model_dump") else dict(body)
    if "token" in values:
        values["token_sha256"] = token_hash(values.pop("token"))
    data = {"operation": operation, "target": str(target), "input": values}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _page(query, number, mapper):
    return {"items": [mapper(row) for row in query.offset((number - 1) * PAGE_SIZE).limit(PAGE_SIZE)],
            "total": query.count(), "page": number, "page_size": PAGE_SIZE}


def membership_view(db, row):
    user = db.get(User, row.user_id)
    return {"id": str(row.id), "group_id": str(row.group_id), "user_code": user.user_code if user else None,
            "member_alias": row.member_alias, "role": row.role, "state": _state(row), "version": row.version,
            "invite_expires_at": row.invite_expires_at, "updated_at": row.updated_at}


def media_view(row):
    return {"id": str(row.id), "group_id": str(row.group_id), "url": f"/property-network/group-media/{row.id}",
            "content_type": "image/webp", "bytes": row.byte_count, "sha256": row.sha256,
            "width": row.width, "height": row.height, "kind": row.kind}


def group_view(db, user, group):
    group_for(db, user, group.id)
    role = _role(db, user, group)
    member = _member(db, group, user.id)
    writable = group.status == "active" and _owner_active(db, group)
    can_feed = bool(role or user.role == "admin" or (writable and group.visibility == "network"))
    managers = role in {"owner", "admin"}
    unblocked_manager = bool(role and not _blocked(db, user.id, group.owner_id))
    image = db.get(Media, group.image_media_id) if group.image_media_id else None
    count = db.query(Membership).join(User, User.id == Membership.user_id).filter(
        Membership.group_id == group.id, Membership.state == "active", User.status == "active", User.email_verified.is_(True)).count() + 1
    summary = None
    if managers:
        summary = {"network_posts": db.query(Post).filter_by(group_id=group.id, created_audience="network", status="visible").count(),
                   "private_posts": db.query(Post).filter_by(group_id=group.id, created_audience="private").count()}
    return {"id": str(group.id), "name": group.name, "description": group.description, "purpose": group.purpose,
            "area_label": group.area_label, "visibility": group.visibility, "join_policy": group.join_policy,
            "post_policy": group.post_policy, "comments_enabled": group.comments_enabled, "status": group.status,
            "version": group.version, "owner_alias": group.owner_alias, "my_role": role,
            "membership": membership_view(db, member) if member else None, "member_count": count,
            "image": media_view(image) if image and can_feed else None, "publication_summary": summary,
            "can_read_feed": can_feed, "can_read_members": bool(role or user.role == "admin"),
            "can_manage": managers and writable, "can_moderate": _can_moderate(db, user, group),
            "can_revoke_access": unblocked_manager and role in {"owner", "admin", "moderator"},
            "can_revoke_links": unblocked_manager and managers,
            "can_post": bool(role and writable), "can_join": not role and writable and _state(member) not in {"blocked", "invited", "requested"},
            "can_publish": role == "owner" and writable, "can_archive": role == "owner" or user.role == "admin",
            "created_at": group.created_at, "updated_at": group.updated_at}


def _current(db, user, action):
    try:
        if action.target_kind == "membership":
            row = db.get(Membership, action.target_id)
            if row and row.user_id == user.id:
                return membership_view(db, row)
            group = group_for(db, user, action.group_id)
            return membership_view(db, row) if row and _can_moderate(db, user, group) else None
        if action.target_kind == "group":
            return group_view(db, user, db.get(Group, action.target_id))
        if action.target_kind == "link":
            group = group_for(db, user, action.group_id)
            return link_view(db.get(Link, action.target_id)) if _can_manage(db, user, group) else None
        if action.target_kind == "post":
            return post_view(db, user, post_for(db, user, action.target_id))
        if action.target_kind == "comment":
            row = comment_for(db, user, action.target_id)
            return comment_view(db, user, row)
        if action.target_kind == "media":
            row = db.get(Media, action.target_id)
            authorize_media(db, user, row)
            return media_view(row)
    except HTTPException as error:
        if error.status_code not in {403, 404}:
            raise
    return None


def receipt(db, user, row):
    return {"submission_id": str(row.submission_id), "operation": row.operation, "target_id": str(row.target_id),
            "applied_at": row.created_at, "result_versions": row.result_versions, "current": _current(db, user, row)}


def _replay(db, user, body, operation, target):
    row = db.query(Action).filter_by(actor_id=user.id, submission_id=body.submission_id).first()
    if row:
        if row.creation_sha256 != _digest(body, operation, target):
            raise HTTPException(409, "This submission identifier was already used")
        return receipt(db, user, row)
    return None


def _finish(db, user, group, body, operation, kind, row, digest_target, *, reason="", link_id=None):
    db.flush()
    action = Action(group_id=group.id, actor_id=user.id, submission_id=body.submission_id, operation=operation,
                    target_kind=kind, target_id=row.id, creation_sha256=_digest(body, operation, digest_target),
                    membership_id=row.id if kind == "membership" else None, link_id=link_id or (row.id if kind == "link" else None),
                    result_versions={"version": getattr(row, "version", 1)}, reason=reason)
    db.add(action)
    db.commit()
    db.refresh(action)
    return receipt(db, user, action)


def own_submission(db, user, identity):
    eligible(user)
    row = db.query(Action).filter_by(actor_id=user.id, submission_id=identity).first()
    if row is None:
        unavailable()
    return receipt(db, user, row)


def listed(db, user, view, term, number):
    eligible(user)
    own = db.query(Membership.group_id).filter(Membership.user_id == user.id,
        Membership.state.in_(["active", "requested", "invited"]))
    banned = db.query(Membership.group_id).filter_by(user_id=user.id, state="blocked")
    query = db.query(Group)
    if view == "mine":
        query = query.filter(or_(Group.owner_id == user.id, Group.id.in_(own)))
    else:
        query = query.join(User, User.id == Group.owner_id).filter(Group.visibility == "network", Group.status == "active",
            User.status == "active", User.email_verified.is_(True))
    query = query.filter(Group.id.notin_(banned), Group.owner_id.notin_(_excluded(db, user)))
    if term:
        query = query.filter(or_(Group.name.icontains(term, autoescape=True), Group.area_label.icontains(term, autoescape=True)))
    # Expired invitation rows never grant an otherwise hidden header.
    rows = query.order_by(Group.updated_at.desc(), Group.id)
    if view == "mine":
        rows = rows.filter(or_(Group.owner_id == user.id, Group.id.in_(db.query(Membership.group_id).filter(
            Membership.user_id == user.id, or_(Membership.state.in_(["active", "requested"]),
                and_(Membership.state == "invited", Membership.invite_expires_at > now()))))))
    return _page(rows, number, lambda group: group_view(db, user, group))


def create_group(db, user, body):
    user = eligible(db.query(User).filter_by(id=user.id).populate_existing().with_for_update(key_share=True).one())
    replay = _replay(db, user, body, "group_create", user.id)
    if replay:
        return replay
    if db.query(Group).filter_by(owner_id=user.id, status="active").count() >= MAX_OWNED_GROUPS:
        raise HTTPException(409, "Your active group limit has been reached")
    group = Group(owner_id=user.id, submission_id=body.submission_id, creation_sha256=_digest(body, "group_create", user.id),
                  **body.model_dump(exclude={"submission_id"}))
    db.add(group)
    db.flush()
    return _finish(db, user, group, body, "group_create", "group", group, user.id)


def edit_group(db, user, identity, body):
    user, group = _locked(db, user, identity)
    replay = _replay(db, user, body, "group_edit", identity)
    if replay:
        return replay
    _require_member(db, user, group)
    if not _can_manage(db, user, group):
        raise HTTPException(403, "Only group managers can change these settings")
    _version(group, body.expected_version)
    if group.visibility == "private" and body.visibility == "network":
        if group.owner_id != user.id or not body.publication_acknowledged:
            raise HTTPException(422, "The group owner must review registered-network publication")
    if body.image_media_id:
        image = db.query(Media).filter_by(id=body.image_media_id).with_for_update().first()
        if image is None or image.group_id != group.id or image.kind != "group_image":
            raise HTTPException(422, "Choose an image uploaded for this group")
    for key, value in body.model_dump(exclude={"submission_id", "expected_version", "publication_acknowledged"}).items():
        setattr(group, key, value)
    group.version += 1
    group.updated_at = now()
    return _finish(db, user, group, body, "group_edit", "group", group, identity)


def archive(db, user, identity, body):
    user, group = _locked(db, user, identity)
    replay = _replay(db, user, body, "archive", identity)
    if replay:
        return replay
    if user.id != group.owner_id and user.role != "admin":
        raise HTTPException(403, "Only the owner or platform moderator can archive a group")
    _version(group, body.expected_version)
    group.status = "archived"
    group.version += 1
    group.updated_at = now()
    return _finish(db, user, group, body, "archive", "group", group, identity, reason=body.reason)


def own_memberships(db, user, state, number):
    eligible(user)
    query = db.query(Membership).filter_by(user_id=user.id)
    if state != "all":
        query = query.filter(Membership.state == state)
    return _page(query.order_by(Membership.updated_at.desc(), Membership.id), number, lambda row: membership_view(db, row))


def members(db, user, identity, state, number):
    group = group_for(db, user, identity)
    if not _role(db, user, group) and user.role != "admin":
        unavailable()
    if state in {"pending", "all"} and not _can_moderate(db, user, group):
        raise HTTPException(403, "Only group moderators can review membership requests")
    query = db.query(Membership).filter_by(group_id=identity)
    if state == "pending":
        query = query.filter(or_(Membership.state == "requested", and_(Membership.state == "invited", Membership.invite_expires_at > now())))
    elif state == "active":
        query = query.filter_by(state="active")
    if not _can_moderate(db, user, group):
        query = query.filter(Membership.user_id.notin_(_excluded(db, user)))
    result = _page(query.order_by(Membership.created_at, Membership.id), number, lambda row: membership_view(db, row))
    result["owner"] = {"user_code": db.get(User, group.owner_id).user_code, "member_alias": group.owner_alias, "role": "owner"}
    return result


def _capacity(db, group):
    if db.query(Membership).filter_by(group_id=group.id, state="active").count() + 1 >= MAX_GROUP_MEMBERS:
        raise HTTPException(409, "This group's member limit has been reached")


def _pending_capacity(db, group):
    pending = db.query(Membership).filter(Membership.group_id == group.id, or_(Membership.state == "requested",
        and_(Membership.state == "invited", Membership.invite_expires_at > now()))).count()
    if pending >= MAX_GROUP_MEMBERS:
        raise HTTPException(409, "This group's pending invitation limit has been reached")


def _new_membership(db, group, user_id, row, state, alias=None):
    if row is None:
        row = Membership(group_id=group.id, user_id=user_id, role="member", state=state, member_alias=alias, version=1)
        db.add(row)
    else:
        row.state = state
        row.version += 1
        if alias is not None:
            row.member_alias = alias
    row.updated_at = now()
    return row


def own_membership_action(db, user, identity, body):
    user, group = _locked(db, user, identity)
    operation = body.action
    replay = _replay(db, user, body, operation, identity)
    if replay:
        return replay
    withdrawing = operation in {"cancel", "decline_invite", "leave"}
    if not withdrawing:
        group_for(db, user, identity)
        _require_write(db, group)
    if user.id == group.owner_id:
        raise HTTPException(409, "The group owner can archive the group instead of leaving")
    row = db.query(Membership).filter_by(group_id=identity, user_id=user.id).populate_existing().with_for_update().first()
    _version(row, body.expected_membership_version)
    state = _state(row)
    if state == "blocked" or not withdrawing and _blocked(db, user.id, group.owner_id):
        unavailable()
    new_state = None
    if operation in {"join", "request"}:
        if state in {"active", "invited", "requested"}:
            conflict()
        if not body.member_alias:
            raise HTTPException(422, "Choose the name you will share with this group")
        if (operation == "join") != (group.join_policy == "open"):
            raise HTTPException(422, "Use this group's current join flow")
        new_state = "active" if operation == "join" else "requested"
    elif operation in {"accept_invite", "decline_invite"}:
        if state != "invited":
            conflict()
        if operation == "accept_invite" and not body.member_alias:
            raise HTTPException(422, "Choose the name you will share with this group")
        new_state = "active" if operation == "accept_invite" else "declined"
    elif operation == "cancel" and state == "requested":
        new_state = "cancelled"
    elif operation == "leave" and state == "active":
        new_state = "left"
    if new_state is None:
        conflict()
    if new_state == "active":
        _capacity(db, group)
    elif new_state == "requested":
        _pending_capacity(db, group)
    row = _new_membership(db, group, user.id, row, new_state,
                          body.member_alias if operation in {"join", "request", "accept_invite"} else None)
    if new_state != "invited":
        row.invite_expires_at = None
    if new_state in {"active", "requested"}:
        row.role = "member"
    return _finish(db, user, group, body, operation, "membership", row, identity)


def manage_member(db, user, identity, code, body):
    hint = db.query(User).filter_by(user_code=code).first()
    if hint is None:
        unavailable()
    user, group = _locked(db, user, identity, (hint.id,))
    replay = _replay(db, user, body, "approve_member" if body.action == "approve" else
                     "decline_member" if body.action == "decline" else body.action, f"{identity}:{code}")
    if replay:
        return replay
    row = db.query(Membership).filter_by(group_id=identity, user_id=hint.id).populate_existing().with_for_update().first()
    ranks = {"member": 1, "moderator": 2, "admin": 3}
    demoting = (body.action == "set_role" and row is not None and row.state == "active"
                and ranks[body.role] < ranks[row.role])
    revoke_only = body.action in {"remove", "block", "revoke_invite"} or demoting
    _require_member(db, user, group, allow_closed=revoke_only)
    role = _role(db, user, group)
    target = db.get(User, hint.id)
    if role not in {"owner", "admin", "moderator"} or target.id in {user.id, group.owner_id}:
        raise HTTPException(403, "This member action is unavailable")
    _version(row, body.expected_membership_version)
    state = _state(row)
    if row and row.role == "admin" and role != "owner" or row and row.role == "moderator" and role == "moderator":
        raise HTTPException(403, "You cannot change this group role")
    operation = body.action
    if operation in {"invite", "set_role"} and role not in {"owner", "admin"}:
        raise HTTPException(403, "Only group administrators can invite or change roles")
    if operation == "invite":
        eligible(target)
        if _blocked(db, user.id, target.id) or _blocked(db, group.owner_id, target.id):
            unavailable()
        if state in {"active", "requested", "invited", "blocked"}:
            conflict()
        _pending_capacity(db, group)
        row = _new_membership(db, group, target.id, row, "invited")
        row.member_alias, row.role = None, "member"
        row.invited_by_id = user.id
        row.invite_expires_at = now() + timedelta(days=body.expires_in_days)
    elif operation == "approve":
        eligible(target)
        if state != "requested" or _blocked(db, group.owner_id, target.id):
            conflict()
        _capacity(db, group)
        row = _new_membership(db, group, target.id, row, "active")
        row.role = "member"
    elif operation in {"decline", "revoke_invite"}:
        if state != ("requested" if operation == "decline" else "invited"):
            conflict()
        row = _new_membership(db, group, target.id, row, "declined" if operation == "decline" else "cancelled")
        row.invite_expires_at = None
    elif operation == "set_role":
        if state != "active" or body.role == "admin" and role != "owner":
            raise HTTPException(403, "Only the owner can appoint group administrators")
        row.role = body.role
        row.version += 1
        row.updated_at = now()
    elif operation in {"remove", "block", "unblock"}:
        if operation == "remove" and state != "active" or operation == "unblock" and state != "blocked":
            conflict()
        if operation == "block" and state == "blocked":
            conflict()
        row = _new_membership(db, group, target.id, row, "blocked" if operation == "block" else "removed")
        row.role = "member"
        row.invite_expires_at = None
    else:
        conflict()
    row.decided_by_id = user.id
    operation = "approve_member" if operation == "approve" else "decline_member" if operation == "decline" else operation
    return _finish(db, user, group, body, operation, "membership", row, f"{identity}:{code}", reason=body.reason)


def token_hash(token):
    try:
        value = base64.urlsafe_b64decode(token + "=")
        if len(value) != 32 or base64.urlsafe_b64encode(value).decode().rstrip("=") != token:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid group invitation") from None
    return hashlib.sha256(value).hexdigest()


def link_view(row):
    state = "revoked" if row.revoked_at else "expired" if row.expires_at <= now() else "exhausted" if row.use_count >= row.max_uses else "active"
    return {"id": str(row.id), "group_id": str(row.group_id), "expires_at": row.expires_at, "revoked_at": row.revoked_at,
            "max_uses": row.max_uses, "use_count": row.use_count, "version": row.version, "state": state,
            "created_at": row.created_at}


def links(db, user, identity, number):
    group = group_for(db, user, identity)
    if not _can_manage(db, user, group):
        raise HTTPException(403, "Only group administrators can manage invitation links")
    return _page(db.query(Link).filter_by(group_id=identity).order_by(Link.created_at.desc(), Link.id), number, link_view)


def create_link(db, user, identity, body):
    user, group = _locked(db, user, identity)
    replay = _replay(db, user, body, "link_create", identity)
    if replay:
        return replay
    _require_member(db, user, group)
    if not _can_manage(db, user, group):
        raise HTTPException(403, "Only group administrators can create invitation links")
    instant = now()
    if db.query(Link).filter(Link.group_id == identity, Link.revoked_at.is_(None), Link.expires_at > instant,
                             Link.use_count < Link.max_uses).count() >= MAX_LIVE_LINKS:
        raise HTTPException(409, "This group's invitation link limit has been reached")
    digest = token_hash(body.token)
    if db.query(Link).filter_by(token_sha256=digest).first():
        raise HTTPException(409, "Choose a fresh invitation link")
    row = Link(group_id=identity, created_by_id=user.id, token_sha256=digest,
               expires_at=instant + timedelta(days=body.expires_in_days), max_uses=body.max_uses, created_at=instant)
    db.add(row)
    return _finish(db, user, group, body, "link_create", "link", row, identity)


def revoke_link(db, user, identity, link_id, body):
    user, group = _locked(db, user, identity)
    replay = _replay(db, user, body, "link_revoke", link_id)
    if replay:
        return replay
    _require_member(db, user, group, allow_closed=True)
    if not _can_manage(db, user, group):
        raise HTTPException(403, "Only group administrators can revoke invitation links")
    row = db.query(Link).filter_by(id=link_id, group_id=identity).populate_existing().with_for_update().first()
    if row is None:
        unavailable()
    _version(row, body.expected_version)
    if row.revoked_at is not None:
        conflict()
    row.revoked_at = now()
    row.version += 1
    return _finish(db, user, group, body, "link_revoke", "link", row, link_id)


def _valid_link(db, user, token, *, lock=False):
    eligible(user)
    hint = db.query(Link).filter_by(token_sha256=token_hash(token)).first()
    if hint is None:
        unavailable()
    if lock:
        user, group = _locked(db, user, hint.group_id)
        row = db.query(Link).filter_by(id=hint.id).populate_existing().with_for_update().one()
    else:
        row, group = hint, db.get(Group, hint.group_id)
    member = _member(db, group, user.id)
    if (group.status != "active" or not _owner_active(db, group) or row.revoked_at is not None
            or row.expires_at <= now() or row.use_count >= row.max_uses
            or member and member.state == "blocked" or _blocked(db, user.id, group.owner_id)):
        unavailable()
    return user, group, row


def resolve_link(db, user, body):
    user, group, link = _valid_link(db, user, body.token)
    return {"group_id": str(group.id), "name": group.name, "description": group.description,
            "visibility": group.visibility, "expires_at": link.expires_at,
            "already_member": bool(_role(db, user, group)), "grants_role": "member"}


def accept_link(db, user, body):
    # A consumed or revoked link can still resolve its actor's prior receipt,
    # but that receipt only returns current authorized membership metadata.
    hint = db.query(Link).filter_by(token_sha256=token_hash(body.token)).first()
    if hint is None:
        unavailable()
    user, group = _locked(db, user, hint.group_id)
    replay = _replay(db, user, body, "link_accept", hint.id)
    if replay:
        return replay
    user, group, link = _valid_link(db, user, body.token, lock=True)
    if db.query(Action).filter_by(actor_id=user.id, link_id=link.id, operation="link_accept").first():
        raise HTTPException(409, "This invitation was already used by your account")
    if user.id == group.owner_id:
        raise HTTPException(409, "You already own this group")
    row = db.query(Membership).filter_by(group_id=group.id, user_id=user.id).populate_existing().with_for_update().first()
    if row and row.state == "active":
        # An explicit no-op still has a receipt; it consumes no link use.
        return _finish(db, user, group, body, "link_accept", "membership", row, link.id, link_id=link.id)
    _capacity(db, group)
    row = _new_membership(db, group, user.id, row, "active", body.member_alias)
    row.role = "member"
    row.invite_expires_at = None
    link.use_count += 1
    return _finish(db, user, group, body, "link_accept", "membership", row, link.id, link_id=link.id)


def _author(db, group, author_id):
    user = db.get(User, author_id)
    member = _member(db, group, author_id)
    alias = group.owner_alias if group.owner_id == author_id else member.member_alias if member else None
    return {"user_code": user.user_code if user else None, "display_name": alias or "Former member"}


def post_for(db, user, identity):
    # A write can have loaded its parent hint before waiting for the group
    # lock. Re-read moderation state rather than trusting that identity map.
    row = db.query(Post).filter_by(id=identity).populate_existing().first()
    if row is None:
        unavailable()
    group = group_for(db, user, row.group_id)
    privileged = _can_moderate(db, user, group)
    if not privileged:
        if _blocked(db, user.id, row.author_id) or row.status != "visible" and row.author_id != user.id:
            unavailable()
        if not _role(db, user, group) and not (group.status == "active" and group.visibility == "network"
                                               and row.created_audience == "network" and row.status == "visible"):
            unavailable()
    return row


def post_view(db, user, row):
    group = group_for(db, user, row.group_id)
    return {"id": str(row.id), "group_id": str(row.group_id), "author": _author(db, group, row.author_id),
            "body": row.body, "media_ids": row.media_ids, "created_audience": row.created_audience,
            "status": row.status, "version": row.version, "created_at": row.created_at,
            "can_hide": user.id == row.author_id or _can_moderate(db, user, group),
            "can_approve": row.status == "pending" and _can_moderate(db, user, group),
            "can_comment": bool(_role(db, user, group) and group.status == "active" and _owner_active(db, group)
                                and group.comments_enabled and row.status == "visible")}


def posts(db, user, identity, number):
    group = group_for(db, user, identity)
    if not group_view(db, user, group)["can_read_feed"]:
        unavailable()
    query = db.query(Post).filter_by(group_id=identity)
    if not _can_moderate(db, user, group):
        query = query.filter(Post.author_id.notin_(_excluded(db, user)), or_(Post.status == "visible", Post.author_id == user.id))
    if not _role(db, user, group) and user.role != "admin":
        query = query.filter_by(status="visible", created_audience="network")
    return _page(query.order_by(Post.created_at.desc(), Post.id), number, lambda row: post_view(db, user, row))


def create_post(db, user, identity, body):
    user, group = _locked(db, user, identity)
    replay = _replay(db, user, body, "post_create", identity)
    if replay:
        return replay
    _require_member(db, user, group)
    _version(group, body.expected_group_version)
    photos = []
    for media_id in sorted(body.media_ids, key=str):
        photo = db.query(Media).filter_by(id=media_id).populate_existing().with_for_update().first()
        if (photo is None or photo.group_id != identity or photo.kind != "post"
                or photo.uploader_id != user.id or photo.post_id is not None):
            raise HTTPException(422, "Choose your own unattached photos uploaded to this group")
        photos.append(photo)
    role = _role(db, user, group)
    row = Post(group_id=identity, author_id=user.id, submission_id=body.submission_id, body=body.body,
               media_ids=[str(value) for value in body.media_ids], created_audience=group.visibility,
               status="pending" if group.post_policy == "moderated" and role == "member" else "visible")
    db.add(row)
    db.flush()
    for photo in photos:
        photo.post_id = row.id
    return _finish(db, user, group, body, "post_create", "post", row, identity)


def moderate_post(db, user, identity, body):
    hint = db.get(Post, identity)
    if hint is None:
        unavailable()
    user, group = _locked(db, user, hint.group_id)
    operation = "post_approve" if body.action == "approve" else "post_hide"
    replay = _replay(db, user, body, operation, identity)
    if replay:
        return replay
    post_for(db, user, identity)
    row = db.query(Post).filter_by(id=identity).populate_existing().with_for_update().one()
    _version(row, body.expected_version)
    moderator = _can_moderate(db, user, group)
    if body.action == "approve":
        if not moderator or row.status != "pending":
            raise HTTPException(403, "This post cannot be approved by you")
        _require_write(db, group)
        row.status = "visible"
    elif not moderator and row.author_id != user.id:
        raise HTTPException(403, "This post cannot be hidden by you")
    else:
        row.status = "hidden"
    row.version += 1
    return _finish(db, user, group, body, operation, "post", row, identity, reason=body.reason)


def comment_view(db, user, row):
    post = db.get(Post, row.post_id)
    group = group_for(db, user, post.group_id)
    return {"id": str(row.id), "post_id": str(row.post_id), "author": _author(db, group, row.author_id),
            "body": row.body, "media_id": str(row.media_id) if row.media_id else None, "hidden": row.hidden,
            "version": row.version, "created_at": row.created_at,
            "can_hide": row.author_id == user.id or _can_moderate(db, user, group)}


def comment_for(db, user, identity):
    row = db.query(Comment).filter_by(id=identity).populate_existing().first()
    if row is None:
        unavailable()
    post = post_for(db, user, row.post_id)
    group = group_for(db, user, post.group_id)
    if not _can_moderate(db, user, group) and (
            _blocked(db, user.id, row.author_id) or row.hidden and row.author_id != user.id):
        unavailable()
    return row


def comments(db, user, identity, number):
    post = post_for(db, user, identity)
    group = group_for(db, user, post.group_id)
    query = db.query(Comment).filter_by(post_id=identity)
    if not _can_moderate(db, user, group):
        query = query.filter(Comment.author_id.notin_(_excluded(db, user)), or_(Comment.hidden.is_(False), Comment.author_id == user.id))
    return _page(query.order_by(Comment.created_at, Comment.id), number, lambda row: comment_view(db, user, row))


def create_comment(db, user, identity, body):
    hint = db.get(Post, identity)
    if hint is None:
        unavailable()
    user, group = _locked(db, user, hint.group_id)
    replay = _replay(db, user, body, "comment_create", identity)
    if replay:
        return replay
    _require_member(db, user, group)
    post = post_for(db, user, identity)
    if post.status != "visible" or not group.comments_enabled:
        raise HTTPException(403, "Comments are closed for this post")
    if body.media_id:
        photo = db.get(Media, body.media_id)
        if photo is None or photo.group_id != group.id or photo.post_id != post.id:
            raise HTTPException(422, "Choose a photo belonging to this group post")
    row = Comment(post_id=identity, author_id=user.id, submission_id=body.submission_id, body=body.body, media_id=body.media_id)
    db.add(row)
    return _finish(db, user, group, body, "comment_create", "comment", row, identity)


def hide_comment(db, user, identity, body):
    hint = db.get(Comment, identity)
    if hint is None:
        unavailable()
    parent = db.get(Post, hint.post_id)
    user, group = _locked(db, user, parent.group_id)
    replay = _replay(db, user, body, "comment_hide", identity)
    if replay:
        return replay
    post_for(db, user, parent.id)
    row = db.query(Comment).filter_by(id=identity).populate_existing().with_for_update().one()
    if row.author_id != user.id and not _can_moderate(db, user, group):
        raise HTTPException(403, "This comment cannot be hidden by you")
    _version(row, body.expected_version)
    row.hidden = True
    row.version += 1
    return _finish(db, user, group, body, "comment_hide", "comment", row, identity, reason=body.reason)


def authorize_media(db, user, row):
    if row is None:
        unavailable()
    group = group_for(db, user, row.group_id)
    if row.kind == "group_image" and group.image_media_id == row.id:
        if not (_role(db, user, group) or user.role == "admin" or (
                group.status == "active" and group.visibility == "network" and _owner_active(db, group))):
            unavailable()
        return row
    if row.post_id:
        post_for(db, user, row.post_id)
    elif row.uploader_id != user.id or not _role(db, user, group):
        unavailable()
    # No uploader exception bypasses group_for or post_for. Leaving, removal,
    # bans and audience withdrawal affect the very next private-byte request.
    return row


def media_bytes(db, user, identity):
    return storage.read_image(authorize_media(db, user, db.get(Media, identity)))


def upload_media(db, user, identity, kind, submission_id, raw):
    user, group = _locked(db, user, identity)
    _require_member(db, user, group)
    if kind == "group_image" and not _can_manage(db, user, group):
        raise HTTPException(403, "Only group administrators can upload a group image")
    data, width, height = storage.prepare_image(raw)
    digest = hashlib.sha256(data).hexdigest()
    values = {"submission_id": str(submission_id), "group_id": str(identity), "kind": kind, "sha256": digest}
    from types import SimpleNamespace
    class UploadInput(SimpleNamespace):
        def model_dump(self, **_kwargs):
            return values
    body = UploadInput(submission_id=submission_id)
    replay = _replay(db, user, body, "media_upload", identity)
    if replay:
        return replay
    from models.property_network import NetworkMedia
    if (db.query(Media).filter_by(uploader_id=user.id).count()
            + db.query(NetworkMedia).filter_by(uploader_id=user.id).count() >= MAX_PHOTOS_PER_USER):
        raise HTTPException(409, "Your network photo limit has been reached")
    media_id = uuid5(NAMESPACE_URL, f"ildr-group-photo:{user.id}:{submission_id}")
    key = f"groups/{identity}/{media_id}.webp"
    # The private byte helper never upserts/publishes/deletes. An uncertain
    # failed upload is resolved via its receipt; the UI must not auto-resend.
    storage.store_image(key, data)
    row = Media(id=media_id, group_id=identity, uploader_id=user.id, kind=kind, submission_id=submission_id,
                object_key=key, sha256=digest, byte_count=len(data), width=width, height=height)
    db.add(row)
    return _finish(db, user, group, body, "media_upload", "media", row, identity)
