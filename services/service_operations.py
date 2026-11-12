"""Private, current service request decisions projected without making changes."""
from fastapi import HTTPException
from sqlalchemy import Date, Text, TIMESTAMP, and_, case, cast, exists, false, func, null, or_, select, true, union_all
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.orm import aliased

from models.listing import Listing
from models.property_network import NetworkProject as Project, NetworkProjectMember as Member, NetworkProperty as Property
from models.service_visit import NetworkServiceWork as Work
from models.service_workflow import (NetworkServicePreference as Preference, NetworkServiceProposal as Proposal,
    NetworkServiceRecipient as Recipient, NetworkServiceRequest as ServiceRequest)
from models.user import User
from services import property_network as network, service_workflow as workflow
from services.maintenance_reminders import TIME_ZONE
from services.service_visit import DR_TIMEZONE


PRIORITY = {"completion_review": 0, "visit_completion_due": 1, "visit_response": 2, "counter_ready": 3,
            "quote_ready": 4, "invitation": 5, "offer_expired": 6, "ready_to_quote": 7, "visit_confirmed": 8,
            "schedule_needed": 9, "request_needs_attention": 10, "invitation_unavailable": 11, "counter_pending": 12,
            "counter_accepted": 13, "quote_submitted": 14, "visit_proposed": 15, "awaiting_replies": 16}


def _sources(db, actor, view, source_kind, source_id):
    custodian = aliased(User, name="operations_custodian")
    private = ServiceRequest.property_id.is_not(None)
    registry_current = and_(private, Property.status == "active", Property.custodian_id == ServiceRequest.custodian_id,
                            Property.property_class == ServiceRequest.property_class)
    listing_current = and_(ServiceRequest.listing_id.is_not(None), Listing.status == "active",
        cast(Listing.type, Text) == ServiceRequest.source_listing_type,
        or_(and_(Listing.owner_id == ServiceRequest.custodian_id, custodian.role.in_(("owner", "realtor"))),
            and_(Listing.owner_id.is_(None), Listing.submitted_by == ServiceRequest.custodian_id, custodian.role == "owner")))
    if view == "managed":
        visible = ServiceRequest.id.in_(workflow.managed_query(db, actor).with_entities(ServiceRequest.id).statement)
    else:
        visible = exists().where(Recipient.request_id == ServiceRequest.id, Recipient.provider_id == actor.id,
                                 Recipient.status.in_(("pending", "accepted", "awarded")))
    query = select(ServiceRequest.id.label("request_id"), case((private, "private"), else_="listing").label("source_kind"),
        func.coalesce(ServiceRequest.property_id, ServiceRequest.listing_id).label("source_id"),
        case((private, Property.title), else_=Listing.title).label("source_title")).select_from(ServiceRequest).join(
        custodian, custodian.id == ServiceRequest.custodian_id).outerjoin(Property, Property.id == ServiceRequest.property_id).outerjoin(
        Listing, Listing.id == ServiceRequest.listing_id).where(
        visible, ServiceRequest.status.in_(("open", "awarded")), custodian.status == "active",
        custodian.email_verified.is_(True), custodian.role != "admin", or_(registry_current, listing_current),
    )
    if source_kind is not None:
        field = ServiceRequest.property_id if source_kind == "private" else ServiceRequest.listing_id
        query = query.where(field == source_id)
    return query.cte("operations_current_sources")


def _eligible(provider):
    return and_(provider.status == "active", provider.email_verified.is_(True), provider.role != "admin",
                provider.user_code > 0, Preference.service_opt_in.is_(True),
                Preference.category_ids.contains(func.jsonb_build_array(ServiceRequest.category_id)),
                Preference.property_class_ids.contains(func.jsonb_build_array(ServiceRequest.property_class)))


def _current_offer():
    return and_(Proposal.id == Recipient.current_proposal_id, Proposal.recipient_id == Recipient.id,
                Proposal.request_id == ServiceRequest.id)


def _action_list(*choices):
    names = [case((allowed, code), else_=cast(null(), Text)) for code, allowed in choices]
    return func.array_remove(array(names, type_=Text), cast(null(), Text))


def _card(sources, stage, actions, *, valid_until=None, starts_at=None, priority=None):
    expiry = cast(null(), Date) if valid_until is None else valid_until
    visit = cast(null(), TIMESTAMP(timezone=True)) if starts_at is None else starts_at
    rank = case(PRIORITY, value=stage, else_=99) if priority is None else priority
    return select(ServiceRequest.id.label("request_id"), ServiceRequest.title.label("title"), stage.label("stage"),
                  ServiceRequest.desired_start_on.label("desired_start_on"), expiry.label("quote_valid_until"),
                  visit.label("starts_at"), actions.label("actions"), sources.c.source_kind, sources.c.source_id,
                  sources.c.source_title, rank.label("priority")).select_from(ServiceRequest).join(
        sources, sources.c.request_id == ServiceRequest.id)


