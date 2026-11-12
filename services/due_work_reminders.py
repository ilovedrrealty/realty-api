"""Current project work reminders, with private opt-in and no delivery worker."""
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import Date, Integer, TIMESTAMP, and_, cast, func, literal, null, select, union_all

from models.due_work_reminders import (NetworkDueWorkAction as Action, NetworkDueWorkDisposition as Disposition,
    NetworkDueWorkPreference as Preference)
from models.property_network import NetworkMilestone as Milestone, NetworkProject as Project, NetworkProjectMember as Member
from models.service_visit import NetworkServiceWork as Work
from models.service_workflow import NetworkServiceRecipient as Winner, NetworkServiceRequest as ServiceRequest
from models.user import User
from services import property_network as network, service_workflow as workflow
from services.maintenance_reminders import TIME_ZONE
from services.service_visit import DR_TIMEZONE


LIVE_PROJECT = {"open", "in_progress"}
OPERATIONS = {"active": {"snooze", "dismiss", "stop"}, "snoozed": {"snooze", "dismiss", "restore", "stop"},
              "dismissed": {"restore", "stop"}}
NEXT_STATE = {"snooze": "snoozed", "dismiss": "dismissed", "restore": "active", "stop": "stopped"}


def _membership(db, project_id, actor_id):
    return db.query(Member).filter_by(project_id=project_id, user_id=actor_id, active=True, role="professional").populate_existing().first()


def _scope(db, user, project_id):
    workflow.checked_user(user)
    if user.role == "admin":
        workflow.unavailable()
    hint = db.get(Project, project_id)
    if (hint is None or hint.status not in LIVE_PROJECT
            or hint.owner_id != user.id and _membership(db, project_id, user.id) is None):
        workflow.unavailable()
    owner_id, property_id, listing_id = hint.owner_id, hint.property_id, hint.listing_id
    # Domain writes use User -> source -> Project. Never lock a request after Project.
    actor = workflow.lock_users(db, user, {owner_id})
    source, custodian = workflow.source_row(db, property_id, listing_id, lock=True)
    project = db.query(Project).filter_by(id=project_id).populate_existing().with_for_update().first()
    if (project is None or project.status not in LIVE_PROJECT or actor.role == "admin"
            or (project.owner_id, project.property_id, project.listing_id) != (owner_id, property_id, listing_id)
            or custodian is None or custodian.id != owner_id or custodian.role == "admin"
            or custodian.status != "active" or custodian.email_verified is not True or source.status != "active"
            or not workflow.source_authorized(db, custodian, source)):
        workflow.unavailable()
    grant_version = 0
    if actor.id != owner_id:
        member = _membership(db, project_id, actor.id)
        if member is None:
            workflow.unavailable()
        grant_version = member.reminder_grant_version
    return actor, project, source, grant_version, network.now().astimezone(DR_TIMEZONE).date()


def _preference(db, actor, project, grant_version):
    return db.query(Preference).filter_by(actor_id=actor.id, project_id=project.id, grant_version=grant_version).first()


def _preference_view(row):
    if row is None:
        return {"milestones_enabled": False, "visits_enabled": False, "lead_days": 7, "version": 0}
    return {"milestones_enabled": row.milestones_enabled, "visits_enabled": row.visits_enabled,
            "lead_days": row.lead_days, "version": row.version}


def _milestones(project, through):
    return select(literal("milestone").label("kind"), Milestone.id.label("item_id"),
        Milestone.reminder_occurrence_version.label("occurrence_version"), Milestone.title.label("title"),
        Milestone.due_date.label("due_on"), cast(null(), TIMESTAMP(timezone=True)).label("starts_at"),
        cast(null(), Integer).label("duration_minutes")).where(
        Milestone.project_id == project.id, Milestone.status.in_(("open", "in_progress")), Milestone.due_date <= through,
    )


