from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import aliased

from models.listing import Listing
from models.property_network import NetworkBlock
from models.shortlist import ClientShortlist as Document, ClientShortlistGrant as Grant, ClientShortlistItem as Item
from models.user import User
from schemas.shortlist import (OwnerShortlistView, ReceivedShortlistView, ShareView, ShortlistItemView,
                               ShortlistList, ShortlistSummary)
from services.property_network import blocked, now
from services.service_workflow import lock_users


MAX_OWNED = 30
MAX_RECEIVED = 50
ROLES = ("buyer", "realtor")


def unavailable():
    raise HTTPException(404, "Shortlist or sharing selection is unavailable")


def _actor(db, user, others=()):
    actor = lock_users(db, user, others)
    if actor.role not in ROLES:
        raise HTTPException(403, "Shortlist access requires a buyer or Realtor account")
    return actor


def _own(db, user_id, identifier, *, lock=False):
    query = db.query(Document).filter(Document.id == identifier, Document.owner_id == user_id,
                                      Document.archived_at.is_(None))
    document = (query.with_for_update() if lock else query).first()
    if document is None:
        unavailable()
    return document


def _latest(db, identifier):
    return db.query(Grant).filter(Grant.shortlist_id == identifier).order_by(Grant.generation.desc()).first()


def _open(instant):
    return and_(Grant.revoked_at.is_(None), Grant.invalidated_at.is_(None), Grant.expires_at > instant)


def _live(db, instant):
    buyer = aliased(User, name="shortlist_buyer")
    realtor = aliased(User, name="shortlist_realtor")
    pair_is_blocked = exists().where(or_(
        and_(NetworkBlock.blocker_id == buyer.id, NetworkBlock.blocked_id == realtor.id),
        and_(NetworkBlock.blocker_id == realtor.id, NetworkBlock.blocked_id == buyer.id),
    ))
    return db.query(Document, Grant).select_from(Document).join(Grant, Grant.shortlist_id == Document.id).join(
        buyer, buyer.id == Grant.buyer_id).join(realtor, realtor.id == Grant.realtor_id).filter(
        Document.archived_at.is_(None), Document.buyer_id == buyer.id, _open(instant),
        buyer.role == "buyer", realtor.role == "realtor", buyer.assigned_realtor_id == realtor.id,
        buyer.status == "active", buyer.email_verified.is_(True), realtor.status == "active",
        realtor.email_verified.is_(True), ~pair_is_blocked, or_(
            and_(Document.owner_id == buyer.id, Grant.recipient_id == realtor.id),
            and_(Document.owner_id == realtor.id, Grant.recipient_id == buyer.id),
        ),
    )


def _recipient(db, actor, member_code):
    target = db.query(User.id).filter(User.user_code == member_code)
    if actor.role == "buyer":
        target = target.filter(User.id == actor.assigned_realtor_id, User.role == "realtor")
    else:
        target = target.filter(User.assigned_realtor_id == actor.id, User.role == "buyer")
    target_id = db.execute(target.statement).scalar_one_or_none()
    if target_id is None:
        unavailable()

    actor = _actor(db, actor, (target_id,))
    peer = db.get(User, target_id)
    buyer, realtor = (actor, peer) if actor.role == "buyer" else (peer, actor)
    if (peer.user_code != member_code or peer.status != "active" or not peer.email_verified
            or buyer.role != "buyer" or realtor.role != "realtor" or buyer.assigned_realtor_id != realtor.id
            or blocked(db, buyer.id, realtor.id)):
        unavailable()
    return actor, peer, buyer, realtor


def _item(listing_id, title, location, transaction):
    return ShortlistItemView(id=listing_id, title=title, location=location, transaction=transaction)


def _items(db, document_id):
    rows = db.execute(select(Listing.id, Listing.title, Listing.location, Listing.transaction)
                      .join(Item, Item.listing_id == Listing.id)
                      .where(Item.shortlist_id == document_id, Listing.status == "active")
                      .order_by(Item.position))
    return [_item(*row) for row in rows]


