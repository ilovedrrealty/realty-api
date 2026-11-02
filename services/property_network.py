"""Assignment-scoped work history and opted-in member walls; no notifications."""
import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import and_, or_
from models.user import User
from models.listing import Listing
from models.property_network import (NetworkProperty as Property, NetworkProject as Project, NetworkProjectMember as Member,
    NetworkMilestone as Milestone, NetworkProjectUpdate as Update, NetworkProjectComment as ProjectComment,
    SocialNetworkProfile as Profile, NetworkPost as Post, NetworkPostComment as PostComment,
    NetworkBlock as Block, NetworkMedia as Media, NetworkAudit as Audit)
from services.property_management import property_for, listings_for, today_dr
from utils import private_network_media as storage

PAGE_SIZE = 30
MAX_REGISTRY_PROPERTIES = 500
MAX_PROPERTY_PROJECTS = 500


def now():
    return datetime.now(timezone.utc)


def page(query, number, mapper):
    total = query.count()
    return {"items": [mapper(row) for row in query.offset((number - 1) * PAGE_SIZE).limit(PAGE_SIZE)],
            "total": total, "page": number, "page_size": PAGE_SIZE}


def audit(db, user, kind, identity, action, data=None):
    db.add(Audit(actor_id=user.id, target_kind=kind, target_id=identity, action=action, data=data or {}))


def version(row, expected):
    if row.version != expected:
        raise HTTPException(409, "This record changed. Reload before saving")


def active_user(db, code):
    if type(code) is not int or code <= 0:
        raise HTTPException(404, "Active verified member not found")
    row = db.query(User).filter(User.user_code == code, User.status == "active", User.email_verified.is_(True)).first()
    if row is None:
        raise HTTPException(404, "Active verified member not found")
    return row


def actor(db, user_id, *, project=False):
    row = db.get(User, user_id)
    if row is None:
        return {"user_code": None, "display_name": "Former member"}
    profile = db.get(Profile, user_id) if not project else None
    # A wall must not expose a private account name when its author chose a
    # different network alias (or has not created a network profile yet).
    label = row.display_name if project else profile.display_name if profile else "Member"
    return {"user_code": row.user_code, "display_name": label}


def creation_digest(body):
    """Hash the normalized ORIGINAL input, never later editable row values."""
    raw = json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def lock_actor(db, user):
    # Serializes this actor's create quota and idempotency keys. No external I/O.
    # PostgreSQL FOR NO KEY UPDATE still serializes actor creates, while staying
    # compatible with the FK KEY SHARE locks acquired by property/audit inserts.
    current = db.query(User).filter(User.id == user.id).populate_existing().with_for_update(key_share=True).first()
    if current is None or current.status != "active" or current.email_verified is not True:
        raise HTTPException(403, "An active verified account is required")
    return current


def registry_query(db, user):
    query = db.query(Property)
    return query if user.role == "admin" else query.filter(Property.custodian_id == user.id)


def registry_for(db, user, identity, *, lock=False):
    query = registry_query(db, user).filter(Property.id == identity)
    row = query.populate_existing().with_for_update().first() if lock else query.first()
    if row is None:
        raise HTTPException(404, "Private property not found")
    return row


def registry_view(db, user, row):
    manage = user.role == "admin" or row.custodian_id == user.id
    if not manage:
        raise HTTPException(404, "Private property not found")
    custodian = db.get(User, row.custodian_id)
    can_start = (row.status == "active" and custodian is not None
                 and custodian.status == "active" and custodian.email_verified is True)
    return {"id": str(row.id), "title": row.title, "location": row.location, "address": row.address,
            "property_class": row.property_class, "relationship": row.relationship, "notes": row.notes,
            "status": row.status, "version": row.version, "created_at": row.created_at, "updated_at": row.updated_at,
            "can_manage": True, "can_create_project": can_start}


def registry_page(db, user, number, status="all"):
    query = registry_query(db, user)
    if status != "all":
        query = query.filter(Property.status == status)
    result = page(query.order_by(Property.updated_at.desc(), Property.id), number,
                  lambda row: registry_view(db, user, row))
    result["can_create"] = True  # Route identity is active+verified, independently of account role.
    return result


