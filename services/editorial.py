from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import and_, func, select, true
from sqlalchemy.dialects.postgresql import insert

from models.editorial import EditorialEvent as Event, EditorialPage as Page, EditorialRevision as Revision
from schemas.editorial import (
    EditorialDetail, EditorialDraft, EditorialList, EditorialSummary, PublicEditorialDetail,
    PublicEditorialList, PublicEditorialSummary, PublicNeighborhoodDetail, PublicNeighborhoodList,
    PublicNeighborhoodSummary, RESERVED_SLUGS, RevisionList, RevisionPreview, RevisionSummary,
)
from services.property_management import today_dr
from services.property_network import now
from services.service_workflow import lock_users


def unavailable():
    raise HTTPException(404, "Editorial content not found")


def conflict():
    raise HTTPException(409, "Editorial version or publication state changed. Refresh before trying again.")


def _editor(db, user):
    actor = lock_users(db, user, ())
    if actor.role != "admin":
        raise HTTPException(403, "Admin access required")
    return actor


def _page(db, identifier, *, lock=False):
    query = db.query(Page).filter(Page.id == identifier)
    row = (query.populate_existing().with_for_update() if lock else query).first()
    if row is None:
        unavailable()
    return row


def _revision(db, identifier, revision):
    row = db.get(Revision, (identifier, revision))
    if row is None:
        unavailable()
    return row


def _state(page):
    return {field: getattr(page, field) for field in (
        "id", "slug", "kind", "version", "draft_revision", "reviewed_revision", "published_revision",
    )}


def _payload(revision):
    return EditorialDraft(content=revision.content, sources=revision.sources, review_note=revision.review_note)


def _detail(page, revision):
    return EditorialDetail(**_state(page), **_payload(revision).model_dump())


def admin_list(db, page, page_size):
    rows = (db.query(Page, Revision).select_from(Page).join(
        Revision, and_(Revision.page_id == Page.id, Revision.revision == Page.draft_revision))
        .order_by(Page.updated_at.desc(), Page.id).offset((page - 1) * page_size).limit(page_size).all())
    items = []
    for document, revision in rows:
        content = _payload(revision).content
        title = content.en.title.strip() or content.es.title.strip() or document.slug
        items.append(EditorialSummary(**_state(document), title=title))
    return EditorialList(items=items)


def read_admin(db, identifier):
    page = _page(db, identifier)
    return _detail(page, _revision(db, identifier, page.draft_revision))


def revisions(db, identifier, page, page_size):
    _page(db, identifier)
    rows = db.execute(select(Revision.revision, Revision.saved_at).where(Revision.page_id == identifier)
                      .order_by(Revision.revision.desc()).offset((page - 1) * page_size).limit(page_size))
    return RevisionList(items=[RevisionSummary(revision=row.revision, saved_at=row.saved_at) for row in rows])


def preview(db, identifier, revision):
    row = _revision(db, identifier, revision)
    return RevisionPreview(revision=row.revision, **_payload(row).model_dump())


def _new_revision(db, page, actor, snapshot, instant, *, restored_from=None):
    row = Revision(page_id=page.id, revision=page.draft_revision,
                   content=snapshot.content.model_dump(mode="json"),
                   sources=[source.model_dump(mode="json") for source in snapshot.sources],
                   review_note=snapshot.review_note, saved_at=instant, saved_by=actor.id, restored_from=restored_from)
    db.add(row)
    db.flush()
    return row


def _commit(db, page, current, actor, name, instant, *, event_revision=None):
    db.add(Event(page_id=page.id, version=page.version, revision=event_revision or current.revision,
                 action=name, actor_id=actor.id, created_at=instant))
    result = _detail(page, current)
    db.commit()
    return result


