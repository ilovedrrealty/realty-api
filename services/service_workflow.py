"""Private selected-member agreements. No delivery/provider or contact I/O."""
import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import and_, or_, exists, func
from models.user import User
from models.listing import Listing
from models.property_network import NetworkProperty as Property, NetworkProject as Project, NetworkProjectMember as Member
from models.service_workflow import (NetworkServicePreference as Preference, NetworkServiceRequest as ServiceRequest,
    NetworkServiceRecipient as Recipient, NetworkServiceProposal as Proposal, NetworkServiceAction as Action)
from schemas.property_network import ProjectCreate
from services import property_network as network
from services.property_management import listings_for, today_dr
from utils.professional_options import CATEGORY_IDS, PROPERTY_CLASS_IDS

PAGE_SIZE = 30
MAX_SOURCE_REQUESTS = 500
MAX_OPEN_REQUESTS = 100
MAX_REVISIONS = 100
MANAGED_OPERATIONS = {"request_create", "recipient_revoke", "owner_counter", "request_cancel", "request_award"}


def unavailable():
    raise HTTPException(404, "Service record is unavailable")


def conflict():
    raise HTTPException(409, "This service record changed or the action is no longer available. Refresh before retrying")


def checked_user(user):
    if user is None or user.status != "active" or user.email_verified is not True:
        raise HTTPException(403, "Active verified account required")
    return user


def fingerprint(operation, target, body):
    value = {"operation": operation, "target": str(target), "input": body.model_dump(mode="json")}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def lock_users(db, actor, ids):
    # All users are locked before any FK target/source/policy row. NO KEY
    # UPDATE serializes eligibility changes without conflicting with KEY SHARE
    # acquired by ordinary FK inserts. Never acquire these IDs in actor order.
    ids = set(ids) | {actor.id}
    rows = db.query(User).filter(User.id.in_(ids)).order_by(User.id).populate_existing().with_for_update(key_share=True).all()
    mapping = {row.id: row for row in rows}
    if set(mapping) != ids:
        unavailable()
    return checked_user(mapping[actor.id])


def source_row(db, property_id, listing_id, *, lock=False):
    cls, identity = (Property, property_id) if property_id is not None else (Listing, listing_id)
    query = db.query(cls).filter(cls.id == identity).populate_existing()
    row = (query.with_for_update() if lock else query).first()
    if row is None:
        unavailable()
    owner_id = row.custodian_id if property_id is not None else row.owner_id or row.submitted_by
    owner = db.get(User, owner_id) if owner_id else None
    if property_id is None and row.owner_id is None and owner is not None and owner.role != "owner":
        owner = None
    return row, owner


def source_authorized(db, user, row):
    if user.role == "admin":
        return True
    if isinstance(row, Property):
        return row.custodian_id == user.id
    try:
        return listings_for(db, user).filter(Listing.id == row.id).first() is not None
    except HTTPException:
        return False


def managed(db, user, request, source=None):
    checked_user(user)
    if user.role == "admin":
        return True
    if user.id not in {request.custodian_id, request.requester_id}:
        return False
    row, owner = source or source_row(db, request.property_id, request.listing_id)
    return owner is not None and owner.id == request.custodian_id and source_authorized(db, user, row)


def source_current(db, request, source=None):
    row, owner = source or source_row(db, request.property_id, request.listing_id)
    if owner is None or owner.id != request.custodian_id or owner.status != "active" or owner.email_verified is not True or row.status != "active":
        return False
    return row.property_class == request.property_class if request.property_id else row.type == request.source_listing_type


def eligible(db, recipient, request):
    member, preference = db.get(User, recipient.provider_id), db.get(Preference, recipient.provider_id)
    return bool(member and member.status == "active" and member.email_verified is True and
        type(member.user_code) is int and member.user_code > 0 and preference and preference.service_opt_in is True and
        request.category_id in preference.category_ids and request.property_class in preference.property_class_ids)


