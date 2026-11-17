"""Private, explicit nominations for an existing service request."""

from dataclasses import dataclass
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import Text, and_, cast, exists, or_
from sqlalchemy.orm import aliased

from models.listing import Listing
from models.professional_profile import ProfessionalProfile
from models.property_network import NetworkProperty
from models.service_referral import NetworkServiceReferral as Referral, NetworkServiceReferralEvent as Event
from models.service_workflow import NetworkServiceRecipient as Recipient, NetworkServiceRequest as Request
from models.user import User
from services import property_network as network, service_workflow as workflow


ACTIVE = ("proposed", "offered")
LIMIT = 5


@dataclass
class Scope:
    request: Request
    source: tuple
    recipients: dict


def _read_scope(db, request_id):
    row = db.get(Request, request_id)
    if row is None:
        workflow.unavailable()
    source = workflow.source_row(db, row.property_id, row.listing_id)
    if not workflow.source_current(db, row, source):
        workflow.unavailable()
    recipients = db.query(Recipient).filter(Recipient.request_id == request_id).all()
    return Scope(row, source, {recipient.provider_id: recipient for recipient in recipients})


def _write_scope(db, actor, request_id, other_users=(), nominee=None):
    actor, row, source, recipients = workflow.locked_request(
        db, actor, request_id, additional_providers=other_users, nominated_actor=nominee,
    )
    if not workflow.source_current(db, row, source):
        workflow.unavailable()
    return actor, Scope(row, source, {item.provider_id: item for item in recipients.values()})


def _verified(member):
    return member is not None and member.status == "active" and member.email_verified is True


def _manages(db, actor, scope):
    return _verified(actor) and workflow.managed(db, actor, scope.request, scope.source)


def _sponsors(db, actor, scope):
    if not _verified(actor):
        return False
    recipient = scope.recipients.get(actor.id)
    return recipient is not None and recipient.status == "accepted" and workflow.eligible(db, recipient, scope.request)


def _rows(db, scope):
    return db.query(Referral).filter(Referral.request_id == scope.request.id)


def _capacity(db, scope, *, count_offers=True):
    if scope.request.status != "open" or len(scope.recipients) >= LIMIT:
        return False
    offers = _rows(db, scope).filter(Referral.status == "offered").count() if count_offers else 0
    return len(scope.recipients) + offers < LIMIT


def _matches(db, actor, scope, context):
    request = scope.request
    query = workflow._reviewed_providers(db, actor, scope.source[1], request.category_id, request.property_class, context)
    return query.filter(User.id.notin_({request.requester_id, *scope.recipients}))


def _target(db, actor, scope, row, *, lock=False):
    query = _matches(db, actor, scope, row).filter(User.id == row.provider_id)
    if lock:
        query = query.with_for_update(read=True, of=ProfessionalProfile)
    return query.one_or_none()


def _proposer_current(db, scope, row):
    proposer = db.get(User, row.proposer_id)
    return _manages(db, proposer, scope) or _sponsors(db, proposer, scope)


def _offer_current(db, scope, row):
    manager = db.get(User, row.offered_by_id) if row.offered_by_id else None
    return manager if _manages(db, manager, scope) and _proposer_current(db, scope, row) else None


def _audience(db, actor, scope, row):
    if row.provider_id == actor.id:
        recipient = scope.recipients.get(actor.id)
        has_recipient = recipient is not None and recipient.id == row.recipient_id and recipient.status != "revoked"
        if row.status in {"offered", "declined"} or (row.status == "accepted" and has_recipient):
            return "target"
        workflow.unavailable()
    if _manages(db, actor, scope):
        return "manager"
    if row.proposer_id == actor.id and _sponsors(db, actor, scope):
        return "proposer"
    workflow.unavailable()


def _project(db, actor, scope, row, audience=None):
    audience = audience or _audience(db, actor, scope, row)
    open_request = scope.request.status == "open"
    offerer = _offer_current(db, scope, row) if audience == "target" and row.status == "offered" else None
    can_offer = audience == "manager" and row.status == "proposed" and _capacity(db, scope)
    can_accept = bool(offerer and _capacity(db, scope, count_offers=False) and _target(db, offerer, scope, row))
    return dict(
        id=row.id, version=row.version, status=row.status, category_id=scope.request.category_id,
        property_class=scope.request.property_class, area_id=row.area_id, language=row.language,
        relationship=row.relationship, created_at=row.created_at, updated_at=row.updated_at,
        provider_alias=None if audience == "target" else row.provider_alias,
        can_offer=bool(can_offer and _proposer_current(db, scope, row) and _target(db, actor, scope, row)),
        can_accept=can_accept, can_decline=audience == "target" and row.status == "offered" and open_request,
        can_revoke=audience == "manager" and row.status in ACTIVE and open_request,
        request_id=row.request_id if audience != "target" or row.status == "accepted" else None,
    )