def _managed(sources, actor, today):
    provider = aliased(User, name="operations_invited_provider")
    eligible_offer = and_(Recipient.status == "accepted", _eligible(provider))
    submitted_quote = and_(Proposal.kind == "provider_quote", Proposal.status == "submitted")
    accepted_counter = and_(Proposal.kind == "owner_counter", Proposal.status == "accepted")
    submitted_counter = and_(Proposal.kind == "owner_counter", Proposal.status == "submitted")
    own_counter = and_(submitted_counter, Proposal.author_id == actor.id)
    available = or_(submitted_quote, accepted_counter)
    relevant = or_(available, submitted_counter)
    offers = select(
        func.min(case((and_(eligible_offer, available, Proposal.valid_until >= today), Proposal.valid_until))).label("award_until"),
        func.min(case((and_(eligible_offer, submitted_counter, Proposal.valid_until >= today), Proposal.valid_until))).label("counter_until"),
        func.max(case((and_(eligible_offer, relevant, Proposal.valid_until < today), Proposal.valid_until))).label("expired_on"),
        func.coalesce(func.bool_or(and_(eligible_offer, Proposal.revision_no < workflow.MAX_REVISIONS,
                                      or_(submitted_quote, own_counter))), False).label("can_counter"),
        func.coalesce(func.bool_or(and_(Recipient.status == "accepted", own_counter)), False).label("can_withdraw"),
        func.coalesce(func.bool_or(Recipient.status.in_(("pending", "accepted"))), False).label("has_open_recipient"),
    ).select_from(Recipient).outerjoin(Proposal, _current_offer()).outerjoin(provider, provider.id == Recipient.provider_id).outerjoin(
        Preference, Preference.user_id == Recipient.provider_id).where(Recipient.request_id == ServiceRequest.id).correlate(
        ServiceRequest).lateral("operations_current_offers")
    award = offers.c.award_until.is_not(None)
    counter = offers.c.counter_until.is_not(None)
    expired = offers.c.expired_on.is_not(None)
    stage = case((award, "quote_ready"), (counter, "counter_pending"), (expired, "offer_expired"),
                 (offers.c.has_open_recipient, "awaiting_replies"), else_="request_needs_attention")
    actions = _action_list(("compare_quotes", true()), ("award", award), ("counter", offers.c.can_counter),
                           ("withdraw_offer", offers.c.can_withdraw), ("cancel_request", true()))
    return _card(sources, stage, actions, valid_until=func.coalesce(
        offers.c.award_until, offers.c.counter_until, offers.c.expired_on)).join(offers, true()).where(ServiceRequest.status == "open")


def _received(sources, actor, today):
    provider = aliased(User, name="operations_own_provider")
    pending = Recipient.status == "pending"
    accepted = Recipient.status == "accepted"
    eligible = _eligible(provider)
    submitted_counter = and_(accepted, Proposal.kind == "owner_counter", Proposal.status == "submitted")
    accepted_counter = and_(accepted, Proposal.kind == "owner_counter", Proposal.status == "accepted")
    submitted_quote = and_(accepted, Proposal.kind == "provider_quote", Proposal.status == "submitted")
    has_offer = or_(submitted_counter, accepted_counter, submitted_quote)
    expired = and_(has_offer, Proposal.valid_until < today)
    can_quote = and_(accepted, eligible, or_(Proposal.id.is_(None), Proposal.revision_no < workflow.MAX_REVISIONS))
    stage = case((and_(pending, eligible), "invitation"), (pending, "invitation_unavailable"),
        (expired, "offer_expired"), (and_(submitted_counter, eligible), "counter_ready"),
        (submitted_counter, "invitation_unavailable"), (accepted_counter, "counter_accepted"),
        (submitted_quote, "quote_submitted"), (can_quote, "ready_to_quote"), else_="invitation_unavailable")
    actions = _action_list(("accept_invitation", and_(pending, eligible)), ("decline_invitation", true()),
        ("submit_quote", can_quote), ("withdraw_offer", and_(submitted_quote, Proposal.author_id == actor.id)),
        ("accept_counter", and_(submitted_counter, eligible, Proposal.valid_until >= today)), ("decline_counter", submitted_counter))
    return _card(sources, stage, actions, valid_until=case((has_offer, Proposal.valid_until))).join(
        Recipient, and_(Recipient.request_id == ServiceRequest.id, Recipient.provider_id == actor.id)).join(
        provider, provider.id == Recipient.provider_id).outerjoin(Preference, Preference.user_id == Recipient.provider_id).outerjoin(
        Proposal, _current_offer()).where(ServiceRequest.status == "open", Recipient.status.in_(("pending", "accepted")))