def own_recipient(db, user, request):
    return db.query(Recipient).filter(Recipient.request_id == request.id, Recipient.provider_id == user.id,
                                     Recipient.status != "revoked").first()


def require_managed(db, user, request, source=None):
    if not managed(db, user, request, source):
        unavailable()


def require_received(db, user, request, recipient):
    checked_user(user)
    if recipient is None or recipient.request_id != request.id or recipient.provider_id != user.id or recipient.status == "revoked":
        unavailable()


def current_proposal(db, recipient):
    return db.get(Proposal, recipient.current_proposal_id) if recipient.current_proposal_id else None


def proposal_view(row):
    if row is None:
        return None
    return {"id": str(row.id), "recipient_id": str(row.recipient_id), "revision_no": row.revision_no,
        "parent_proposal_id": str(row.parent_proposal_id) if row.parent_proposal_id else None,
        "kind": row.kind, "status": row.status, "version": row.version, "amount": format(row.amount, ".2f"),
        "currency": row.currency, "pricing_basis": row.pricing_basis, "scope_text": row.scope_text,
        "exclusions": row.exclusions, "estimated_days": row.estimated_days, "valid_until": row.valid_until,
        "is_expired": today_dr() > row.valid_until, "created_at": row.created_at}


def awardable(proposal):
    return bool(proposal and today_dr() <= proposal.valid_until and
        ((proposal.kind == "provider_quote" and proposal.status == "submitted") or
         (proposal.kind == "owner_counter" and proposal.status == "accepted")))


def can_negotiate(db, request, recipient):
    return request.status == "open" and source_current(db, request) and eligible(db, recipient, request)


def linked_project(db, user, request, *, winner=False):
    if request.project_id is None or (not winner and not managed(db, user, request)):
        return None
    try:
        row, _ = network.project_for(db, user, request.project_id)
        return network.project_view(db, user, row)
    except HTTPException:
        return None


def managed_recipient(db, user, request, row):
    proposal = current_proposal(db, row)
    new = can_negotiate(db, request, row) and row.status == "accepted"
    return {"id": str(row.id), "user_code": row.user_code, "member_alias": row.member_alias,
        "status": row.status, "version": row.version, "current_proposal": proposal_view(proposal),
        "capabilities": {"can_revoke": request.status == "open" and row.status in {"pending", "accepted"},
            "can_counter": bool(new and proposal and proposal.status == "submitted" and (proposal.kind == "provider_quote" or proposal.author_id == user.id)),
            "can_withdraw": bool(request.status == "open" and row.status == "accepted" and proposal and
                proposal.kind == "owner_counter" and proposal.status == "submitted" and proposal.author_id == user.id),
            "can_award": bool(new and awardable(proposal))}}


def request_view(db, user, request, *, received=False):
    checked_user(user)
    common = {"id": str(request.id), "category_id": request.category_id, "title": request.title,
        "brief": request.brief, "area_label": request.area_label, "requester_alias": request.requester_alias,
        "desired_start_on": request.desired_start_on, "created_at": request.created_at}
    if not received and managed(db, user, request):
        row, _ = source_row(db, request.property_id, request.listing_id)
        recipients = [managed_recipient(db, user, request, r) for r in db.query(Recipient).filter(
            Recipient.request_id == request.id).order_by(Recipient.id).limit(5)]
        project = linked_project(db, user, request)
        return {**common, "view": "managed", "version": request.version, "status": request.status,
            "source": {"property_id": str(request.property_id) if request.property_id else None,
                "listing_id": str(request.listing_id) if request.listing_id else None, "title": row.title,
                "property_class": request.property_class}, "updated_at": request.updated_at,
            "recipients": recipients, "project_id": project["id"] if project else None, "can_open_project": project is not None,
            "capabilities": {"can_cancel": request.status == "open", "can_compare": True,
                "can_award": any(r["capabilities"]["can_award"] for r in recipients)}}
    recipient = own_recipient(db, user, request)
    require_received(db, user, request, recipient)
    proposal = current_proposal(db, recipient)
    new = can_negotiate(db, request, recipient)
    open_thread = request.status == "open" and recipient.status == "accepted"
    project = linked_project(db, user, request, winner=True) if recipient.status == "awarded" else None
    return {**common, "view": "received", "property_class": request.property_class,
        "recipient": {"id": str(recipient.id), "status": recipient.status, "version": recipient.version},
        "current_proposal": proposal_view(proposal), "project_id": project["id"] if project else None,
        "can_open_project": project is not None, "capabilities": {
            "can_accept": new and recipient.status == "pending",
            "can_decline": request.status == "open" and recipient.status in {"pending", "accepted"},
            "can_quote": new and open_thread,
            "can_withdraw": bool(open_thread and proposal and proposal.kind == "provider_quote" and proposal.status == "submitted"),
            "can_accept_counter": bool(new and open_thread and proposal and proposal.kind == "owner_counter" and proposal.status == "submitted" and today_dr() <= proposal.valid_until),
            "can_decline_counter": bool(open_thread and proposal and proposal.kind == "owner_counter" and proposal.status == "submitted")}}


