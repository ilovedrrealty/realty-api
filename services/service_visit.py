"""Coordinate visits and completion for an existing private service award."""

from datetime import timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException

from models.property_network import NetworkProjectMember, NetworkProjectUpdate
from models.service_visit import NetworkServiceWork as Work, NetworkServiceWorkAction as WorkAction
from models.service_workflow import NetworkServiceRecipient as Recipient, NetworkServiceRequest as ServiceRequest
from models.user import User
from services import property_network as network, service_workflow as workflow


DR_TIMEZONE = timezone(timedelta(hours=-4))
MAX_VISIT_DURATION = timedelta(hours=8)


def _access(db, user, request, source, recipients, *, lock_project=False):
    if request.status != "awarded" or request.project_id is None:
        workflow.unavailable()
    winner = next((recipient for recipient in recipients if recipient.status == "awarded"), None)
    if winner is None:
        workflow.unavailable()

    if user.role == "admin":
        actor = "admin"
    elif user.id == request.custodian_id:
        actor = "owner"
    elif user.id == winner.provider_id:
        actor = "provider"
    else:
        workflow.unavailable()

    source = source or workflow.source_row(db, request.property_id, request.listing_id)
    _, custodian = source
    if custodian is None or custodian.id != request.custodian_id:
        workflow.unavailable()
    if actor != "provider" and not workflow.managed(db, user, request, source):
        workflow.unavailable()
    try:
        project, project_role = network.project_for(db, user, request.project_id, lock=lock_project)
    except HTTPException as error:
        if error.status_code in {403, 404}:
            workflow.unavailable()
        raise
    db.refresh(project)
    if (project.owner_id != request.custodian_id or project.property_id != request.property_id
            or project.listing_id != request.listing_id):
        workflow.unavailable()
    if actor == "owner" and project_role != "owner":
        workflow.unavailable()

    awarded_user = db.query(User).filter_by(id=winner.provider_id).populate_existing().first()
    membership = db.query(NetworkProjectMember).filter_by(project_id=project.id, user_id=winner.provider_id,
                                                          active=True, role="professional").first()
    award_active = (awarded_user is not None and awarded_user.status == "active"
                    and awarded_user.email_verified is True and membership is not None)
    if actor == "provider" and (project_role != "professional" or not award_active):
        workflow.unavailable()
    can_change = award_active and workflow.source_current(db, request, source)
    return actor, project, winner.provider_id, can_change


def _read_request(db, user, request_id):
    workflow.checked_user(user)
    request = db.query(ServiceRequest).filter_by(id=request_id).populate_existing().first()
    if request is None:
        workflow.unavailable()
    recipients = db.query(Recipient).filter_by(request_id=request_id).populate_existing().all()
    return request, recipients


def _read_access(db, user, request_id):
    request, recipients = _read_request(db, user, request_id)
    actor, project, _, can_change = _access(db, user, request, None, recipients)
    return request, actor, project, can_change


def require_live_participant(db, user, request_id, *, for_write=False):
    """Recheck both sides on every request and take the existing locks for writes."""
    if user.role == "admin":
        workflow.unavailable()
    if for_write:
        user, request, source, recipient_map = workflow.locked_request(db, user, request_id)
        recipients = recipient_map.values()
    else:
        request, recipients = _read_request(db, user, request_id)
        source = None
    role, project, provider_id, can_change = _access(db, user, request, source, recipients, lock_project=for_write)
    if (not can_change or project.status == "archived" or db.get(User, request.custodian_id).role == "admin"
            or db.get(User, provider_id).role == "admin"):
        workflow.unavailable()
    return user, role


def _find_work(db, request_id):
    return db.query(Work).filter_by(request_id=request_id).populate_existing().first()


def _actions(work, actor, project, can_change, instant):
    if actor == "admin" or not can_change or project.status == "archived":
        return []
    status = work.status if work is not None else "unscheduled"
    if status in {"unscheduled", "cancelled"}:
        return ["propose_visit"]
    if status == "proposed":
        actions = ["cancel_visit"]
        if work.proposed_by != actor:
            actions.append("decline_visit")
            if work.proposed_starts_at > instant:
                actions.append("accept_visit")
        return actions
    if status == "confirmed":
        actions = ["propose_visit", "cancel_visit"]
        if actor == "provider" and work.confirmed_starts_at <= instant:
            actions.append("request_completion")
        return actions
    if status == "completion_requested" and actor == "owner":
        return ["request_changes", "approve_completion"]
    return []


def _view(request, work, actor, project, can_change, instant):
    confirmed = proposed = None
    if work is not None and work.confirmed_starts_at is not None:
        confirmed = {"starts_at": work.confirmed_starts_at, "duration_minutes": work.confirmed_duration_minutes}
    if work is not None and work.proposed_starts_at is not None:
        proposed = {"starts_at": work.proposed_starts_at, "duration_minutes": work.proposed_duration_minutes,
                    "proposed_by": work.proposed_by}
    return {
        "request_id": str(request.id),
        "project_id": str(project.id),
        "version": work.version if work is not None else 0,
        "status": work.status if work is not None else "unscheduled",
        "confirmed_visit": confirmed,
        "proposed_visit": proposed,
        "completion_note": work.completion_note if work is not None else None,
        "completed_on": work.completed_on if work is not None else None,
        "completion_review_note": work.completion_review_note if work is not None else None,
        "completion_update_id": str(work.completion_update_id) if work is not None and work.completion_update_id else None,
        "actions": _actions(work, actor, project, can_change, instant),
    }