def _awarded(sources, actor, view, instant):
    provider = aliased(User, name="operations_awarded_provider")
    side = "owner" if view == "managed" else "provider"
    participant = ServiceRequest.custodian_id == actor.id if side == "owner" else Recipient.provider_id == actor.id
    status = func.coalesce(Work.status, "unscheduled")
    schedule = status.in_(("unscheduled", "cancelled"))
    proposed, confirmed = status == "proposed", status == "confirmed"
    review = status == "completion_requested"
    respond = and_(proposed, Work.proposed_by != side)
    completion_due = and_(confirmed, Work.confirmed_starts_at <= instant) if side == "provider" else false()
    stage = case((schedule, "schedule_needed"), (respond, "visit_response"), (proposed, "visit_proposed"),
                 (completion_due, "visit_completion_due"), (confirmed, "visit_confirmed"), else_="completion_review")
    owner_review = review if side == "owner" else false()
    actions = _action_list(("propose_visit", or_(schedule, confirmed)), ("accept_visit", and_(respond, Work.proposed_starts_at > instant)),
        ("decline_visit", respond), ("cancel_visit", or_(proposed, confirmed)), ("request_completion", completion_due),
        ("approve_completion", owner_review), ("request_changes", owner_review))
    rank = case((review, 17), else_=case(PRIORITY, value=stage, else_=99)) if side == "provider" else None
    return _card(sources, stage, actions, starts_at=Work.confirmed_starts_at, priority=rank).join(
        Project, Project.id == ServiceRequest.project_id).join(
        Recipient, and_(Recipient.request_id == ServiceRequest.id, Recipient.status == "awarded")).join(
        provider, provider.id == Recipient.provider_id).join(
        Member, and_(Member.project_id == Project.id, Member.user_id == Recipient.provider_id,
                     Member.role == "professional", Member.active.is_(True))).outerjoin(Work, Work.request_id == ServiceRequest.id).where(
        ServiceRequest.status == "awarded", participant, Project.status != "archived", Project.owner_id == ServiceRequest.custodian_id,
        Project.property_id.is_not_distinct_from(ServiceRequest.property_id), Project.listing_id.is_not_distinct_from(ServiceRequest.listing_id),
        provider.status == "active", provider.email_verified.is_(True), provider.role != "admin", status != "completed",
    )


def _order(cards):
    day = func.coalesce(cards.c.quote_valid_until, cast(func.timezone(TIME_ZONE, cards.c.starts_at), Date), cards.c.desired_start_on)
    return cards.c.priority, day.nullslast(), cards.c.starts_at.nullslast(), cards.c.request_id


def _view(row, view):
    return {"request_id": str(row.request_id), "title": row.title, "stage": row.stage,
            "desired_start_on": row.desired_start_on, "quote_valid_until": row.quote_valid_until,
            "starts_at": row.starts_at, "actions": list(row.actions), "source": None if view == "received" else {
                "kind": row.source_kind, "id": str(row.source_id), "title": row.source_title}}


def queue(db, user, view, page, source_kind=None, source_id=None):
    if (source_kind is None) != (source_id is None) or (view == "received" and source_kind is not None):
        raise HTTPException(422, "Choose a complete managed property filter")
    workflow.checked_user(user)
    if user.role == "admin":
        workflow.unavailable()
    actor = workflow.lock_users(db, user, ())
    if actor.role == "admin":
        workflow.unavailable()
    instant = network.now()
    today = instant.astimezone(DR_TIMEZONE).date()
    sources = _sources(db, actor, view, source_kind, source_id)
    pre_award = _managed(sources, actor, today) if view == "managed" else _received(sources, actor, today)
    cards = union_all(pre_award, _awarded(sources, actor, view, instant)).cte("operations_visible_cards")
    items = select(cards).order_by(*_order(cards)).offset((page - 1) * network.PAGE_SIZE).limit(network.PAGE_SIZE).subquery()
    total = select(func.count().label("total")).select_from(cards).subquery()
    # Count and page use one current source/ACL snapshot, including an empty page.
    rows = db.execute(select(total.c.total, *items.c).select_from(total.outerjoin(items, true())).order_by(*_order(items))).all()
    return {"view": view, "as_of": today, "time_zone": TIME_ZONE,
            "items": [_view(row, view) for row in rows if row.request_id is not None],
            "total": rows[0].total, "page": page, "page_size": network.PAGE_SIZE}
