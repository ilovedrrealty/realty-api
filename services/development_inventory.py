"""Listing-scoped version control for developer-verified offerings."""

from fastapi import HTTPException
from sqlalchemy import and_, select

from models.development_inventory import DevelopmentInventory as Inventory, DevelopmentInventoryEvent as Event, DevelopmentInventoryRevision as Revision
from models.listing import Listing
from schemas.development_inventory import AdminInventory, Draft, Phase, Project, PublishedInventory, RevisionSnapshot, Unit
from services.property_management import today_dr
from services.property_network import now
from services.service_workflow import lock_users


def unavailable():
    raise HTTPException(404, "Development inventory not found")


def conflict():
    raise HTTPException(409, "The development inventory changed. Refresh and try again.")


def _listing_eligible():
    return and_(Listing.status == "active", Listing.transaction.in_(("sale", "both")))


def _eligible_listing(db, identifier, *, lock=False):
    lookup = select(Listing.id).where(Listing.id == identifier, _listing_eligible())
    if lock:
        lookup = lookup.with_for_update()
    if db.execute(lookup).scalar_one_or_none() is None:
        unavailable()


def _revision(db, identifier, number):
    revision = db.get(Revision, (identifier, number))
    if revision is None:
        unavailable()
    return revision


def _public(inventory, revision, draft):
    evidence = draft.evidence
    current = evidence.verified_on <= today_dr() <= evidence.availability_valid_through
    phases = [
        Phase(**phase.model_dump(exclude={"units"}), units=[
            Unit(
                code=unit.code, floor=unit.floor, area_m2=unit.area_m2, price=unit.price, currency=unit.currency,
                status=unit.status if current else "unconfirmed",
                floor_plan_url=unit.floor_plan_url if unit.floor_plan_approved else None,
            ) for unit in phase.units
        ]) for phase in draft.project.phases
    ]
    # The evidence object is intentionally absent. Only its public dates cross
    # this boundary, and only the separately approved unit links are included.
    return PublishedInventory(
        listing_id=inventory.listing_id, revision=revision.revision, published_at=inventory.published_at,
        verified_on=evidence.verified_on, availability_valid_through=evidence.availability_valid_through,
        availability_current=current, project=Project(**draft.project.model_dump(exclude={"phases"}), phases=phases),
    )


def _admin_snapshot(db, inventory, current=None):
    if current is None:
        current = _revision(db, inventory.listing_id, inventory.draft_revision)
    draft = Draft.model_validate(current.snapshot)
    published = None
    if inventory.published_revision is not None:
        saved = current if inventory.published_revision == current.revision else _revision(
            db, inventory.listing_id, inventory.published_revision)
        published = _public(inventory, saved, draft if saved is current else Draft.model_validate(saved.snapshot))
    return AdminInventory(listing_id=inventory.listing_id, version=inventory.version,
                          draft_revision=inventory.draft_revision, reviewed_revision=inventory.reviewed_revision,
                          published_revision=inventory.published_revision, draft=draft, published=published)


def admin_read(db, identifier):
    _eligible_listing(db, identifier)
    inventory = db.get(Inventory, identifier)
    return _admin_snapshot(db, inventory) if inventory else AdminInventory(listing_id=identifier, version=0)


def preview(db, identifier, revision_number):
    _eligible_listing(db, identifier)
    revision = _revision(db, identifier, revision_number)
    return RevisionSnapshot(revision=revision.revision, saved_at=revision.saved_at, **revision.snapshot)


def _change_scope(db, user, identifier, expected):
    actor = lock_users(db, user, ())
    if actor.role != "admin":
        raise HTTPException(403, "Admin access required")
    # Serialize edits, including two concurrent first drafts, with the listing
    # lifecycle. Its existing status and transaction remain the public gate.
    _eligible_listing(db, identifier, lock=True)
    inventory = db.get(Inventory, identifier)
    if (inventory.version if inventory else 0) != expected:
        conflict()
    return actor, inventory


def _finish(db, inventory, draft, actor, action, *, event_revision=None):
    db.add(Event(listing_id=inventory.listing_id, version=inventory.version, action=action,
                 revision=event_revision if event_revision is not None else draft.revision,
                 actor_id=actor.id, created_at=now()))
    result = _admin_snapshot(db, inventory, draft)
    db.commit()
    return result


def save_draft(db, user, identifier, body):
    actor, inventory = _change_scope(db, user, identifier, body.expected_version)
    if inventory is None:
        inventory = Inventory(listing_id=identifier, version=1, draft_revision=1)
        db.add(inventory)
        db.flush()
    else:
        inventory.version += 1
        inventory.draft_revision += 1
        inventory.reviewed_revision = None
    draft = Revision(listing_id=identifier, revision=inventory.draft_revision,
                     snapshot=body.model_dump(mode="json", exclude={"expected_version"}), saved_by=actor.id, saved_at=now())
    db.add(draft)
    db.flush()
    return _finish(db, inventory, draft, actor, "draft")


def _reviewable(record):
    draft = Draft.model_validate(record.snapshot)
    evidence, project = draft.evidence, draft.project
    if (not evidence.source_reference.strip() or evidence.verified_on is None
            or evidence.availability_valid_through is None or evidence.verified_on > today_dr()):
        raise HTTPException(422, "Supply a developer source, verification date and availability cutoff")
    if (not project.name_en or not project.name_es or not any(phase.units for phase in project.phases)
            or any(not phase.name_en or not phase.name_es for phase in project.phases)
            or any(not stage.label_en or not stage.label_es for stage in project.payment_stages)
            or any(not update.title_en or not update.title_es for update in project.updates)):
        raise HTTPException(422, "Add bilingual project and phase names, unit inventory and any stage or update titles")


def review(db, user, identifier, body):
    actor, inventory = _change_scope(db, user, identifier, body.expected_version)
    if inventory.draft_revision != body.revision or inventory.reviewed_revision == body.revision:
        conflict()
    latest = _revision(db, identifier, inventory.draft_revision)
    _reviewable(latest)
    inventory.version += 1
    inventory.reviewed_revision = latest.revision
    return _finish(db, inventory, latest, actor, "review")


def publish(db, user, identifier, body):
    actor, inventory = _change_scope(db, user, identifier, body.expected_version)
    if (body.revision != inventory.draft_revision or body.revision != inventory.reviewed_revision
            or body.revision == inventory.published_revision):
        conflict()
    latest = _revision(db, identifier, inventory.draft_revision)
    _reviewable(latest)
    inventory.version += 1
    inventory.published_revision = latest.revision
    inventory.published_at = now()
    return _finish(db, inventory, latest, actor, "publish")


def withdraw(db, user, identifier, body):
    actor, inventory = _change_scope(db, user, identifier, body.expected_version)
    previous = inventory.published_revision
    if previous is None:
        conflict()
    latest = _revision(db, identifier, inventory.draft_revision)
    inventory.version += 1
    inventory.published_revision = inventory.published_at = None
    return _finish(db, inventory, latest, actor, "withdraw", event_revision=previous)


def public_read(db, identifier):
    row = db.execute(
        select(Inventory, Revision).select_from(Listing).join(Inventory, Inventory.listing_id == Listing.id)
        .join(Revision, and_(Revision.listing_id == Inventory.listing_id, Revision.revision == Inventory.published_revision))
        .where(Listing.id == identifier, _listing_eligible(), Inventory.published_revision.is_not(None))
    ).one_or_none()
    if row is None:
        unavailable()
    inventory, revision = row
    return _public(inventory, revision, Draft.model_validate(revision.snapshot))
