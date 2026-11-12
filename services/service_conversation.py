"""Private award messages. This module has no external delivery path."""
from uuid import uuid4

from models.service_conversation import NetworkServiceConversationMessage as Message
from services import property_network as network, service_visit, service_workflow as workflow


def _view(row):
    return {"id": str(row.id), "sender": row.sender_role, "body": row.body, "created_at": row.created_at}


def _own_message(db, sender_id, submission_id):
    return db.query(Message).filter_by(sender_id=sender_id, submission_id=submission_id).first()


def list_messages(db, user, request_id, page):
    service_visit.require_live_participant(db, user, request_id)
    rows = db.query(Message).filter_by(request_id=request_id).order_by(Message.created_at.desc(), Message.id.desc())
    return {"request_id": str(request_id), **network.page(rows, page, _view)}


def own_submission(db, user, request_id, submission_id):
    actor, _ = service_visit.require_live_participant(db, user, request_id)
    row = _own_message(db, actor.id, submission_id)
    if row is None or row.request_id != request_id:
        workflow.unavailable()
    return _view(row)


def post_message(db, user, request_id, body):
    actor, role = service_visit.require_live_participant(db, user, request_id, for_write=True)
    previous = _own_message(db, actor.id, body.submission_id)
    if previous is not None:
        if previous.request_id != request_id or previous.body != body.body:
            workflow.conflict()
        return _view(previous)

    row = Message(id=uuid4(), request_id=request_id, sender_id=actor.id, submission_id=body.submission_id,
                  sender_role=role, body=body.body)
    db.add(row)
    db.flush()
    result = _view(row)
    db.commit()
    return result