def registry_submission(db, user, submission_id):
    # Even Admin resolves only their own actor key, never another account's key.
    row = db.query(Property).filter(Property.custodian_id == user.id, Property.submission_id == submission_id).first()
    if row is None:
        raise HTTPException(404, "Private property submission not found")
    return registry_view(db, user, row)


def create_registry(db, user, body):
    user = lock_actor(db, user)
    digest = creation_digest(body)
    existing = db.query(Property).filter(Property.custodian_id == user.id, Property.submission_id == body.submission_id).first()
    if existing is not None:
        if existing.creation_sha256 != digest:
            raise HTTPException(409, "This submission key was used with different property details")
        return registry_view(db, user, existing)
    if db.query(Property).filter(Property.custodian_id == user.id).count() >= MAX_REGISTRY_PROPERTIES:
        raise HTTPException(409, "This account has reached its private property limit")
    row = Property(custodian_id=user.id, submission_id=body.submission_id,
                   title=body.title, location=body.location, address=body.address,
                   property_class=body.property_class, relationship=body.relationship, notes=body.notes,
                   status="active", version=1, creation_sha256=digest, authority_acknowledged=True)
    db.add(row); db.flush()
    audit(db, user, "registry_property", row.id, "created")
    db.commit(); db.refresh(row)
    return registry_view(db, user, row)


def edit_registry(db, user, identity, body):
    row = registry_for(db, user, identity, lock=True)
    version(row, body.expected_version)
    for name in ("title", "location", "address", "property_class", "relationship", "notes", "status"):
        setattr(row, name, getattr(body, name))
    row.version += 1; row.updated_at = now()
    audit(db, user, "registry_property", row.id, "updated", {"version": row.version, "status": row.status})
    db.commit(); db.refresh(row)
    return registry_view(db, user, row)


def accessible_projects(db, user):
    query = db.query(Project)
    if user.role == "admin":
        return query
    member_ids = db.query(Member.project_id).filter(Member.user_id == user.id, Member.active.is_(True))
    return query.filter(or_(Project.owner_id == user.id, Project.id.in_(member_ids)))


def project_for(db, user, identity, *, lock=False, permission="read"):
    query = accessible_projects(db, user).filter(Project.id == identity)
    row = (query.with_for_update() if lock else query).first()
    if row is None:
        raise HTTPException(404, "Project not found")
    role = "admin" if user.role == "admin" else "owner" if row.owner_id == user.id else None
    if role is None:
        membership = db.query(Member).filter(Member.project_id == row.id, Member.user_id == user.id, Member.active.is_(True)).first()
        if membership is None:
            raise HTTPException(404, "Project not found")
        role = membership.role
    allowed = {"read": {"admin", "owner", "manager", "professional", "viewer"},
               "manage": {"admin", "owner", "manager"}, "assign": {"admin", "owner"},
               "update": {"admin", "owner", "manager", "professional"}}
    if role not in allowed[permission]:
        raise HTTPException(403, "This project action is not permitted")
    return row, role


def progress(db, project_id):
    states = [value for value, in db.query(Milestone.status).filter(Milestone.project_id == project_id)]
    total = sum(value != "cancelled" for value in states)
    complete = states.count("completed")
    return {"total": total, "completed": complete, "percent": round(100 * complete / total, 1) if total else None}


def project_view(db, user, row):
    _, role = project_for(db, user, row.id)
    listing = db.get(Listing, row.listing_id) if row.listing_id is not None else None
    private = db.get(Property, row.property_id) if row.property_id is not None else None
    if listing is None and private is None:
        raise HTTPException(404, "Project property not found")
    label = listing.title if listing is not None else private.title
    return {"id": str(row.id), "listing_id": str(row.listing_id) if row.listing_id is not None else None,
            "listing_title": label, "property_id": str(row.property_id) if row.property_id is not None else None,
            "property_title": private.title if private is not None else None,
            "title": row.title, "summary": row.summary, "property_class": row.property_class,
            "service_category_ids": row.service_category_ids, "status": row.status, "version": row.version,
            "my_role": role, "can_manage": role in {"admin", "owner", "manager"},
            "can_assign": role in {"admin", "owner"}, "can_update": role != "viewer",
            "progress": progress(db, row.id), "created_at": row.created_at, "updated_at": row.updated_at}