def preference_view(db, user):
    checked_user(user)
    row = db.get(Preference, user.id)
    return {"service_opt_in": row.service_opt_in if row else False, "member_alias": row.member_alias if row else "",
        "category_ids": row.category_ids if row else [], "property_class_ids": row.property_class_ids if row else [],
        "version": row.version if row else 0, "updated_at": row.updated_at if row else None, "can_manage": True}


def prior_action(db, actor, key, digest, operation):
    row = db.query(Action).filter(Action.actor_id == actor.id, Action.submission_id == key).first()
    if row is not None and (row.creation_sha256 != digest or row.operation != operation):
        conflict()
    return row


def action_versions(row, *, provider=False, recipient=None):
    values = row.result_versions
    if row.operation == "preference_save":
        return {"preference_version": values["preference_version"]}
    if provider and row.operation in {"request_award", "request_cancel"}:
        values = values.get("closed_versions", {}).get(str(recipient.id), {})
    allowed = ("recipient_version", "proposal_version") if provider else ("request_version", "recipient_version", "proposal_version")
    return {key: values[key] for key in allowed if key in values}


def current_action_result(db, user, action):
    if action.operation == "preference_save":
        if action.actor_id != user.id:
            unavailable()
        return preference_view(db, user)
    request = db.get(ServiceRequest, action.request_id)
    if request is None:
        unavailable()
    managed_action = action.operation in MANAGED_OPERATIONS
    if action.operation == "proposal_withdraw":
        managed_action = db.get(Proposal, action.proposal_id).kind == "owner_counter"
    if managed_action:
        require_managed(db, user, request)
    else:
        require_received(db, user, request, db.get(Recipient, action.recipient_id))
    value = request_view(db, user, request, received=not managed_action)
    return {"request": value, "project": linked_project(db, user, request)} if action.operation == "request_award" else value


def record_action(db, user, body, operation, target, digest, request=None, recipient=None, proposal=None, versions=None):
    row = Action(actor_id=user.id, submission_id=body.submission_id, operation=operation, target_id=target,
        request_id=request.id if request else None, recipient_id=recipient.id if recipient else None,
        proposal_id=proposal.id if proposal else None, creation_sha256=digest, result_versions=versions or {})
    db.add(row); db.flush()
    return row


def save_preference(db, user, body):
    user = lock_users(db, user, {user.id})
    row = db.query(Preference).filter(Preference.user_id == user.id).populate_existing().with_for_update().first()
    digest = fingerprint("preference_save", user.id, body)
    if prior_action(db, user, body.submission_id, digest, "preference_save"):
        return preference_view(db, user)
    if (row.version if row else 0) != body.expected_version:
        conflict()
    if body.service_opt_in and (type(user.user_code) is not int or user.user_code <= 0):
        raise HTTPException(409, "A current member code is required to offer services")
    if row is None:
        row = Preference(user_id=user.id, version=1)
        db.add(row)
    else:
        row.version += 1
    row.service_opt_in, row.member_alias = body.service_opt_in, body.member_alias
    row.category_ids, row.property_class_ids, row.updated_at = body.category_ids, body.property_class_ids, network.now()
    record_action(db, user, body, "preference_save", user.id, digest, versions={"preference_version": row.version})
    db.commit()
    return preference_view(db, user)