def _replace_items(db, document_id, listing_ids):
    active = db.execute(select(Listing.id).where(Listing.id.in_(listing_ids), Listing.status == "active")).scalars()
    if set(active) != set(listing_ids):
        unavailable()
    db.query(Item).filter(Item.shortlist_id == document_id).delete(synchronize_session=False)
    db.add_all(Item(shortlist_id=document_id, listing_id=identifier, position=position)
               for position, identifier in enumerate(listing_ids))


def _document_fields(document):
    return {"version": document.version, "title": document.title,
            "shared_notes": document.shared_notes, "shared_preferences": document.shared_preferences}


def _owner_view(db, document, instant):
    grant = _latest(db, document.id)
    share = None
    if grant is not None:
        if grant.revoked_at is not None:
            state = "revoked"
        elif grant.expires_at <= instant:
            state = "expired"
        elif _live(db, instant).filter(Grant.id == grant.id).with_entities(Grant.id).first() is not None:
            state = "active"
        else:
            state = "unavailable"
        share = ShareView(id=grant.id, recipient_user_code=grant.recipient_user_code,
                          expires_at=grant.expires_at, state=state)
    return OwnerShortlistView(id=document.id, share=share, items=_items(db, document.id), **_document_fields(document))


def _set_fields(document, body, instant):
    document.title = body.title
    document.shared_notes = body.shared_notes
    document.shared_preferences = body.shared_preferences
    document.updated_at = instant


def _check_version(document, expected):
    if document.version != expected:
        raise HTTPException(409, "Shortlist changed. Refresh before saving.")


def _apply_update(db, document, body, instant):
    _check_version(document, body.expected_version)
    if "listing_ids" in body.model_fields_set:
        _replace_items(db, document.id, body.listing_ids)
    _set_fields(document, body, instant)
    document.version += 1
    db.flush()


def owned(db, user):
    latest_expiry = (select(Grant.expires_at).where(Grant.shortlist_id == Document.id)
                     .order_by(Grant.generation.desc()).limit(1).correlate(Document).scalar_subquery())
    rows = db.execute(select(Document.id, Document.title, latest_expiry.label("expires_at"))
                      .where(Document.owner_id == user.id, Document.archived_at.is_(None))
                      .order_by(Document.updated_at.desc(), Document.id))
    return ShortlistList(items=[ShortlistSummary(id=row.id, title=row.title, expires_at=row.expires_at) for row in rows])


def received(db, user):
    rows = (_live(db, now()).filter(Grant.recipient_id == user.id)
            .with_entities(Grant.id, Document.title, Grant.expires_at)
            .order_by(Document.updated_at.desc(), Grant.id).all())
    return ShortlistList(items=[ShortlistSummary(id=row.id, title=row.title, expires_at=row.expires_at) for row in rows])


def create(db, user, body):
    actor = _actor(db, user)
    count = db.execute(select(func.count()).select_from(Document).where(
        Document.owner_id == actor.id, Document.archived_at.is_(None)
    )).scalar_one()
    if count >= MAX_OWNED:
        raise HTTPException(409, "Archive a shortlist before creating another")
    instant = now()
    document = Document(id=uuid4(), owner_id=actor.id, buyer_id=actor.id if actor.role == "buyer" else None,
                        version=1, created_at=instant)
    _set_fields(document, body, instant)
    db.add(document)
    db.flush()
    _replace_items(db, document.id, body.listing_ids)
    db.flush()
    result = _owner_view(db, document, instant)
    db.commit()
    return result


def read_owned(db, user, identifier):
    return _owner_view(db, _own(db, user.id, identifier), now())


def update_owned(db, user, identifier, body):
    actor = _actor(db, user)
    document = _own(db, actor.id, identifier, lock=True)
    instant = now()
    _apply_update(db, document, body, instant)
    result = _owner_view(db, document, instant)
    db.commit()
    return result