def construct_project(db, user, body, *, actor_locked=False):
    """Checked construction without commit, shared with atomic service awards.

    Callers using actor_locked must already hold the active actor's NO KEY
    UPDATE lock in their consistent transaction lock order. Source checks,
    original fingerprints, project quotas and membership rules stay here.
    """
    user = user if actor_locked else lock_actor(db, user)
    digest = creation_digest(body) if body.submission_id is not None else None
    if body.submission_id is not None:
        existing = db.query(Project).filter(Project.created_by_id == user.id, Project.submission_id == body.submission_id).first()
        if existing is not None:
            # Reauthorize before returning an old ID; archive/class/quota rules
            # govern NEW projects, not resolution of the original creation.
            project_for(db, user, existing.id)
            if existing.creation_sha256 != digest:
                raise HTTPException(409, "This submission key was used with different project details")
            return existing
    if body.property_id is not None:
        private = registry_for(db, user, body.property_id, lock=True)
        if private.status != "active":
            raise HTTPException(409, "Archived private properties cannot start new projects")
        if body.property_class != private.property_class:
            raise HTTPException(422, "Choose the private property's current property class")
        owner = db.get(User, private.custodian_id)
        count_query = db.query(Project).filter(Project.property_id == private.id)
    else:
        listing = property_for(db, user, body.listing_id, lock=True)
        owner_id = listing.owner_id or listing.submitted_by
        owner = db.get(User, owner_id) if owner_id else None
        if listing.owner_id is None and owner is not None and owner.role != "owner":
            owner = None  # A submitting/importing agent is not invented as the owner.
        count_query = db.query(Project).filter(Project.listing_id == listing.id)
    if owner is None or owner.status != "active" or owner.email_verified is not True:
        raise HTTPException(409, "Assign an active verified property owner before creating a project")
    if count_query.count() >= MAX_PROPERTY_PROJECTS:
        raise HTTPException(409, "This property has reached its project limit")
    row = Project(listing_id=body.listing_id, property_id=body.property_id, owner_id=owner.id, title=body.title, summary=body.summary,
                  property_class=body.property_class, service_category_ids=body.service_category_ids,
                  status="open", version=1, created_by_id=user.id if body.submission_id is not None else None,
                  submission_id=body.submission_id, creation_sha256=digest)
    db.add(row); db.flush()
    if user.id != owner.id and user.role != "admin":
        db.add(Member(project_id=row.id, user_id=user.id, role="manager", active=True))
    audit(db, user, "project", row.id, "created")
    return row


def create_project(db, user, body):
    row = construct_project(db, user, body)
    db.commit(); db.refresh(row)
    return project_view(db, user, row)


def project_submission(db, user, submission_id):
    row = db.query(Project).filter(Project.created_by_id == user.id, Project.submission_id == submission_id).first()
    if row is None:
        raise HTTPException(404, "Project submission not found")
    project_for(db, user, row.id)
    return project_view(db, user, row)


def registry_history(db, user, identity, number):
    visible = accessible_projects(db, user).filter(Project.property_id == identity)
    own = registry_query(db, user).filter(Property.id == identity).with_entities(Property.id).first()
    if own is None and visible.first() is None:
        raise HTTPException(404, "Private property service history not found")
    query = db.query(Update, Project).join(Project, Project.id == Update.project_id).filter(
        Update.project_id.in_(visible.with_entities(Project.id)))
    return page(query.order_by(Update.created_at.desc(), Update.id), number, lambda pair: {
        "id": str(pair[0].id), "project_id": str(pair[1].id), "project_title": pair[1].title,
        "service_category_ids": pair[1].service_category_ids, "project_status": pair[1].status,
        "kind": pair[0].kind, "body": pair[0].body, "occurred_on": pair[0].occurred_on,
        "media_ids": pair[0].media_ids, "created_at": pair[0].created_at})


def edit_project(db, user, identity, body):
    row, _ = project_for(db, user, identity, lock=True, permission="manage")
    version(row, body.expected_version)
    row.title, row.summary, row.status = body.title, body.summary, body.status
    row.version += 1; row.updated_at = now()
    audit(db, user, "project", row.id, "updated", {"version": row.version, "status": row.status})
    db.commit(); db.refresh(row)
    return project_view(db, user, row)


def members(db, user, identity):
    row, _ = project_for(db, user, identity)
    result = [{**actor(db, row.owner_id, project=True), "role": "owner", "active": True}]
    result.extend({**actor(db, value.user_id, project=True), "role": value.role, "active": value.active}
                  for value in db.query(Member).filter(Member.project_id == identity).order_by(Member.id).limit(101))
    return result


