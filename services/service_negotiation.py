"""Conversations limited to the current custodian and one accepted provider."""
from uuid import uuid4

from models.service_conversation import NetworkServiceConversationMessage as AwardMessage
from models.service_negotiation import NetworkServiceNegotiationMessage as Message
from models.service_workflow import NetworkServiceRecipient as Recipient, NetworkServiceRequest as ServiceRequest
from models.user import User
from services import property_network as network, service_workflow as workflow
from services.service_conversation import message_view


def _participant(db, user, request_id, recipient_id, *, for_write=False):
    workflow.checked_user(user)
    if user.role == "admin":
        workflow.unavailable()
    if for_write:
        user, request, source, recipients = workflow.locked_request(db, user, request_id)
        recipient = recipients.get(recipient_id)
    else:
        request = db.query(ServiceRequest).filter_by(id=request_id).populate_existing().first()
        recipient = db.query(Recipient).filter_by(id=recipient_id, request_id=request_id).populate_existing().first()
        source = None
    if (request is None or recipient is None or request.status != "open" or request.project_id is not None
            or recipient.status != "accepted" or user.id not in {request.custodian_id, recipient.provider_id}
            or user.role == "admin"):
        workflow.unavailable()
    source = source or workflow.source_row(db, request.property_id, request.listing_id)
    record, custodian = source
    provider = db.query(User).filter_by(id=recipient.provider_id).populate_existing().first()
    if (not workflow.source_current(db, request, source) or not workflow.eligible(db, recipient, request)
            or provider is None or provider.role == "admin" or custodian.role == "admin"
            or not workflow.source_authorized(db, custodian, record)):
        workflow.unavailable()
    return user, "owner" if user.id == custodian.id else "provider"


def access(db, user, request_id, recipient_id):
    _participant(db, user, request_id, recipient_id)
    return {"request_id": str(request_id), "recipient_id": str(recipient_id), "can_open_conversation": True}


def list_messages(db, user, request_id, recipient_id, page):
    _participant(db, user, request_id, recipient_id)
    rows = db.query(Message).filter_by(request_id=request_id, recipient_id=recipient_id).order_by(
        Message.created_at.desc(), Message.id.desc())
    return {"request_id": str(request_id), "recipient_id": str(recipient_id), **network.page(rows, page, message_view)}


def _own_message(db, sender_id, submission_id):
    return db.query(Message).filter_by(sender_id=sender_id, submission_id=submission_id).first()


def own_submission(db, user, request_id, recipient_id, submission_id):
    actor, _ = _participant(db, user, request_id, recipient_id)
    row = _own_message(db, actor.id, submission_id)
    if row is None or (row.request_id, row.recipient_id) != (request_id, recipient_id):
        workflow.unavailable()
    return message_view(row)


def post_message(db, user, request_id, recipient_id, body):
    actor, role = _participant(db, user, request_id, recipient_id, for_write=True)
    previous = _own_message(db, actor.id, body.submission_id)
    if previous is not None:
        if (previous.request_id, previous.recipient_id, previous.body) != (request_id, recipient_id, body.body):
            workflow.conflict()
        return message_view(previous)
    if db.query(AwardMessage.id).filter_by(sender_id=actor.id, submission_id=body.submission_id).first() is not None:
        workflow.conflict()

    row = Message(id=uuid4(), request_id=request_id, recipient_id=recipient_id, sender_id=actor.id,
                  submission_id=body.submission_id, sender_role=role, body=body.body)
    db.add(row)
    db.flush()
    result = message_view(row)
    db.commit()
    return result