def _received_view(db, user_id, identifier, instant):
    rows = (_live(db, instant).filter(Grant.id == identifier, Grant.recipient_id == user_id)
            .outerjoin(Item, Item.shortlist_id == Document.id)
            .outerjoin(Listing, and_(Listing.id == Item.listing_id, Listing.status == "active"))
            .add_columns(Listing.id, Listing.title, Listing.location, Listing.transaction)
            .order_by(Item.position).all())
    if not rows:
        unavailable()
    document, grant = rows[0][:2]
    return ReceivedShortlistView(id=grant.id, expires_at=grant.expires_at, **_document_fields(document),
                                 items=[_item(*row[2:]) for row in rows if row[2] is not None])


def read_received(db, user, identifier):
    return _received_view(db, user.id, identifier, now())


def update_received(db, user, identifier, body):
    pair = db.execute(select(Grant.shortlist_id, Grant.buyer_id, Grant.realtor_id).where(
        Grant.id == identifier, Grant.recipient_id == user.id
    )).one_or_none()
    if pair is None:
        unavailable()
    actor = _actor(db, user, (pair.buyer_id, pair.realtor_id))
    locked_id = db.execute(select(Document.id).where(Document.id == pair.shortlist_id,
                           Document.archived_at.is_(None)).with_for_update()).scalar_one_or_none()
    if locked_id is None:
        unavailable()
    instant = now()
    authorized = _live(db, instant).filter(Grant.id == identifier, Grant.recipient_id == actor.id).first()
    if authorized is None:
        unavailable()
    document, _ = authorized
    _apply_update(db, document, body, instant)
    result = _received_view(db, actor.id, identifier, instant)
    db.commit()
    return result


def share(db, user, identifier, body):
    actor, peer, buyer, realtor = _recipient(db, user, body.recipient_user_code)
    document = _own(db, actor.id, identifier, lock=True)
    if document.buyer_id is not None and document.buyer_id != buyer.id:
        raise HTTPException(409, "Create a separate shortlist for this client")

    instant = now()
    duration = timedelta(days=body.expires_in_days)
    latest = _latest(db, identifier)
    grant = _live(db, instant).filter(Grant.shortlist_id == identifier).with_entities(Grant).first()
    if grant is not None:
        if grant.recipient_id != peer.id or grant.expires_at - grant.created_at != duration:
            raise HTTPException(409, "Revoke the current link before creating another")
        return _owner_view(db, document, instant)

    count = db.execute(select(func.count()).select_from(Grant).join(Document, Document.id == Grant.shortlist_id)
                       .where(Grant.recipient_id == peer.id, Document.archived_at.is_(None), _open(instant))).scalar_one()
    if count >= MAX_RECEIVED:
        raise HTTPException(409, "This member has reached the active shortlist limit")

    # Keep prior grant identities permanently closed, even after eligibility changes.
    db.query(Grant).filter(Grant.shortlist_id == identifier, _open(instant)).update(
        {Grant.invalidated_at: instant}, synchronize_session=False)
    document.buyer_id = buyer.id
    document.updated_at = instant
    db.add(Grant(id=uuid4(), shortlist_id=document.id, buyer_id=buyer.id, realtor_id=realtor.id,
                 recipient_id=peer.id, recipient_user_code=body.recipient_user_code,
                 generation=latest.generation + 1 if latest is not None else 1,
                 created_at=instant, expires_at=instant + duration))
    db.flush()
    result = _owner_view(db, document, instant)
    db.commit()
    return result


def revoke(db, user, identifier, grant_id):
    actor = _actor(db, user)
    document = _own(db, actor.id, identifier, lock=True)
    grant = db.query(Grant).filter(Grant.id == grant_id, Grant.shortlist_id == identifier).first()
    if grant is None:
        unavailable()
    instant = now()
    if grant.revoked_at is None:
        grant.revoked_at = instant
        document.updated_at = instant
        db.flush()
    result = _owner_view(db, document, instant)
    db.commit()
    return result


def archive(db, user, identifier, expected):
    actor = _actor(db, user)
    document = _own(db, actor.id, identifier, lock=True)
    _check_version(document, expected)
    document.archived_at = document.updated_at = now()
    document.version += 1
    db.commit()