def assign(db, user, identity, body):
    row, _ = project_for(db, user, identity, lock=True, permission="assign")
    version(row, body.expected_version)
    target = active_user(db, body.user_code)
    if target.id == row.owner_id:
        raise HTTPException(422, "The property owner already has owner access")
    member = db.query(Member).filter(Member.project_id == row.id, Member.user_id == target.id).first()
    if member is None:
        if db.query(Member).filter(Member.project_id == row.id).count() >= 100:
            raise HTTPException(409, "This project has reached its member limit")
        member = Member(project_id=row.id, user_id=target.id)
        db.add(member)
    member.role, member.active = body.role, True
    row.version += 1; row.updated_at = now()
    audit(db, user, "project", row.id, "member_assigned", {"member_code": target.user_code, "role": body.role})
    db.commit()
    return members(db, user, identity)


def revoke(db, user, identity, code, body):
    row, _ = project_for(db, user, identity, lock=True, permission="assign")
    version(row, body.expected_version)
    target = db.query(User).filter(User.user_code == code).first()
    member = db.query(Member).filter(Member.project_id == row.id, Member.user_id == target.id).first() if target else None
    if member is None:
        raise HTTPException(404, "Project member not found")
    member.active = False
    row.version += 1; row.updated_at = now()
    audit(db, user, "project", row.id, "member_revoked", {"member_code": code})
    db.commit()
    return members(db, user, identity)


def milestone_view(row):
    return {"id": str(row.id), "project_id": str(row.project_id), "title": row.title,
            "status": row.status, "due_date": row.due_date, "version": row.version}


def milestone_event(db, user, row):
    db.add(Update(project_id=row.project_id, author_id=user.id, submission_id=uuid4(), kind="milestone",
                  body=f"{row.title}: {row.status}", occurred_on=today_dr(), media_ids=[]))
    audit(db, user, "milestone", row.id, "milestone_state", {"title": row.title, "status": row.status,
          "due_date": row.due_date.isoformat() if row.due_date else None, "version": row.version})


def create_milestone(db, user, identity, body):
    project_for(db, user, identity, lock=True, permission="manage")
    if db.query(Milestone).filter(Milestone.project_id == identity).count() >= 1000:
        raise HTTPException(409, "This project has reached its milestone limit")
    row = Milestone(project_id=identity, title=body.title, due_date=body.due_date, status="open", version=1)
    db.add(row); db.flush(); milestone_event(db, user, row); db.commit(); db.refresh(row)
    return milestone_view(row)


def edit_milestone(db, user, identity, body):
    row = db.get(Milestone, identity)
    if row is None:
        raise HTTPException(404, "Milestone not found")
    project_for(db, user, row.project_id, lock=True, permission="manage")
    db.refresh(row)
    version(row, body.expected_version)
    row.title, row.due_date, row.status = body.title, body.due_date, body.status
    row.version += 1
    milestone_event(db, user, row); db.commit()
    return milestone_view(row)


def update_for(db, user, identity, permission="read", lock=False):
    row = db.get(Update, identity)
    if row is None:
        raise HTTPException(404, "Progress update not found")
    project_for(db, user, row.project_id, lock=lock, permission=permission)
    return row


def update_view(db, row):
    return {"id": str(row.id), "project_id": str(row.project_id), "kind": row.kind, "body": row.body,
            "occurred_on": row.occurred_on, "correction_of": str(row.correction_of) if row.correction_of else None,
            "author": actor(db, row.author_id, project=True), "media_ids": row.media_ids, "created_at": row.created_at}


def media_for_attachment(db, user, identities, project_id):
    result = []
    for identity in identities:
        row = db.query(Media).filter(Media.id == identity).with_for_update().first()
        if row is None or row.uploader_id != user.id or row.project_id != project_id or row.update_id or row.post_id:
            raise HTTPException(422, "Choose your own unattached photos for this context")
        result.append(row)
    return result