def discovery(db, user, category_id, property_class, number):
    checked_user(user)
    if category_id not in CATEGORY_IDS or property_class not in PROPERTY_CLASS_IDS:
        raise HTTPException(422, "Choose a known service context")
    query = db.query(Preference, User).join(User, User.id == Preference.user_id).filter(
        Preference.service_opt_in.is_(True), User.status == "active", User.email_verified.is_(True), User.user_code > 0,
        Preference.category_ids.contains([category_id]), Preference.property_class_ids.contains([property_class]))
    return network.page(query.order_by(User.user_code), number, lambda pair: {
        "user_code": pair[1].user_code, "member_alias": pair[0].member_alias, "category_ids": pair[0].category_ids})


def create_request(db, user, body):
    digest = fingerprint("request_create", body.property_id or body.listing_id, body)
    old = db.query(Action).filter(Action.actor_id == user.id, Action.submission_id == body.submission_id).first()
    if old is not None and old.operation == "request_create":
        # An old provider code/preference or source archive is not a NEW gate
        # for resolution. Reauthorize the stored request before key comparison.
        user, previous, previous_source, _ = locked_request(db, user, old.request_id)
        require_managed(db, user, previous, previous_source)
        prior_action(db, user, body.submission_id, digest, "request_create")
        return current_action_result(db, user, old)
    source, owner = source_row(db, body.property_id, body.listing_id)
    checked_user(user)
    if not source_authorized(db, user, source):
        unavailable()
    members = db.query(User).filter(User.user_code.in_(body.recipient_user_codes)).all()
    if owner is None or len(members) != len(body.recipient_user_codes):
        unavailable()
    original_owner_id = owner.id
    user = lock_users(db, user, {owner.id} | {m.id for m in members})
    source, owner = source_row(db, body.property_id, body.listing_id, lock=True)
    if owner is None or owner.id != original_owner_id or sorted(m.user_code for m in members) != body.recipient_user_codes:
        conflict()
    if not source_authorized(db, user, source):
        unavailable()
    db.query(Preference).filter(Preference.user_id.in_([m.id for m in members])).order_by(Preference.user_id).populate_existing().with_for_update().all()
    old = prior_action(db, user, body.submission_id, digest, "request_create")
    if old:
        return current_action_result(db, user, old)
    if owner is None or owner.status != "active" or owner.email_verified is not True or source.status != "active":
        conflict()
    if body.property_id and body.property_class != source.property_class:
        conflict()
    if body.desired_start_on is not None and not today_dr() <= body.desired_start_on <= today_dr() + timedelta(days=365):
        raise HTTPException(422, "Choose a desired date within the next year")
    query = db.query(ServiceRequest).filter(ServiceRequest.property_id == body.property_id) if body.property_id else db.query(ServiceRequest).filter(ServiceRequest.listing_id == body.listing_id)
    if query.count() >= MAX_SOURCE_REQUESTS or query.filter(ServiceRequest.status == "open").count() >= MAX_OPEN_REQUESTS:
        conflict()
    request = ServiceRequest(property_id=body.property_id, listing_id=body.listing_id, custodian_id=owner.id, requester_id=user.id,
        source_listing_type=source.type if body.listing_id else None, category_id=body.category_id, property_class=body.property_class,
        title=body.title, brief=body.brief, area_label=body.area_label, requester_alias=body.requester_alias,
        desired_start_on=body.desired_start_on, recipient_user_codes=body.recipient_user_codes, scope_sha256=digest, status="open", version=1)
    db.add(request); db.flush()
    for member in members:
        recipient = Recipient(request_id=request.id, provider_id=member.id, user_code=member.user_code,
            member_alias="", status="pending", version=1)
        preference = db.get(Preference, member.id)
        if member.id in {user.id, owner.id} or not eligible(db, recipient, request):
            conflict()
        recipient.member_alias = preference.member_alias
        db.add(recipient)
    record_action(db, user, body, "request_create", request.id, digest, request, versions={"request_version": 1})
    db.commit()
    return request_view(db, user, request)


