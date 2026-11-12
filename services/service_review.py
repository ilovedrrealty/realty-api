"""Private feedback shared only by the current participants of a completed award."""
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import exists, or_

from models.property_network import NetworkProjectUpdate as Update
from models.service_review import NetworkServiceReview as Review
from models.service_visit import NetworkServiceWork as Work, NetworkServiceWorkAction as Action
from models.service_workflow import NetworkServiceRecipient as Recipient, NetworkServiceRequest as ServiceRequest
from services import service_visit, service_workflow as workflow


def _completed_award(db, user, request_id):
    workflow.checked_user(user)
    if user.role == "admin":
        workflow.unavailable()
    candidate = db.query(ServiceRequest.id).join(Recipient, Recipient.request_id == ServiceRequest.id).join(
        Work, Work.request_id == ServiceRequest.id).filter(
        ServiceRequest.id == request_id, ServiceRequest.status == "awarded", Recipient.status == "awarded",
        Work.status == "completed", or_(ServiceRequest.custodian_id == user.id, Recipient.provider_id == user.id),
    ).first()
    if candidate is None:
        workflow.unavailable()

    # Use the same ordered locks as an award mutation until feedback is projected or committed.
    actor, role = service_visit.require_live_participant(db, user, request_id, for_write=True)
    request = db.get(ServiceRequest, request_id)
    source, custodian = workflow.source_row(db, request.property_id, request.listing_id)
    if custodian is None or not workflow.source_authorized(db, custodian, source):
        workflow.unavailable()
    approved = exists().where(Action.request_id == Work.request_id, Action.actor_id == custodian.id,
                              Action.operation == "approve_completion").correlate(Work)
    winner = db.query(Recipient.provider_id).join(Work, Work.request_id == Recipient.request_id).join(
        Update, Update.id == Work.completion_update_id).filter(
        Recipient.request_id == request_id, Recipient.status == "awarded", Work.status == "completed", approved,
        Update.project_id == request.project_id, Update.author_id == custodian.id, Update.kind == "progress",
        Update.body == Work.completion_note, Update.occurred_on == Work.completed_on,
        Update.correction_of.is_(None), Update.media_ids == [],
    ).first()
    if winner is None:
        workflow.unavailable()
    return actor, role, custodian.id, winner.provider_id


def _view(row):
    return {"id": str(row.id), "stars": row.stars, "comment": row.comment, "created_at": row.created_at}


def _review(db, request_id, custodian_id, provider_id):
    row = db.query(Review).filter_by(request_id=request_id).first()
    if row is not None and (row.reviewer_id, row.provider_id) != (custodian_id, provider_id):
        workflow.unavailable()
    return row


def detail(db, user, request_id):
    _, role, custodian_id, provider_id = _completed_award(db, user, request_id)
    row = _review(db, request_id, custodian_id, provider_id)
    return {"request_id": str(request_id), "review": _view(row) if row is not None else None,
            "can_submit": role == "owner" and row is None}


def own_submission(db, user, request_id, submission_id):
    actor, _, custodian_id, provider_id = _completed_award(db, user, request_id)
    row = _review(db, request_id, custodian_id, provider_id)
    if row is None or row.reviewer_id != actor.id or row.submission_id != submission_id:
        workflow.unavailable()
    return _view(row)


def submit(db, user, request_id, body):
    actor, role, custodian_id, provider_id = _completed_award(db, user, request_id)
    if role != "owner":
        raise HTTPException(403, "Only the property custodian can leave a service review")
    row = _review(db, request_id, custodian_id, provider_id)
    if row is not None:
        if (row.submission_id, row.stars, row.comment) != (body.submission_id, body.stars, body.comment):
            workflow.conflict()
        return _view(row)
    if db.query(Review.id).filter_by(reviewer_id=actor.id, submission_id=body.submission_id).first() is not None:
        workflow.conflict()

    row = Review(id=uuid4(), request_id=request_id, reviewer_id=actor.id, provider_id=provider_id,
                 submission_id=body.submission_id, stars=body.stars, comment=body.comment)
    db.add(row)
    db.flush()
    result = _view(row)
    db.commit()
    return result
