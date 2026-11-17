"""Search and import Dominican trade contacts without exposing the public directory."""

from collections import defaultdict
from hashlib import sha256
import json
import re
from uuid import uuid4

from sqlalchemy import func, or_, select, text

from models.trade_contact import TradeContact, TradeContactSource
from schemas.trade_contacts import TradeContactImportResult


def _normalized(value):
    return " ".join(re.findall(r"\w+", (value or "").casefold()))


def _key(*parts):
    return sha256(json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def contact_identity(entry):
    # An organization can operate in several trades. Registry identities stay
    # distinct even when two organizations share a brand or contact details.
    if entry.external_id:
        return _key("registry", _normalized(entry.source_authority), entry.external_id.casefold(),
                    _normalized(entry.category))
    return _key("organization", _normalized(entry.name), _normalized(entry.category),
                _normalized(entry.province), _normalized(entry.locality))


def _source_identity(entry):
    return _key(_normalized(entry.source_authority), entry.external_id or entry.source_url)


def _fields(details):
    return {key: details.get(key) for key in ("name", "category", "province", "locality")}


def _preserve_details(previous, observation, same_source):
    if previous is None:
        return observation
    refresh = same_source and observation["verified_on"] >= previous["verified_on"]
    if refresh:
        return {**previous, **{key: value for key, value in observation.items() if value is not None}}
    portfolio_fields = {"name", "category", "province", "locality", "subtype", "contact_name", "contact_title",
                        "phone", "email", "website", "address", "postal_code", "service_area"}
    return {**previous, **{key: value for key, value in observation.items()
                           if key in portfolio_fields and previous.get(key) is None and value is not None}}


def import_entries(db, batch):
    if not batch.dry_run:
        # Serializes imports across admins, including overlapping evidence,
        # from classification through commit. It releases on rollback as well.
        db.execute(text("SELECT pg_advisory_xact_lock(77581, 42017)"))
    groups = defaultdict(dict)
    for entry in batch.entries:
        key, source = contact_identity(entry), _source_identity(entry)
        observation = entry.model_dump(mode="json")
        prior = groups[key].get(source)
        if prior is None or observation["verified_on"] >= prior["verified_on"]:
            groups[key][source] = observation

    contacts = {row.identity_key: row for row in db.scalars(
        select(TradeContact).where(TradeContact.identity_key.in_(groups)))}
    sources = {(row.contact_id, row.source_key): row for row in db.scalars(
        select(TradeContactSource).where(TradeContactSource.contact_id.in_([row.id for row in contacts.values()])))}
    counts = TradeContactImportResult()
    for key, observations in groups.items():
        contact = contacts.get(key)
        contact_id = contact.id if contact else uuid4()
        details = contact.details if contact else None
        source_changes = []
        for source_key, observation in observations.items():
            evidence = sources.get((contact_id, source_key))
            evidence_before = evidence.evidence if evidence else None
            if evidence_before and observation["verified_on"] < evidence_before["verified_on"]:
                continue
            if observation != evidence_before:
                observation = ({**evidence_before, **{field: value for field, value in observation.items()
                                                     if value is not None}} if evidence_before else observation)
                if observation != evidence_before:
                    source_changes.append((source_key, evidence, observation))
            same_source = (details is not None and details["source_authority"] == observation["source_authority"]
                           and (details.get("external_id") or details["source_url"])
                           == (observation.get("external_id") or observation["source_url"]))
            details = _preserve_details(details, observation, same_source)
        if contact is None:
            counts.inserted += 1
        elif source_changes or details != contact.details:
            counts.updated += 1
        else:
            counts.unchanged += 1
        if batch.dry_run:
            continue
        if contact is None:
            db.add(TradeContact(id=contact_id, identity_key=key, details=details, **_fields(details)))
            db.flush()
        elif details != contact.details:
            contact.details = details
            for field, value in _fields(details).items():
                setattr(contact, field, value)
        for source_key, evidence, observation in source_changes:
            if evidence is None:
                db.add(TradeContactSource(id=uuid4(), contact_id=contact_id, source_key=source_key, evidence=observation))
            else:
                evidence.evidence = observation
    if not batch.dry_run:
        db.commit()
    return counts


def search(db, *, q, category, province, limit, offset):
    filters = []
    if q:
        filters.append(or_(*(column.icontains(q, autoescape=True) for column in (
            TradeContact.name, TradeContact.category, TradeContact.province, TradeContact.locality,
            TradeContact.details["subtype"].astext, TradeContact.details["phone"].astext,
            TradeContact.details["email"].astext, TradeContact.details["contact_name"].astext,
        ))))
    for column, value in ((TradeContact.category, category), (TradeContact.province, province)):
        if value:
            filters.append(func.lower(column) == value.lower())
    total = db.scalar(select(func.count()).select_from(TradeContact).where(*filters))
    source_count = (select(func.count()).select_from(TradeContactSource)
                    .where(TradeContactSource.contact_id == TradeContact.id).scalar_subquery())
    entries = db.execute(select(TradeContact.id, TradeContact.details, source_count).where(*filters)
                         .order_by(func.lower(TradeContact.name), TradeContact.id).limit(limit).offset(offset)).all()
    return {"total": total, "items": [dict(details, id=identifier, source_count=count)
                                      for identifier, details, count in entries]}