def _visits(actor, project, source, through):
    due_on = cast(func.timezone(TIME_ZONE, Work.confirmed_starts_at), Date)
    occurrence = func.coalesce(Work.confirmed_reminder_version, 1)
    query = select(literal("visit").label("kind"), Work.request_id.label("item_id"), occurrence.label("occurrence_version"),
        ServiceRequest.title.label("title"), due_on.label("due_on"), Work.confirmed_starts_at.label("starts_at"),
        Work.confirmed_duration_minutes.label("duration_minutes")).select_from(Work).join(
        ServiceRequest, ServiceRequest.id == Work.request_id).join(
        Winner, and_(Winner.request_id == Work.request_id, Winner.status == "awarded")).join(
        User, User.id == Winner.provider_id).join(
        Member, and_(Member.project_id == project.id, Member.user_id == Winner.provider_id,
                     Member.active.is_(True), Member.role == "professional")).where(
        ServiceRequest.project_id == project.id, ServiceRequest.status == "awarded", ServiceRequest.custodian_id == project.owner_id,
        ServiceRequest.property_id.is_not_distinct_from(project.property_id),
        ServiceRequest.listing_id.is_not_distinct_from(project.listing_id),
        Work.status.in_(("confirmed", "proposed")), Work.confirmed_starts_at.is_not(None), due_on <= through,
        User.status == "active", User.email_verified.is_(True), User.role != "admin",
    )
    if actor.id != project.owner_id:
        query = query.where(Winner.provider_id == actor.id)
    if project.property_id is not None:
        return query.where(ServiceRequest.property_class == source.property_class)
    return query.where(ServiceRequest.source_listing_type == source.type)


def _items(db, actor, project, source, preference, today):
    through = today + timedelta(days=preference.lead_days)
    queries = []
    if preference.milestones_enabled:
        queries.append(_milestones(project, through))
    if preference.visits_enabled:
        queries.append(_visits(actor, project, source, through))
    live = (queries[0] if len(queries) == 1 else union_all(*queries)).subquery()
    return db.query(live.c.kind, live.c.item_id, live.c.occurrence_version, live.c.title, live.c.due_on, live.c.starts_at,
                    live.c.duration_minutes, Disposition).select_from(live).outerjoin(Disposition, and_(
        Disposition.preference_id == preference.id, Disposition.kind == live.c.kind,
        Disposition.item_id == live.c.item_id, Disposition.occurrence_version == live.c.occurrence_version,
    )), live


def _item(db, actor, project, source, preference, today, kind, item_id, occurrence_version):
    if preference is None or not (preference.milestones_enabled if kind == "milestone" else preference.visits_enabled):
        workflow.unavailable()
    query, live = _items(db, actor, project, source, preference, today)
    row = query.filter(live.c.kind == kind, live.c.item_id == item_id, live.c.occurrence_version == occurrence_version).first()
    if row is None:
        workflow.unavailable()
    return row


def _view(row, today, disposition=None):
    kind, identity, occurrence_version, title, due_on, starts_at, duration_minutes, stored = row
    state_row = disposition if disposition is not None else stored
    state = state_row.state if state_row is not None else "active"
    snooze = state_row.snoozed_until if state_row is not None else None
    if state == "snoozed" and snooze <= today:
        state, snooze = "active", None
    attention = "none"
    if state == "active":
        attention = "overdue" if due_on < today else "due" if due_on == today else "upcoming"
    return {"kind": kind, "id": str(identity), "occurrence_version": occurrence_version, "title": title,
            "due_on": due_on, "starts_at": starts_at, "duration_minutes": duration_minutes, "state": state,
            "snoozed_until": snooze, "version": state_row.version if state_row is not None else 0, "attention": attention}