def managed_query(db, user):
    query = db.query(ServiceRequest)
    if user.role == "admin":
        return query
    registry = exists().where(Property.id == ServiceRequest.property_id, Property.custodian_id == ServiceRequest.custodian_id,
                              Property.custodian_id == user.id)
    if user.role in {"owner", "realtor"}:
        permitted = or_(Listing.owner_id == user.id,
            and_(Listing.owner_id.is_(None), Listing.submitted_by == user.id)) if user.role == "owner" else or_(
                Listing.assigned_realtor_id == user.id, Listing.owner_id == user.id,
                and_(Listing.assigned_realtor_id.is_(None), Listing.owner_id.is_(None), Listing.submitted_by == user.id))
        listing = exists().where(Listing.id == ServiceRequest.listing_id, permitted,
            func.coalesce(Listing.owner_id, Listing.submitted_by) == ServiceRequest.custodian_id,
            or_(Listing.owner_id.is_not(None), exists().where(User.id == Listing.submitted_by, User.role == "owner")))
    else:
        listing = False
    return query.filter(or_(ServiceRequest.custodian_id == user.id, ServiceRequest.requester_id == user.id), or_(registry, listing))


def requests_page(db, user, view, status, number):
    checked_user(user)
    if view == "managed":
        if status not in {"all", "open", "awarded", "cancelled"}:
            raise HTTPException(422, "Choose a managed request status")
        query = managed_query(db, user)
        if status != "all":
            query = query.filter(ServiceRequest.status == status)
    else:
        if status not in {"all", "actionable", "closed", "awarded"}:
            raise HTTPException(422, "Choose a received request status")
        query = db.query(ServiceRequest).join(Recipient, Recipient.request_id == ServiceRequest.id).filter(
            Recipient.provider_id == user.id, Recipient.status != "revoked")
        if status == "actionable":
            query = query.filter(ServiceRequest.status == "open", Recipient.status.in_(["pending", "accepted"]))
        elif status == "closed":
            query = query.filter(Recipient.status.in_(["declined", "closed"]))
        elif status == "awarded":
            query = query.filter(Recipient.status == "awarded")
    result = network.page(query.order_by(ServiceRequest.created_at.desc(), ServiceRequest.id), number,
                          lambda row: request_view(db, user, row, received=view == "received"))
    result["can_create"] = True  # Every current active verified account may manage its own private registry.
    return result


def get_request(db, user, identity, view=None):
    request = db.get(ServiceRequest, identity)
    if request is None:
        unavailable()
    if view == "managed":
        require_managed(db, user, request)
    return request_view(db, user, request, received=view == "received")


def comparison(db, user, identity):
    request = db.get(ServiceRequest, identity)
    if request is None:
        unavailable()
    require_managed(db, user, request)
    recipients = [managed_recipient(db, user, request, row) for row in db.query(Recipient).filter(Recipient.request_id == request.id).order_by(Recipient.id)]
    return {"request_id": str(request.id), "request_version": request.version,
        "groups": [{"currency": currency, "items": [r for r in recipients if r["current_proposal"] and r["current_proposal"]["currency"] == currency]} for currency in ("USD", "DOP")],
        "unquoted": [r for r in recipients if r["current_proposal"] is None]}