def for_request(db, actor, request_id, number):
    scope = _read_scope(db, request_id)
    manager, sponsor = _manages(db, actor, scope), _sponsors(db, actor, scope)
    if not manager and not sponsor:
        workflow.unavailable()
    query = _rows(db, scope)
    if not manager:
        query = query.filter(Referral.proposer_id == actor.id)
    result = network.page(query.order_by(Referral.created_at.desc(), Referral.id), number,
                          lambda row: _project(db, actor, scope, row, "manager" if manager else "proposer"))
    capacity = _capacity(db, scope)
    return {**result, "can_suggest": sponsor and not manager and capacity, "can_offer": manager and capacity}


def candidates(db, actor, request_id, filters):
    scope = _read_scope(db, request_id)
    if not _manages(db, actor, scope) and not _sponsors(db, actor, scope):
        workflow.unavailable()
    existing = _rows(db, scope).filter(Referral.provider_id == User.id, Referral.status.in_(ACTIVE)).exists()
    query = _matches(db, actor, scope, filters).filter(~existing)
    if not _capacity(db, scope):
        query = query.filter(False)
    return workflow._provider_page(query, filters.page)


def _current_source():
    owner = aliased(User)
    valid_owner = exists().where(owner.id == Request.custodian_id, owner.status == "active",
                                owner.email_verified.is_(True))
    registry = exists().where(
        NetworkProperty.id == Request.property_id, NetworkProperty.status == "active",
        NetworkProperty.current_custodian_id == Request.custodian_id,
        NetworkProperty.property_class == Request.property_class,
    )
    listing = exists().where(
        Listing.id == Request.listing_id, Listing.status == "active", cast(Listing.type, Text) == Request.source_listing_type,
        or_(Listing.owner_id == Request.custodian_id, and_(
            Listing.owner_id.is_(None), Listing.submitted_by == Request.custodian_id,
            exists().where(owner.id == Request.custodian_id, owner.role == "owner").correlate(Request),
        )),
    )
    return and_(Request.custody_closed.is_(False), valid_owner, or_(registry, listing))


def inbox(db, actor, number):
    workflow.checked_user(actor)
    recipient = exists().where(Recipient.id == Referral.recipient_id, Recipient.request_id == Referral.request_id,
                               Recipient.provider_id == actor.id, Recipient.status != "revoked")
    query = db.query(Referral).join(Request, Request.id == Referral.request_id).filter(
        Referral.provider_id == actor.id, _current_source(),
        or_(Referral.status.in_(("offered", "declined")), and_(Referral.status == "accepted", recipient)),
    )
    return network.page(query.order_by(Referral.updated_at.desc(), Referral.id), number,
                        lambda row: _project(db, actor, _read_scope(db, row.request_id), row))


def _event(db, actor, key):
    return db.query(Event).filter(Event.actor_id == actor.id, Event.submission_id == key).one_or_none()


def _event_projection(db, actor, event):
    row = db.get(Referral, event.referral_id)
    if row is None:
        workflow.unavailable()
    return _project(db, actor, _read_scope(db, row.request_id), row)


def submission(db, actor, key):
    workflow.checked_user(actor)
    event = _event(db, actor, key)
    if event is None:
        workflow.unavailable()
    return _event_projection(db, actor, event)


def _replay(db, actor, body, digest, operation):
    event = _event(db, actor, body.submission_id)
    if event is None:
        return None
    result = _event_projection(db, actor, event)
    if event.operation != operation or event.input_sha256 != digest:
        workflow.conflict()
    return result


def _commit(db, actor, scope, row, body, operation, digest):
    db.flush()
    db.add(Event(id=uuid4(), referral_id=row.id, actor_id=actor.id, submission_id=body.submission_id,
                 operation=operation, input_sha256=digest, result_version=row.version))
    # A decline and an acceptance remain visible to their target, while a revoke
    # is written and returned only to the current manager.
    value = _project(db, actor, scope, row)
    db.commit()
    return value