def list_due_work(db, user, project_id, page):
    actor, project, source, grant_version, today = _scope(db, user, project_id)
    preference = _preference(db, actor, project, grant_version)
    result = {"items": [], "total": 0, "page": page, "page_size": network.PAGE_SIZE}
    if preference is not None and (preference.milestones_enabled or preference.visits_enabled):
        query, live = _items(db, actor, project, source, preference, today)
        result = network.page(query.order_by(live.c.due_on, live.c.starts_at.nullsfirst(), live.c.kind, live.c.item_id),
                              page, lambda row: _view(row, today))
    return {"project_id": str(project_id), "as_of": today, "time_zone": TIME_ZONE,
            "preference": _preference_view(preference), **result}


def _prior(db, actor, preference, body, operation, digest, kind=None, item_id=None, occurrence_version=None):
    row = db.get(Action, (actor.id, body.submission_id))
    if row is not None and (preference is None or row.preference_id != preference.id
            or (row.operation, row.creation_sha256, row.kind, row.item_id, row.occurrence_version)
            != (operation, digest, kind, item_id, occurrence_version)):
        workflow.conflict()
    return row


def _record(db, actor, preference, body, operation, digest, kind=None, item_id=None, occurrence_version=None):
    db.add(Action(actor_id=actor.id, submission_id=body.submission_id, preference_id=preference.id, operation=operation,
                  creation_sha256=digest, kind=kind, item_id=item_id, occurrence_version=occurrence_version))


def save_preferences(db, user, project_id, body):
    actor, project, _, grant_version, _ = _scope(db, user, project_id)
    preference = _preference(db, actor, project, grant_version)
    digest = workflow.fingerprint("preferences", project_id, body)
    if _prior(db, actor, preference, body, "preferences", digest):
        return _preference_view(preference)
    workflow.expected(preference.version if preference is not None else 0, body.expected_version)
    if preference is None:
        preference = Preference(id=uuid4(), actor_id=actor.id, project_id=project.id, grant_version=grant_version, version=0)
        db.add(preference)
    preference.milestones_enabled, preference.visits_enabled = body.milestones_enabled, body.visits_enabled
    preference.lead_days = body.lead_days
    preference.version += 1
    db.flush()
    _record(db, actor, preference, body, "preferences", digest)
    result = _preference_view(preference)
    db.commit()
    return result


def apply_action(db, user, project_id, kind, item_id, body):
    actor, project, source, grant_version, today = _scope(db, user, project_id)
    preference = _preference(db, actor, project, grant_version)
    item = _item(db, actor, project, source, preference, today, kind, item_id, body.occurrence_version)
    operation = body.operation
    digest = workflow.fingerprint(operation, item_id, body)
    current = _view(item, today)
    if _prior(db, actor, preference, body, operation, digest, kind, item_id, body.occurrence_version):
        return current
    workflow.expected(current["version"], body.expected_version)
    if operation not in OPERATIONS.get(current["state"], ()):
        workflow.conflict()
    if operation == "snooze" and body.snoozed_until <= today:
        raise HTTPException(422, "Choose a snooze date after today")

    disposition = item[-1]
    if disposition is None:
        disposition = Disposition(preference_id=preference.id, kind=kind, item_id=item_id,
                                  occurrence_version=body.occurrence_version, version=0)
        db.add(disposition)
    disposition.state, disposition.snoozed_until = NEXT_STATE[operation], body.snoozed_until
    disposition.version += 1
    _record(db, actor, preference, body, operation, digest, kind, item_id, body.occurrence_version)
    result = _view(item, today, disposition)
    db.commit()
    return result


def own_submission(db, user, project_id, submission_id):
    actor, project, source, grant_version, today = _scope(db, user, project_id)
    preference = _preference(db, actor, project, grant_version)
    action = db.get(Action, (actor.id, submission_id))
    if preference is None or action is None or action.preference_id != preference.id:
        workflow.unavailable()
    result = (_preference_view(preference) if action.kind is None else _view(_item(
        db, actor, project, source, preference, today, action.kind, action.item_id, action.occurrence_version), today))
    return {"operation": action.operation, "result": result}
