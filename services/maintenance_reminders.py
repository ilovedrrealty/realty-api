"""Personal in-app maintenance reminders for an exact current property custodian."""
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, func, select

from models.maintenance_reminders import (NetworkMaintenanceAction as Action,
    NetworkMaintenanceOccurrence as Occurrence, NetworkMaintenancePreference as Preference,
    NetworkMaintenanceSchedule as Schedule)
from services import property_network as network, service_workflow as workflow
from services.property_management import add_months
from services.service_visit import DR_TIMEZONE


TIME_ZONE = "America/Santo_Domingo"
MONTH_INTERVAL = {"monthly": 1, "quarterly": 3, "yearly": 12}
ACTIONS = {"active": {"complete", "snooze", "pause", "stop"}, "paused": {"resume", "stop"}}


def _require_custody(db, actor, record, custodian):
    if (actor.role == "admin" or custodian is None or custodian.id != actor.id
            or record.status != "active" or not workflow.source_authorized(db, actor, record)):
        workflow.unavailable()


def _scope(db, user, source_kind, source_id):
    workflow.checked_user(user)
    if user.role == "admin":
        workflow.unavailable()
    fields = {"property_id": source_id, "listing_id": None} if source_kind == "private" else {
        "property_id": None, "listing_id": source_id}
    _require_custody(db, user, *workflow.source_row(db, **fields))
    # Serialize the actor's submissions and current source changes in workflow order.
    actor = workflow.lock_users(db, user, ())
    _require_custody(db, actor, *workflow.source_row(db, **fields, lock=True))
    return actor, {"actor_id": actor.id, **fields}, network.now().astimezone(DR_TIMEZONE).date()


def _preference(db, fields):
    return db.query(Preference).filter_by(**fields).first()


def _new_preference(db, fields):
    row = Preference(id=uuid4(), enabled=False, lead_days=7, version=0, **fields)
    db.add(row)
    db.flush()
    return row


def _preference_view(row):
    return {"enabled": row.enabled, "lead_days": row.lead_days, "version": row.version} if row is not None else {
        "enabled": False, "lead_days": 7, "version": 0}


def _attention(schedule, occurrence, preference, today):
    if not preference.enabled or schedule.status != "active":
        return "none"
    if occurrence.snoozed_until is not None and occurrence.snoozed_until > today:
        return "snoozed"
    days = (occurrence.due_on - today).days
    if days < 0:
        return "overdue"
    if days == 0:
        return "due"
    return "upcoming" if days <= preference.lead_days else "scheduled"


def _schedule_view(schedule, occurrence, preference, today):
    return {"id": str(schedule.id), "title": schedule.title, "repeat": schedule.repeat, "anchor_on": schedule.anchor_on,
            "due_on": occurrence.due_on, "snoozed_until": occurrence.snoozed_until, "status": schedule.status,
            "version": schedule.version, "attention": _attention(schedule, occurrence, preference, today)}


def _schedules(db, preference):
    latest_due = select(func.max(Occurrence.due_on)).where(Occurrence.schedule_id == Schedule.id).correlate(Schedule).scalar_subquery()
    return db.query(Schedule, Occurrence).select_from(Schedule).join(Occurrence, and_(
        Occurrence.schedule_id == Schedule.id, Occurrence.due_on == latest_due,
    )).filter(Schedule.preference_id == preference.id)


def _schedule(db, preference, schedule_id):
    if preference is None:
        workflow.unavailable()
    result = _schedules(db, preference).filter(Schedule.id == schedule_id).first()
    if result is None:
        workflow.unavailable()
    return result


def list_reminders(db, user, source_kind, source_id, page):
    _, fields, today = _scope(db, user, source_kind, source_id)
    preference = _preference(db, fields)
    result = {"items": [], "total": 0, "page": page, "page_size": network.PAGE_SIZE}
    if preference is not None:
        rows = _schedules(db, preference).order_by(Schedule.status.in_(("completed", "stopped")),
                                                 Occurrence.due_on, Schedule.id)
        result = network.page(rows, page, lambda pair: _schedule_view(*pair, preference, today))
    return {"source": {"kind": source_kind, "id": str(source_id)}, "as_of": today, "time_zone": TIME_ZONE,
            "preference": _preference_view(preference), **result}


def _prior(db, actor, preference, body, operation, digest):
    row = db.get(Action, (actor.id, body.submission_id))
    if row is not None and (preference is None or row.preference_id != preference.id
                            or row.operation != operation or row.creation_sha256 != digest):
        workflow.conflict()
    return row