def create_update(db, user, identity, body):
    project_for(db, user, identity, lock=True, permission="update")
    saved = db.query(Update).filter(Update.author_id == user.id, Update.submission_id == body.submission_id).first()
    if saved:
        if (saved.project_id, saved.body, saved.occurred_on, saved.correction_of, saved.media_ids) != (
            identity, body.body, body.occurred_on, body.correction_of, [str(value) for value in body.media_ids]):
            raise HTTPException(409, "This submission identifier was already used")
        return update_view(db, saved)
    if body.occurred_on > today_dr():
        raise HTTPException(422, "Progress dates cannot be in the future")
    if body.correction_of:
        original = db.get(Update, body.correction_of)
        if original is None or original.project_id != identity:
            raise HTTPException(422, "Corrections must reference this project's history")
    photos = media_for_attachment(db, user, body.media_ids, identity)
    row = Update(project_id=identity, author_id=user.id, submission_id=body.submission_id,
                 kind="correction" if body.correction_of else "progress", body=body.body,
                 occurred_on=body.occurred_on, correction_of=body.correction_of,
                 media_ids=[str(value) for value in body.media_ids])
    db.add(row); db.flush()
    for photo in photos:
        photo.update_id = row.id
    db.commit(); db.refresh(row)
    return update_view(db, row)


def project_comment(db, user, identity, body):
    update_for(db, user, identity, permission="update", lock=True)
    saved = db.query(ProjectComment).filter(ProjectComment.author_id == user.id, ProjectComment.submission_id == body.submission_id).first()
    if saved:
        if (saved.update_id, saved.body, saved.media_id) != (identity, body.body, body.media_id):
            raise HTTPException(409, "This submission identifier was already used")
        return comment_view(db, user, saved)
    if body.media_id:
        photo = db.get(Media, body.media_id)
        if photo is None or photo.update_id != identity:
            raise HTTPException(422, "Choose a photo belonging to this progress update")
    row = ProjectComment(update_id=identity, author_id=user.id, submission_id=body.submission_id, body=body.body, media_id=body.media_id)
    db.add(row); db.commit(); db.refresh(row)
    return comment_view(db, user, row)


def blocked(db, first, second):
    return db.query(Block).filter(or_(and_(Block.blocker_id == first, Block.blocked_id == second),
                                     and_(Block.blocker_id == second, Block.blocked_id == first))).first() is not None


def profile_for(db, user, code, *, lock=False):
    target = db.query(User).filter(User.user_code == code).first() if user.role == "admin" and code else active_user(db, code)
    if target is None:
        raise HTTPException(404, "Member profile not found")
    query = db.query(Profile).filter(Profile.user_id == target.id)
    profile = (query.with_for_update() if lock else query).first()
    privileged = user.id == target.id or user.role == "admin"
    if not privileged and (profile is None or profile.visibility != "members" or blocked(db, user.id, target.id)):
        raise HTTPException(404, "Member profile not found")
    return target, profile


def profile_view(user, target, row):
    return {"user_code": target.user_code, "display_name": row.display_name if row else target.display_name,
            "bio": row.bio if row else "", "status_text": row.status_text if row else "",
            "visibility": row.visibility if row else "private", "wall_policy": row.wall_policy if row else "own_only",
            "comments_enabled": row.comments_enabled if row else True, "version": row.version if row else 0,
            "can_edit": user.id == target.id}


def save_profile(db, user, body):
    # No profile can enter discovery without a routable existing member code.
    # This does not generate codes, create accounts or backfill legacy users.
    active_user(db, user.user_code)
    # Serialize first-save and later wall policy/post decisions on the same user.
    db.query(User).filter(User.id == user.id).with_for_update().one()
    row = db.query(Profile).filter(Profile.user_id == user.id).first()
    if (row.version if row else 0) != body.expected_version:
        raise HTTPException(409, "Your profile changed. Reload before saving")
    if row is None:
        row = Profile(user_id=user.id, version=0)
        db.add(row)
    for key in ("display_name", "bio", "status_text", "visibility", "wall_policy", "comments_enabled"):
        setattr(row, key, getattr(body, key))
    row.version += 1
    audit(db, user, "profile", user.id, "profile_saved", {"version": row.version, "visibility": row.visibility})
    db.commit(); db.refresh(row)
    return profile_view(user, user, row)