def create(db, user, body):
    actor = _editor(db, user)
    if body.slug in RESERVED_SLUGS:
        raise HTTPException(409, "This slug is reserved for existing website content")
    _check_neighborhood_kind(body.kind, body)
    instant = now()
    created = db.execute(insert(Page).values(id=uuid4(), slug=body.slug, kind=body.kind, version=1,
                                            draft_revision=1, updated_at=instant)
                         .on_conflict_do_nothing(index_elements=[Page.slug]).returning(Page.id)).scalar_one_or_none()
    if created is None:
        raise HTTPException(409, "An editorial page already uses this slug")
    page = _page(db, created)
    current = _new_revision(db, page, actor, body, instant)
    return _commit(db, page, current, actor, "create", instant)


def _checked_page(db, user, identifier, expected_version):
    actor = _editor(db, user)
    page = _page(db, identifier, lock=True)
    if page.version != expected_version:
        conflict()
    return actor, page


def _next_draft(db, page, actor, snapshot, name, *, restored_from=None):
    instant = now()
    page.version += 1
    page.draft_revision += 1
    page.reviewed_revision = None
    page.updated_at = instant
    current = _new_revision(db, page, actor, snapshot, instant, restored_from=restored_from)
    return _commit(db, page, current, actor, name, instant)


def write_draft(db, user, identifier, body):
    actor, page = _checked_page(db, user, identifier, body.expected_version)
    _check_neighborhood_kind(page.kind, body)
    snapshot = body
    if page.kind == "neighborhood" and "neighborhood" not in body.content.model_fields_set:
        previous = _payload(_revision(db, page.id, page.draft_revision)).content.neighborhood
        if previous is not None:
            input_data = body.model_dump(exclude={"expected_version"})
            input_data["content"]["neighborhood"] = previous.model_dump()
            try:
                snapshot = EditorialDraft.model_validate(input_data)
            except ValidationError:
                raise HTTPException(422, "Keep the listed sources for existing neighborhood facts.") from None
    return _next_draft(db, page, actor, snapshot, "draft")


def _check_neighborhood_kind(kind, draft):
    if kind != "neighborhood" and draft.content.neighborhood is not None:
        raise HTTPException(422, "Comparison facts are available for neighborhood pages.")


def restore(db, user, identifier, body):
    actor, page = _checked_page(db, user, identifier, body.expected_version)
    restored = _revision(db, page.id, body.revision)
    return _next_draft(db, page, actor, _payload(restored), "restore", restored_from=restored.revision)


def _reviewable(revision):
    snapshot = _payload(revision)
    complete = all(locale.title.strip() and locale.summary.strip() and locale.body.strip()
                   for locale in (snapshot.content.en, snapshot.content.es))
    if not complete or not snapshot.sources:
        raise HTTPException(422, "Complete English and Spanish content and add an HTTPS source before review")
    if snapshot.content.neighborhood is not None and any(
        fact.checked_on > today_dr() for fact in snapshot.content.neighborhood.facts
    ):
        raise HTTPException(422, "Neighborhood fact dates cannot be in the future.")


def review(db, user, identifier, body):
    actor, page = _checked_page(db, user, identifier, body.expected_version)
    if body.revision != page.draft_revision or page.reviewed_revision == body.revision:
        conflict()
    current = _revision(db, page.id, page.draft_revision)
    _reviewable(current)
    instant = now()
    page.version += 1
    page.reviewed_revision = current.revision
    page.updated_at = instant
    return _commit(db, page, current, actor, "review", instant)


def publish(db, user, identifier, body):
    actor, page = _checked_page(db, user, identifier, body.expected_version)
    if body.revision != page.draft_revision or body.revision != page.reviewed_revision or (
            page.published_revision == body.revision):
        conflict()
    current = _revision(db, page.id, page.draft_revision)
    _reviewable(current)
    instant = now()
    page.version += 1
    page.published_revision = current.revision
    page.published_at = page.updated_at = instant
    return _commit(db, page, current, actor, "publish", instant)