def _record(db, actor, preference, body, operation, digest, schedule_id=None):
    db.add(Action(actor_id=actor.id, submission_id=body.submission_id, preference_id=preference.id,
                  schedule_id=schedule_id, operation=operation, creation_sha256=digest))


def save_preferences(db, user, source_kind, source_id, body):
    actor, fields, _ = _scope(db, user, source_kind, source_id)
    preference = _preference(db, fields)
    digest = workflow.fingerprint("preferences", source_id, body)
    if _prior(db, actor, preference, body, "preferences", digest):
        return _preference_view(preference)
    workflow.expected(preference.version if preference is not None else 0, body.expected_version)
    preference = preference if preference is not None else _new_preference(db, fields)
    preference.enabled, preference.lead_days = body.enabled, body.lead_days
    preference.version += 1
    _record(db, actor, preference, body, "preferences", digest)
    result = _preference_view(preference)
    db.commit()
    return result


def create(db, user, source_kind, source_id, body):
    actor, fields, today = _scope(db, user, source_kind, source_id)
    preference = _preference(db, fields)
    digest = workflow.fingerprint("create", source_id, body)
    prior = _prior(db, actor, preference, body, "create", digest)
    if prior is not None:
        return _schedule_view(*_schedule(db, preference, prior.schedule_id), preference, today)
    preference = preference if preference is not None else _new_preference(db, fields)
    schedule = Schedule(id=uuid4(), preference_id=preference.id, title=body.title, repeat=body.repeat,
                        anchor_on=body.first_due_on, status="active", version=1)
    db.add(schedule)
    db.flush()
    occurrence = Occurrence(schedule_id=schedule.id, due_on=body.first_due_on, status="open")
    db.add(occurrence)
    _record(db, actor, preference, body, "create", digest, schedule.id)
    result = _schedule_view(schedule, occurrence, preference, today)
    db.commit()
    return result


def _next_due(schedule, current_due, today):
    anchor = schedule.anchor_on
    threshold = max(current_due, today)
    try:
        if schedule.repeat == "weekly":
            return anchor + timedelta(weeks=(threshold - anchor).days // 7 + 1)
        step = MONTH_INTERVAL[schedule.repeat]
        months = (threshold.year - anchor.year) * 12 + threshold.month - anchor.month
        elapsed = (months // step) * step
        candidate = add_months(anchor, elapsed, anchor.day)
        return candidate if candidate > threshold else add_months(anchor, elapsed + step, anchor.day)
    except (OverflowError, ValueError):
        raise HTTPException(422, "The next occurrence exceeds the supported date range") from None


def _close_occurrence(occurrence, status, today):
    occurrence.status, occurrence.closed_on, occurrence.snoozed_until = status, today, None


def apply_action(db, user, source_kind, source_id, schedule_id, body):
    actor, fields, today = _scope(db, user, source_kind, source_id)
    preference = _preference(db, fields)
    schedule, occurrence = _schedule(db, preference, schedule_id)
    operation = body.operation
    digest = workflow.fingerprint(operation, schedule_id, body)
    if _prior(db, actor, preference, body, operation, digest):
        return _schedule_view(schedule, occurrence, preference, today)
    workflow.expected(schedule.version, body.expected_version)
    if operation not in ACTIONS.get(schedule.status, ()) or occurrence.status != "open":
        workflow.conflict()

    if operation == "complete":
        _close_occurrence(occurrence, "completed", today)
        if schedule.repeat == "none":
            schedule.status = "completed"
        else:
            due_on = _next_due(schedule, occurrence.due_on, today)
            db.flush()
            occurrence = Occurrence(schedule_id=schedule.id, due_on=due_on, status="open")
            db.add(occurrence)
    elif operation == "snooze":
        if body.snoozed_until <= today:
            raise HTTPException(422, "Choose a snooze date after today")
        occurrence.snoozed_until = body.snoozed_until
    elif operation == "stop":
        _close_occurrence(occurrence, "stopped", today)
        schedule.status = "stopped"
    else:
        schedule.status = "paused" if operation == "pause" else "active"
    schedule.version += 1
    _record(db, actor, preference, body, operation, digest, schedule.id)
    result = _schedule_view(schedule, occurrence, preference, today)
    db.commit()
    return result


def own_submission(db, user, source_kind, source_id, submission_id):
    actor, fields, today = _scope(db, user, source_kind, source_id)
    preference = _preference(db, fields)
    action = db.get(Action, (actor.id, submission_id))
    if preference is None or action is None or action.preference_id != preference.id:
        workflow.unavailable()
    result = (_preference_view(preference) if action.schedule_id is None else
              _schedule_view(*_schedule(db, preference, action.schedule_id), preference, today))
    return {"operation": action.operation, "result": result}