def member_profiles(db, user, term, number):
    excluded = db.query(Block.blocked_id).filter(Block.blocker_id == user.id).union(
        db.query(Block.blocker_id).filter(Block.blocked_id == user.id))
    query = db.query(Profile, User).join(User, User.id == Profile.user_id).filter(
        Profile.visibility == "members", User.status == "active", User.email_verified.is_(True), User.id.notin_(excluded))
    if term:
        term = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(Profile.display_name.ilike("%" + term + "%", escape="\\"))
    return page(query.order_by(Profile.display_name, Profile.user_id), number, lambda pair: profile_view(user, pair[1], pair[0]))


def post_for(db, user, identity, *, lock=False, moderate=False):
    query = db.query(Post).filter(Post.id == identity)
    post = (query.with_for_update() if lock else query).first()
    if post is None:
        raise HTTPException(404, "Post not found")
    owner = db.get(User, post.profile_user_id)
    target, profile = profile_for(db, user, owner.user_code, lock=lock)
    privileged = user.id == target.id or user.role == "admin"
    if moderate and not privileged:
        raise HTTPException(403, "Only the profile owner can moderate this wall")
    if not privileged and (post.status != "visible" or blocked(db, user.id, post.author_id)):
        raise HTTPException(404, "Post not found")
    return post, target, profile


def post_view(db, user, row, profile):
    return {"id": str(row.id), "profile_user_code": db.get(User, row.profile_user_id).user_code,
            "author": actor(db, row.author_id), "body": row.body, "media_ids": row.media_ids, "status": row.status,
            "can_moderate": user.id == row.profile_user_id or user.role == "admin",
            "can_comment": row.status == "visible" and profile.comments_enabled,
            "created_at": row.created_at}


def wall_posts(db, user, code, number):
    target, profile = profile_for(db, user, code)
    if profile is None:
        return {"items": [], "total": 0, "page": number, "page_size": PAGE_SIZE}
    query = db.query(Post).filter(Post.profile_user_id == target.id)
    if user.id != target.id and user.role != "admin":
        blocked_authors = db.query(Block.blocked_id).filter(Block.blocker_id == user.id).union(
            db.query(Block.blocker_id).filter(Block.blocked_id == user.id))
        query = query.filter(Post.status == "visible", Post.author_id.notin_(blocked_authors))
    return page(query.order_by(Post.created_at.desc(), Post.id), number, lambda row: post_view(db, user, row, profile))


def create_post(db, user, code, body):
    target, profile = profile_for(db, user, code, lock=True)
    if profile is None:
        raise HTTPException(409, "Save your network profile before posting")
    if target.id != user.id and (profile.wall_policy != "approval_required" or blocked(db, user.id, target.id)):
        raise HTTPException(403, "This profile does not accept other members' posts")
    saved = db.query(Post).filter(Post.author_id == user.id, Post.submission_id == body.submission_id).first()
    if saved:
        if (saved.profile_user_id, saved.body, saved.media_ids) != (target.id, body.body, [str(value) for value in body.media_ids]):
            raise HTTPException(409, "This submission identifier was already used")
        return post_view(db, user, saved, profile)
    photos = media_for_attachment(db, user, body.media_ids, None)
    row = Post(profile_user_id=target.id, author_id=user.id, submission_id=body.submission_id, body=body.body,
               media_ids=[str(value) for value in body.media_ids], status="visible" if target.id == user.id else "pending")
    db.add(row); db.flush()
    for photo in photos:
        photo.post_id = row.id
    db.commit(); db.refresh(row)
    return post_view(db, user, row, profile)


def moderate_post(db, user, identity, body):
    post, target, profile = post_for(db, user, identity, lock=True, moderate=True)
    if body.action == "approve" and blocked(db, post.author_id, target.id):
        raise HTTPException(409, "A blocked member's post cannot be approved")
    post.status = "visible" if body.action == "approve" else "hidden"
    audit(db, user, "post", post.id, body.action)
    db.commit()
    return post_view(db, user, post, profile)


def comment_view(db, user, row, wall_owner_id=None):
    return {"id": str(row.id), "body": row.body, "author": actor(db, row.author_id, project=isinstance(row, ProjectComment)),
            "hidden": bool(getattr(row, "hidden", False)), "created_at": row.created_at,
            "media_id": str(row.media_id) if row.media_id else None,
            "can_hide": wall_owner_id is not None and (user.role == "admin" or user.id in {wall_owner_id, row.author_id})}