def locked_request(db, user, identity):
    hint = db.get(ServiceRequest, identity)
    if hint is None:
        unavailable()
    recipient_hints = db.query(Recipient).filter(Recipient.request_id == identity).all()
    if not managed(db, user, hint) and not any(r.provider_id == user.id and r.status != "revoked" for r in recipient_hints):
        unavailable()
    user = lock_users(db, user, {hint.custodian_id, hint.requester_id} | {r.provider_id for r in recipient_hints})
    source = source_row(db, hint.property_id, hint.listing_id, lock=True)
    db.query(Preference).filter(Preference.user_id.in_([r.provider_id for r in recipient_hints])).order_by(Preference.user_id).populate_existing().with_for_update().all()
    request = db.query(ServiceRequest).filter(ServiceRequest.id == identity).populate_existing().with_for_update().one()
    recipients = db.query(Recipient).filter(Recipient.request_id == identity).order_by(Recipient.id).populate_existing().with_for_update().all()
    db.query(Proposal).filter(Proposal.request_id == identity).order_by(Proposal.id).populate_existing().with_for_update().all()
    return user, request, source, {r.id: r for r in recipients}


def expected(actual, supplied):
    if actual != supplied:
        conflict()


def new_negotiation(db, request, source, recipient):
    if request.status != "open" or not source_current(db, request, source) or not eligible(db, recipient, request):
        conflict()


def close_proposal(db, recipient, state="closed"):
    proposal = current_proposal(db, recipient)
    if proposal and proposal.status in {"submitted", "accepted"}:
        proposal.status = state
        proposal.version += 1
    return proposal


def recipient_action(db, user, request_id, recipient_id, body, operation):
    user, request, source, recipients = locked_request(db, user, request_id)
    recipient = recipients.get(recipient_id)
    if recipient is None:
        unavailable()
    if operation == "recipient_revoke":
        require_managed(db, user, request, source)
    else:
        require_received(db, user, request, recipient)
    digest = fingerprint(operation, recipient_id, body)
    old = prior_action(db, user, body.submission_id, digest, operation)
    if old:
        return current_action_result(db, user, old)
    expected(recipient.version, body.expected_recipient_version)
    if request.status != "open":
        conflict()
    if operation == "recipient_accept":
        new_negotiation(db, request, source, recipient)
        if recipient.status != "pending":
            conflict()
        recipient.status = "accepted"
    else:
        if recipient.status not in {"pending", "accepted"}:
            conflict()
        if operation == "recipient_revoke":
            expected(request.version, body.expected_request_version)
            request.version += 1; request.updated_at = network.now()
        recipient.status = "revoked" if operation == "recipient_revoke" else "declined"
        close_proposal(db, recipient)
    recipient.version += 1
    proposal = current_proposal(db, recipient)
    versions = {"recipient_version": recipient.version}
    if proposal:
        versions["proposal_version"] = proposal.version
    if operation == "recipient_revoke":
        versions["request_version"] = request.version
    action = record_action(db, user, body, operation, recipient.id, digest, request, recipient, proposal, versions)
    db.commit()
    return current_action_result(db, user, action)