def create(db, actor, request_id, body):
    workflow.checked_user(actor)
    digest = workflow.fingerprint("referral_create", request_id, body)
    replay = _replay(db, actor, body, digest, "create")
    if replay is not None:
        return replay
    scope = _read_scope(db, request_id)
    if not _manages(db, actor, scope) and not _sponsors(db, actor, scope):
        workflow.unavailable()
    provider = db.query(User.id).filter(User.user_code == body.provider_user_code).scalar()
    if provider is None:
        workflow.conflict()
    actor, scope = _write_scope(db, actor, request_id, (provider,))
    replay = _replay(db, actor, body, digest, "create")
    if replay is not None:
        return replay
    manager = _manages(db, actor, scope)
    if not manager and not _sponsors(db, actor, scope):
        workflow.unavailable()
    if body.share_confirmed != manager:
        raise HTTPException(422, "Confirm sharing the brief only when making an offer.")
    if not _capacity(db, scope) or _rows(db, scope).filter(
        Referral.provider_id == provider, Referral.status.in_(ACTIVE),
    ).first():
        workflow.conflict()
    now = network.now()
    row = Referral(id=uuid4(), request_id=request_id, proposer_id=actor.id, provider_id=provider,
                   area_id=body.area_id, language=body.language, relationship=body.relationship,
                   status="offered" if manager else "proposed", offered_by_id=actor.id if manager else None,
                   created_at=now, updated_at=now, version=1)
    match = _target(db, actor, scope, row, lock=True)
    if match is None or match.user_code != body.provider_user_code:
        workflow.conflict()
    row.provider_alias = match.member_alias
    db.add(row)
    return _commit(db, actor, scope, row, body, "create", digest)


def change(db, actor, referral_id, body, operation):
    workflow.checked_user(actor)
    digest = workflow.fingerprint("referral_" + operation, referral_id, body)
    replay = _replay(db, actor, body, digest, operation)
    if replay is not None:
        return replay
    hint = db.get(Referral, referral_id)
    if hint is None:
        workflow.unavailable()
    preview = _read_scope(db, hint.request_id)
    target_action = operation in {"accept", "decline"}
    nominated = hint.provider_id if target_action and hint.status not in {"proposed", "revoked"} and hint.provider_id == actor.id else None
    if (target_action and nominated is None) or (not target_action and not _manages(db, actor, preview)):
        workflow.unavailable()
    actor, scope = _write_scope(db, actor, hint.request_id, tuple(filter(None, (
        hint.provider_id, hint.proposer_id, hint.offered_by_id,
    ))), nominated)
    row = db.query(Referral).filter(Referral.id == referral_id).populate_existing().with_for_update().one()
    replay = _replay(db, actor, body, digest, operation)
    if replay is not None:
        return replay
    audience = _audience(db, actor, scope, row)
    if audience != ("target" if target_action else "manager"):
        workflow.unavailable()
    if body.share_confirmed != (operation == "offer"):
        raise HTTPException(422, "Confirm sharing the brief only when making an offer.")
    workflow.expected(row.version, body.expected_version)
    if scope.request.status != "open":
        workflow.conflict()
    if operation == "offer":
        if row.status != "proposed" or not _capacity(db, scope) or not _proposer_current(db, scope, row):
            workflow.conflict()
        if _target(db, actor, scope, row, lock=True) is None:
            workflow.conflict()
        row.status, row.offered_by_id = "offered", actor.id
    elif operation == "accept":
        offerer = _offer_current(db, scope, row)
        if row.status != "offered" or offerer is None or not _capacity(db, scope, count_offers=False):
            workflow.conflict()
        match = _target(db, offerer, scope, row, lock=True)
        if match is None:
            workflow.conflict()
        recipient = Recipient(id=uuid4(), request_id=row.request_id, provider_id=actor.id, user_code=match.user_code,
                              member_alias=match.member_alias, status="accepted", version=1)
        db.add(recipient)
        db.flush()
        scope.recipients[actor.id] = recipient
        scope.request.version += 1
        scope.request.updated_at = network.now()
        row.status, row.recipient_id = "accepted", recipient.id
    elif operation == "decline":
        if row.status != "offered":
            workflow.conflict()
        row.status = "declined"
    else:
        if row.status not in ACTIVE:
            workflow.conflict()
        row.status = "revoked"
    row.version += 1
    row.updated_at = network.now()
    return _commit(db, actor, scope, row, body, operation, digest)
