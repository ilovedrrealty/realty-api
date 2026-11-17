"""Authorize transaction documents against current room membership on every operation."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, func, or_

from models.transaction_document import TransactionDocument as Document, TransactionDocumentVersion as Version
from models.transaction_room import TransactionRoomGrant as Grant
from models.user import User
from services.transaction_room import MANAGERS, PROFESSIONAL_ACCOUNTS, record_event, require_version, room_for
from utils.transaction_document_storage import DocumentContent, read_document


def _missing():
    return HTTPException(404, "Transaction document not found.")


def _get_version(db, room_id, document_id, version_id):
    version = db.query(Version).filter(
        Version.room_id == room_id, Version.document_id == document_id, Version.id == version_id,
    ).populate_existing().one_or_none()
    if version is None:
        raise _missing()
    return version


def _content(version):
    return DocumentContent(version.content_type, version.byte_count, version.sha256)


def _receipt(version):
    return dict(document_id=version.document_id, version_id=version.id, number=version.number, status=version.status)


def _may_append(document, actor, role):
    return role in MANAGERS or document.creator_id == actor.id


def reserve(db, actor, room_id, body):
    room, role = room_for(db, actor, room_id, permission="contribute")
    existing = db.query(Version, Document.title).join(Document, and_(
        Document.room_id == Version.room_id, Document.id == Version.document_id,
    )).filter(
        Version.room_id == room.id, Version.uploader_id == actor.id, Version.request_id == body.request_id,
    ).one_or_none()
    expected = DocumentContent(body.content_type, body.byte_count, body.sha256)
    if existing is not None:
        version, title = existing
        same_document = version.document_id == body.document_id if body.document_id else version.number == 1
        if not same_document or title != body.title or version.file_name != body.file_name or _content(version) != expected:
            raise HTTPException(409, "This request already belongs to a different document upload.")
        return _receipt(version)

    number = 1
    if body.document_id is None:
        document = Document(room_id=room.id, id=uuid4(), title=body.title, creator_id=actor.id)
        db.add(document)
        db.flush()
    else:
        document = db.get(Document, (room.id, body.document_id))
        if document is None or not _may_append(document, actor, role):
            raise _missing()
        if body.title != document.title:
            raise HTTPException(409, "Use the existing document title when adding a version.")
        previous = db.query(func.max(Version.number)).filter(
            Version.room_id == room.id, Version.document_id == document.id,
        ).scalar()
        number = previous + 1

    version = Version(
        room_id=room.id, document_id=document.id, id=uuid4(), number=number,
        request_id=body.request_id, uploader_id=actor.id, file_name=body.file_name,
        content_type=body.content_type, byte_count=body.byte_count, sha256=body.sha256, status="reserved",
    )
    db.add(version)
    receipt = _receipt(version)
    db.commit()
    return receipt


def pending_upload(db, actor, room_id, document_id, version_id):
    # Check the live contribution scope, then release locks before reading bytes
    # from the client or contacting the storage provider.
    room_for(db, actor, room_id, permission="contribute")
    version = _get_version(db, room_id, document_id, version_id)
    if version.uploader_id != actor.id:
        raise _missing()
    content, receipt = _content(version), _receipt(version)
    db.rollback()
    return content, receipt


def complete_upload(db, actor, room_id, document_id, version_id, expected):
    room, role = room_for(db, actor, room_id, permission="contribute")
    version = _get_version(db, room_id, document_id, version_id)
    if version.uploader_id != actor.id or _content(version) != expected:
        raise _missing()
    if version.status != "reserved":
        return _receipt(version)

    version.status = "submitted"
    version.uploaded_at = datetime.now(timezone.utc)
    record_event(db, room, actor, role, "document_version_submitted", version.id)
    receipt = _receipt(version)
    db.commit()
    return receipt


def _current_grant(db, room_id, actor_id):
    return db.query(Grant.id).filter(
        Grant.room_id == room_id, Grant.recipient_id == actor_id, Grant.state == "active",
    ).scalar()


def _may_download(version, role, grant_id):
    if version.status == "reserved":
        return False
    if role in MANAGERS:
        return True
    return version.status == "approved" and (
        (role == "buyer" and version.share_with_buyer)
        or (role == "professional" and grant_id is not None and version.professional_grant_id == grant_id)
    )


def list_documents(db, actor, room_id):
    room, role = room_for(db, actor, room_id, stabilize=True)
    staff = role in MANAGERS
    grant_id = _current_grant(db, room_id, actor.id) if role == "professional" else None
    query = db.query(Document, Version).join(Version, and_(
        Version.room_id == Document.room_id, Version.document_id == Document.id,
    )).filter(Document.room_id == room.id, Version.status != "reserved")
    if not staff:
        permitted = Version.share_with_buyer.is_(True) if role == "buyer" else (
            Version.professional_grant_id == grant_id if grant_id is not None else False
        )
        query = query.filter(or_(Version.uploader_id == actor.id, and_(Version.status == "approved", permitted)))

    results = {}
    for document, version in query.order_by(Document.created_at.desc(), Document.id, Version.number.desc()):
        if document.id not in results:
            results[document.id] = dict(
                id=document.id, title=document.title, versions=[],
                can_add_version=room.status == "open" and _may_append(document, actor, role),
            )
        results[document.id]["versions"].append(dict(
            id=version.id, number=version.number, file_name=version.file_name, content_type=version.content_type,
            byte_count=version.byte_count, uploaded_at=version.uploaded_at, reviewed_at=version.reviewed_at,
            status=version.status, can_download=_may_download(version, role, grant_id),
            can_review=staff and room.status == "open" and version.status in {"approved", "submitted"},
            share_with_buyer=version.share_with_buyer if staff else None,
            professional_grant_id=version.professional_grant_id if staff else None,
        ))
    return {"items": list(results.values()), "can_upload": room.status == "open"}


def _authorized_download(db, actor, room_id, document_id, version_id, *, stabilize=False):
    _, role = room_for(db, actor, room_id, stabilize=stabilize)
    version = _get_version(db, room_id, document_id, version_id)
    grant_id = _current_grant(db, room_id, actor.id) if role == "professional" else None
    if not _may_download(version, role, grant_id):
        raise _missing()
    return version


def download(db, actor, room_id, document_id, version_id):
    version = _authorized_download(db, actor, room_id, document_id, version_id)
    expected, file_name = _content(version), version.file_name
    db.rollback()
    data = read_document(room_id, document_id, version_id, expected)
    # Assignment or access may change while storage is being read.
    current = _authorized_download(db, actor, room_id, document_id, version_id, stabilize=True)
    if _content(current) != expected or current.file_name != file_name:
        raise _missing()
    return data, expected.content_type, file_name


def review(db, actor, room_id, document_id, version_id, body):
    room, role = room_for(db, actor, room_id, permission="manage")
    require_version(room, body.expected_version)
    version = _get_version(db, room_id, document_id, version_id)
    if version.status not in {"submitted", "approved"}:
        raise HTTPException(409, "This document version cannot be reviewed.")
    if body.professional_grant_id is not None:
        grant = db.query(Grant.id).join(User, User.id == Grant.recipient_id).filter(
            Grant.room_id == room_id, Grant.id == body.professional_grant_id, Grant.state == "active",
            User.status == "active", User.email_verified.is_(True), User.role.in_(PROFESSIONAL_ACCOUNTS),
        ).scalar()
        if grant is None:
            raise HTTPException(422, "Select a professional with active access to this room.")

    previous = version.status
    if (previous == body.status and version.share_with_buyer == body.share_with_buyer
            and version.professional_grant_id == body.professional_grant_id):
        return _receipt(version)
    version.status = body.status
    version.reviewed_at = datetime.now(timezone.utc)
    version.reviewer_id = actor.id
    version.share_with_buyer = body.share_with_buyer
    version.professional_grant_id = body.professional_grant_id
    if previous == "approved":
        action = "document_version_access_changed" if body.status == "approved" else "document_version_withdrawn"
    else:
        action = "document_version_approved" if body.status == "approved" else "document_version_rejected"
    record_event(db, room, actor, role, action, version.id)
    receipt = _receipt(version)
    db.commit()
    return receipt