def write_proposal(db, user, request_id, recipient_id, body, *, counter=False):
    operation = "owner_counter" if counter else "provider_quote"
    user, request, source, recipients = locked_request(db, user, request_id)
    recipient = recipients.get(recipient_id)
    if recipient is None:
        unavailable()
    if counter:
        require_managed(db, user, request, source)
    else:
        require_received(db, user, request, recipient)
    digest = fingerprint(operation, recipient_id, body)
    old = prior_action(db, user, body.submission_id, digest, operation)
    if old:
        return current_action_result(db, user, old)
    new_negotiation(db, request, source, recipient)
    expected(recipient.version, body.expected_recipient_version)
    if recipient.status != "accepted":
        conflict()
    current = current_proposal(db, recipient)
    expected(current.id if current else None, body.current_proposal_id)
    expected(current.version if current else None, body.expected_proposal_version)
    if counter:
        expected(request.version, body.expected_request_version)
        if current is None or current.status != "submitted":
            conflict()
        if current.kind == "owner_counter" and current.author_id != user.id:
            unavailable()
    if not today_dr() <= body.terms.valid_until <= today_dr() + timedelta(days=365):
        raise HTTPException(422, "Choose proposal validity within the next year")
    count = db.query(Proposal).filter(Proposal.recipient_id == recipient.id).count()
    if count >= MAX_REVISIONS:
        conflict()
    if current and current.status in {"submitted", "accepted"}:
        current.status = "superseded"; current.version += 1
    row = Proposal(request_id=request.id, recipient_id=recipient.id, author_id=user.id,
        parent_proposal_id=current.id if current else None, revision_no=count + 1,
        kind="owner_counter" if counter else "provider_quote", status="submitted", version=1,
        amount=Decimal(body.terms.amount), currency=body.terms.currency, pricing_basis=body.terms.pricing_basis,
        scope_text=body.terms.scope_text, exclusions=body.terms.exclusions, estimated_days=body.terms.estimated_days,
        valid_until=body.terms.valid_until)
    db.add(row); db.flush()
    recipient.current_proposal_id = row.id; recipient.version += 1
    versions = {"recipient_version": recipient.version, "proposal_version": row.version}
    if counter:
        versions["request_version"] = request.version
    action = record_action(db, user, body, operation, recipient.id, digest, request, recipient, row, versions)
    db.commit()
    return current_action_result(db, user, action)


def proposal_action(db, user, proposal_id, body, operation):
    hint = db.get(Proposal, proposal_id)
    if hint is None:
        unavailable()
    user, request, source, recipients = locked_request(db, user, hint.request_id)
    proposal = db.get(Proposal, proposal_id)
    recipient = recipients.get(proposal.recipient_id)
    owner_withdraw = operation == "proposal_withdraw" and proposal.kind == "owner_counter"
    if owner_withdraw:
        require_managed(db, user, request, source)
        if proposal.author_id != user.id:
            unavailable()
    else:
        require_received(db, user, request, recipient)
    digest = fingerprint(operation, proposal_id, body)
    old = prior_action(db, user, body.submission_id, digest, operation)
    if old:
        return current_action_result(db, user, old)
    expected(recipient.version, body.expected_recipient_version)
    expected(proposal.version, body.expected_proposal_version)
    if request.status != "open" or recipient.status != "accepted" or recipient.current_proposal_id != proposal.id or proposal.status != "submitted":
        conflict()
    if operation in {"counter_accept", "counter_decline"} and proposal.kind != "owner_counter":
        conflict()
    if operation == "counter_accept":
        new_negotiation(db, request, source, recipient)
        if today_dr() > proposal.valid_until:
            conflict()
    if operation == "proposal_withdraw" and proposal.author_id != user.id:
        unavailable()
    proposal.status = {"counter_accept": "accepted", "counter_decline": "declined", "proposal_withdraw": "withdrawn"}[operation]
    proposal.version += 1; recipient.version += 1
    action = record_action(db, user, body, operation, proposal.id, digest, request, recipient, proposal,
        {"recipient_version": recipient.version, "proposal_version": proposal.version})
    db.commit()
    return current_action_result(db, user, action)