def unpublish(db, user, identifier, body):
    actor, page = _checked_page(db, user, identifier, body.expected_version)
    published = page.published_revision
    if published is None:
        conflict()
    current = _revision(db, page.id, page.draft_revision)
    instant = now()
    page.version += 1
    page.published_revision = page.published_at = None
    page.updated_at = instant
    return _commit(db, page, current, actor, "unpublish", instant, event_revision=published)


def _published(statement):
    return (statement.select_from(Page).join(
        Revision, and_(Revision.page_id == Page.id, Revision.revision == Page.published_revision))
        .where(Page.published_revision.is_not(None), Page.slug.not_in(RESERVED_SLUGS)))


def public_list(db, locale, page, page_size):
    localized = Revision.content[locale]
    cards = (_published(select(Page.slug, Page.kind, Page.published_at, localized["title"].as_string().label("title"),
                               localized["summary"].as_string().label("summary")))
             .order_by(Page.published_at.desc(), Page.slug).offset((page - 1) * page_size).limit(page_size).subquery())
    totals = _published(select(func.count().label("total"))).subquery()
    rows = db.execute(select(totals.c.total, cards).select_from(totals).outerjoin(cards, true())
                      .order_by(cards.c.published_at.desc(), cards.c.slug)).all()
    return PublicEditorialList(total=rows[0].total, items=[
        PublicEditorialSummary(slug=row.slug, kind=row.kind, title=row.title, summary=row.summary, published_at=row.published_at)
        for row in rows if row.slug is not None
    ])


def public_detail(db, slug, locale):
    row = db.execute(_published(select(Page.slug, Page.kind, Page.published_at, Revision.content[locale].label("content"),
                                       Revision.sources)).where(Page.slug == slug)).one_or_none()
    if row is None:
        unavailable()
    content = row.content
    return PublicEditorialDetail(slug=row.slug, kind=row.kind, published_at=row.published_at,
                                 title=content["title"], summary=content["summary"], body=content["body"], sources=row.sources)


def _published_neighborhoods(statement):
    return _published(statement).where(
        Page.kind == "neighborhood", func.jsonb_typeof(Revision.content["neighborhood"]) == "object",
    )


def public_neighborhood_list(db, locale, page, page_size):
    localized = Revision.content[locale]
    location = Revision.content["neighborhood"]["listing_location"].as_string().label("listing_location")
    cards = _published_neighborhoods(select(
        Page.slug, Page.published_at, localized["title"].as_string().label("title"),
        localized["summary"].as_string().label("summary"), location,
    )).order_by(Page.slug).offset((page - 1) * page_size).limit(page_size).subquery()
    totals = _published_neighborhoods(select(func.count().label("total"))).subquery()
    rows = db.execute(select(totals.c.total, cards).select_from(totals).outerjoin(cards, true()).order_by(cards.c.slug)).all()
    return PublicNeighborhoodList(total=rows[0].total, items=[
        PublicNeighborhoodSummary(slug=row.slug, title=row.title, summary=row.summary,
                                  published_at=row.published_at, listing_location=row.listing_location)
        for row in rows if row.slug is not None
    ])


def public_neighborhood_detail(db, slug, locale):
    row = db.execute(_published_neighborhoods(select(
        Page.slug, Page.published_at, Revision.content, Revision.sources,
    )).where(Page.slug == slug)).one_or_none()
    if row is None:
        unavailable()
    summary = row.content[locale]
    neighborhood = row.content["neighborhood"]
    sources = {}
    for source in row.sources:
        sources.setdefault(source["url"], source)
    return PublicNeighborhoodDetail(
        slug=row.slug, title=summary["title"], summary=summary["summary"], published_at=row.published_at,
        listing_location=neighborhood["listing_location"],
        facts=[dict(topic=fact["topic"], text=fact[locale], checked_on=fact["checked_on"],
                    source=sources[fact["source_url"]]) for fact in neighborhood["facts"]],
    )