def post_comments(db, user, identity, number):
    post, target, _ = post_for(db, user, identity)
    query = db.query(PostComment).filter(PostComment.post_id == post.id)
    if user.id != target.id and user.role != "admin":
        blocked_authors = db.query(Block.blocked_id).filter(Block.blocker_id == user.id).union(
            db.query(Block.blocker_id).filter(Block.blocked_id == user.id))
        query = query.filter(PostComment.hidden.is_(False), PostComment.author_id.notin_(blocked_authors))
    return page(query.order_by(PostComment.created_at, PostComment.id), number,
                lambda row: comment_view(db, user, row, target.id))


def create_post_comment(db, user, identity, body):
    post, target, profile = post_for(db, user, identity, lock=True)
    if post.status != "visible" or not profile.comments_enabled:
        raise HTTPException(403, "Comments are disabled for this post")
    saved = db.query(PostComment).filter(PostComment.author_id == user.id, PostComment.submission_id == body.submission_id).first()
    if saved:
        if (saved.post_id, saved.body, saved.media_id) != (identity, body.body, body.media_id):
            raise HTTPException(409, "This submission identifier was already used")
        return comment_view(db, user, saved, target.id)
    if body.media_id:
        photo = db.get(Media, body.media_id)
        if photo is None or photo.post_id != identity:
            raise HTTPException(422, "Choose a photo belonging to this post")
    row = PostComment(post_id=post.id, author_id=user.id, submission_id=body.submission_id,
                      body=body.body, hidden=False, media_id=body.media_id)
    db.add(row); db.commit(); db.refresh(row)
    return comment_view(db, user, row, target.id)


def hide_comment(db, user, identity):
    comment = db.query(PostComment).filter(PostComment.id == identity).with_for_update().first()
    if comment is None:
        raise HTTPException(404, "Comment not found")
    post, target, _ = post_for(db, user, comment.post_id)
    if user.role != "admin" and user.id not in {comment.author_id, target.id}:
        raise HTTPException(403, "This comment cannot be moderated by you")
    comment.hidden = True
    audit(db, user, "comment", comment.id, "hidden")
    db.commit()
    return comment_view(db, user, comment, target.id)


def set_block(db, user, code, body):
    target = active_user(db, code)
    if target.id == user.id:
        raise HTTPException(422, "You cannot block yourself")
    # Same blocker lock makes repeated block/unblock requests deterministic.
    db.query(User).filter(User.id == user.id).with_for_update().one()
    row = db.query(Block).filter(Block.blocker_id == user.id, Block.blocked_id == target.id).first()
    if body.blocked and row is None:
        db.add(Block(blocker_id=user.id, blocked_id=target.id))
    elif not body.blocked and row:
        db.delete(row)
    audit(db, user, "profile", user.id, "blocked" if body.blocked else "unblocked", {"member_code": code})
    db.commit()
    return {"blocked": body.blocked}


def media_view(row):
    return {"id": str(row.id), "url": f"/property-network/media/{row.id}", "content_type": "image/webp",
            "bytes": row.byte_count, "sha256": row.sha256, "width": row.width, "height": row.height}


def upload_media(db, user, project_id, raw):
    if project_id:
        project_for(db, user, project_id, lock=True, permission="update")
    # Serializes the per-uploader bound without trusting the uploaded filename.
    db.query(User).filter(User.id == user.id).with_for_update().one()
    if db.query(Media).filter(Media.uploader_id == user.id).count() >= 5000:
        raise HTTPException(409, "Your network photo limit has been reached")
    data, width, height = storage.prepare_image(raw)
    identity = uuid4()
    key = f"network/{user.id}/{identity}.webp"
    storage.store_image(key, data)
    row = Media(id=identity, uploader_id=user.id, project_id=project_id, object_key=key,
                sha256=hashlib.sha256(data).hexdigest(), byte_count=len(data), width=width, height=height)
    db.add(row); db.commit(); db.refresh(row)
    return media_view(row)


def media_bytes(db, user, identity):
    row = db.get(Media, identity)
    if row is None:
        raise HTTPException(404, "Photo not found")
    if row.project_id:
        project_for(db, user, row.project_id)
        if row.update_id is None and row.uploader_id != user.id:
            raise HTTPException(404, "Photo not found")
    elif row.post_id and row.uploader_id != user.id:
        post_for(db, user, row.post_id)
    elif row.uploader_id != user.id:
        raise HTTPException(404, "Photo not found")
    return storage.read_image(row)