def close_request(db, user, identity, body, *, award=False):
    operation = "request_award" if award else "request_cancel"
    user, request, source, recipients = locked_request(db, user, identity)
    require_managed(db, user, request, source)
    digest = fingerprint(operation, identity, body)
    old = prior_action(db, user, body.submission_id, digest, operation)
    if old:
        return current_action_result(db, user, old)
    expected(request.version, body.expected_request_version)
    if request.status != "open":
        conflict()
    winner = proposal = None
    if award:
        winner = recipients.get(body.recipient_id)
        if winner is None:
            unavailable()
        proposal = current_proposal(db, winner)
        expected(winner.version, body.expected_recipient_version)
        expected(proposal.id if proposal else None, body.proposal_id)
        expected(proposal.version if proposal else None, body.expected_proposal_version)
        new_negotiation(db, request, source, winner)
        if winner.status != "accepted" or not awardable(proposal):
            conflict()
        if db.query(Project).filter(Project.created_by_id == user.id, Project.submission_id == body.submission_id).first():
            conflict()  # Never attach an existing independently-created project.
        project_body = ProjectCreate(property_id=request.property_id, listing_id=request.listing_id,
            submission_id=body.submission_id, title=body.project_title, summary=body.project_summary,
            property_class=request.property_class, service_category_ids=[request.category_id])
        project = network.construct_project(db, user, project_body, actor_locked=True)
        db.add(Member(project_id=project.id, user_id=winner.provider_id, role="professional", active=True))
        request.project_id = project.id
    request.status = "awarded" if award else "cancelled"
    request.version += 1; request.updated_at = network.now()
    closed_versions = {}
    for recipient in recipients.values():
        if recipient.status in {"pending", "accepted"}:
            current = close_proposal(db, recipient, "awarded" if recipient is winner else "closed")
            recipient.status = "awarded" if recipient is winner else "closed"
            recipient.version += 1
            closed_versions[str(recipient.id)] = {"recipient_version": recipient.version}
            if current:
                closed_versions[str(recipient.id)]["proposal_version"] = current.version
    versions = {"request_version": request.version, "closed_versions": closed_versions}
    if winner:
        versions.update(closed_versions[str(winner.id)])
    action = record_action(db, user, body, operation, request.id, digest, request, winner, proposal, versions)
    db.commit()
    return current_action_result(db, user, action)


def submission(db, user, identity):
    checked_user(user)
    action = db.query(Action).filter(Action.actor_id == user.id, Action.submission_id == identity).first()
    if action is None:
        unavailable()
    value = current_action_result(db, user, action)
    provider = action.request_id is not None and not managed(db, user, db.get(ServiceRequest, action.request_id))
    recipient = db.get(Recipient, action.recipient_id) if action.recipient_id else None
    return {"submission_id": str(action.submission_id), "operation": action.operation, "target_id": str(action.target_id),
        "applied_at": action.created_at, "result_versions": action_versions(action, provider=provider, recipient=recipient), "current": value}


def events(db, user, identity, number, view=None):
    request = db.get(ServiceRequest, identity)
    if request is None:
        unavailable()
    own = None
    is_managed = view != "received" and managed(db, user, request)
    if view == "managed" and not is_managed:
        unavailable()
    query = db.query(Action).filter(Action.request_id == identity)
    if not is_managed:
        own = own_recipient(db, user, request)
        require_received(db, user, request, own)
        query = query.filter(or_(Action.recipient_id == own.id, Action.operation.in_(["request_cancel", "request_award"])))

    def project(row):
        closure = not is_managed and row.operation in {"request_cancel", "request_award"}
        return {"id": str(row.id), "action": "request_closed" if closure else row.operation, "occurred_at": row.created_at,
            "recipient_id": str(own.id) if closure else str(row.recipient_id) if row.recipient_id else None,
            "proposal_id": None if closure else str(row.proposal_id) if row.proposal_id else None,
            "result_versions": action_versions(row, provider=not is_managed, recipient=own)}
    return network.page(query.order_by(Action.created_at, Action.id), number, project)


def proposal_history(db, user, identity, recipient_id, number, view=None):
    request, recipient = db.get(ServiceRequest, identity), db.get(Recipient, recipient_id)
    if request is None or recipient is None or recipient.request_id != identity:
        unavailable()
    is_managed = view != "received" and managed(db, user, request)
    if view == "managed" and not is_managed:
        unavailable()
    if not is_managed:
        require_received(db, user, request, recipient)
    query = db.query(Proposal).filter(Proposal.request_id == identity, Proposal.recipient_id == recipient_id)
    return network.page(query.order_by(Proposal.revision_no.desc(), Proposal.id), number, proposal_view)