def _prior_action(db, user, submission_id):
    return db.query(WorkAction).filter_by(actor_id=user.id, submission_id=submission_id).first()


def get_work(db, user, request_id):
    request, actor, project, can_change = _read_access(db, user, request_id)
    return _view(request, _find_work(db, request_id), actor, project, can_change, network.now())


def get_submission(db, user, request_id, submission_id):
    request, actor, project, can_change = _read_access(db, user, request_id)
    action = _prior_action(db, user, submission_id)
    if action is None or action.request_id != request_id:
        workflow.unavailable()
    return _view(request, _find_work(db, request_id), actor, project, can_change, network.now())


def _require_available_window(db, work, provider_id):
    start = work.proposed_starts_at
    end = start + timedelta(minutes=work.proposed_duration_minutes)
    candidates = db.query(Work.confirmed_starts_at, Work.confirmed_duration_minutes).join(
        Recipient, Recipient.request_id == Work.request_id,
    ).filter(
        Recipient.provider_id == provider_id, Recipient.status == "awarded", Work.request_id != work.request_id,
        Work.confirmed_starts_at > start - MAX_VISIT_DURATION, Work.confirmed_starts_at < end,
    )
    # locked_request holds the same provider lock across awards, including when
    # a property owner accepts a proposal on behalf of the owner side.
    if any(other_start + timedelta(minutes=duration) > start for other_start, duration in candidates):
        workflow.conflict()


def _clear_proposal(work):
    work.proposed_starts_at = work.proposed_duration_minutes = work.proposed_by = None


def _clear_completion(work):
    work.completion_note = work.completed_on = work.completion_review_note = None


def _approve_completion(db, work, user, project, body):
    if body.share_in_history is not True:
        workflow.conflict()
    # The owner is authorizing this private history entry. Its internal key is
    # separate from user-entered progress keys and from the action receipt.
    update = NetworkProjectUpdate(id=uuid4(), project_id=project.id, author_id=user.id, submission_id=uuid4(),
                                  kind="progress", body=work.completion_note, occurred_on=work.completed_on, media_ids=[])
    db.add(update)
    db.flush()
    work.completion_update_id = update.id
    work.status = "completed"


def _proposal_start(body, instant):
    try:
        start = body.starts_at.astimezone(timezone.utc)
        # Confirming the appointment requires a representable UTC end time.
        end = start + timedelta(minutes=body.duration_minutes)
    except OverflowError:
        workflow.conflict()
    if start <= instant or end <= start:
        workflow.conflict()
    return start


def _apply_operation(db, work, user, actor, project, provider_id, body, instant):
    if body.operation == "propose_visit":
        work.proposed_starts_at = _proposal_start(body, instant)
        work.proposed_duration_minutes = body.duration_minutes
        work.proposed_by = actor
        work.status = "proposed"
    elif body.operation == "accept_visit":
        _require_available_window(db, work, provider_id)
        work.confirmed_starts_at = work.proposed_starts_at
        work.confirmed_duration_minutes = work.proposed_duration_minutes
        work.confirmed_reminder_version = work.version + 1
        _clear_proposal(work)
        _clear_completion(work)
        work.status = "confirmed"
    elif body.operation == "decline_visit":
        _clear_proposal(work)
        work.status = "confirmed" if work.confirmed_starts_at is not None else "unscheduled"
    elif body.operation == "cancel_visit":
        work.confirmed_starts_at = work.confirmed_duration_minutes = None
        work.confirmed_reminder_version = None
        _clear_proposal(work)
        _clear_completion(work)
        work.status = "cancelled"
    elif body.operation == "request_completion":
        first_date = work.confirmed_starts_at.astimezone(DR_TIMEZONE).date()
        if not first_date <= body.completed_on <= instant.astimezone(DR_TIMEZONE).date():
            workflow.conflict()
        work.completion_note = body.note
        work.completed_on = body.completed_on
        work.status = "completion_requested"
    elif body.operation == "request_changes":
        work.completion_review_note = body.note
        work.status = "confirmed"
    elif body.operation == "approve_completion":
        _approve_completion(db, work, user, project, body)


def mutate_work(db, user, request_id, body):
    user, request, source, recipients = workflow.locked_request(db, user, request_id)
    actor, project, provider_id, can_change = _access(db, user, request, source, recipients.values(), lock_project=True)
    if actor == "admin":
        raise HTTPException(403, "Service work is read-only for administrators")
    work = _find_work(db, request_id)
    digest = workflow.fingerprint(body.operation, request_id, body)
    prior = _prior_action(db, user, body.submission_id)
    instant = network.now()
    if prior is not None:
        if prior.request_id != request_id or prior.operation != body.operation or prior.creation_sha256 != digest:
            workflow.conflict()
        return _view(request, work, actor, project, can_change, instant)

    workflow.expected(work.version if work is not None else 0, body.expected_version)
    if body.operation not in _actions(work, actor, project, can_change, instant):
        workflow.conflict()
    if work is None:
        work = Work(request_id=request_id, status="unscheduled", version=0)
        db.add(work)
    _apply_operation(db, work, user, actor, project, provider_id, body, instant)
    work.version += 1
    work.updated_at = instant
    db.flush()
    db.add(WorkAction(actor_id=user.id, submission_id=body.submission_id, request_id=request_id,
                      operation=body.operation, creation_sha256=digest))
    result = _view(request, work, actor, project, can_change, instant)
    db.commit()
    return result
