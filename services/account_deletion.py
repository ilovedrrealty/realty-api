"""Account deletion requests, cancellation and recorded Admin review."""

from datetime import datetime, timezone
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException
from sqlalchemy import func, select

from models.account_deletion import AccountDeletionEvent, AccountDeletionRequest
from models.user import User


LEGACY_NAMESPACE = UUID("ec0ff1a0-99ec-40aa-8806-0e54814d917c")


def _now():
    return datetime.now(timezone.utc)


def _state(row):
    if row is None:
        return dict(request_id=None, status="none", requested_at=None, review_after=None,
                    version=0, reviewed_at=None, review_reason=None)
    return dict(request_id=row.id, status=row.status, requested_at=row.requested_at, review_after=None,
                version=row.version, reviewed_at=row.reviewed_at, review_reason=row.review_reason)


def _legacy_id(user):
    when = user.deletion_requested_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
    return uuid5(LEGACY_NAMESPACE, f"{user.id}:{when}")


def _active(db, user_id):
    return db.scalar(select(AccountDeletionRequest).where(
        AccountDeletionRequest.user_id == user_id, AccountDeletionRequest.status != "cancelled",
    ))


def _lock_user(db, user_id):
    return db.scalar(select(User).where(User.id == user_id).with_for_update()
                     .execution_options(populate_existing=True))


def _event(db, row, actor_id, action, *, reason=None, occurred_at=None):
    db.add(AccountDeletionEvent(id=uuid4(), request_id=row.id, actor_id=actor_id,
                                action=action, version=row.version, reason=reason,
                                occurred_at=occurred_at or _now()))


def _adopt_legacy(db, user):
    row = _active(db, user.id)
    if row is None and user.deletion_requested_at is not None:
        row = AccountDeletionRequest(id=_legacy_id(user), user_id=user.id,
                                     requested_at=user.deletion_requested_at, status="pending_review", version=1)
        db.add(row)
        db.flush()
        _event(db, row, user.id, "legacy_requested", occurred_at=row.requested_at)
    return row


def own_state(db, user):
    row = db.scalar(select(AccountDeletionRequest).where(AccountDeletionRequest.user_id == user.id)
                     .order_by(AccountDeletionRequest.requested_at.desc(), AccountDeletionRequest.id.desc()).limit(1))
    if user.deletion_requested_at is not None and (row is None or row.status == "cancelled"):
        return dict(request_id=_legacy_id(user), status="pending_review", requested_at=user.deletion_requested_at,
                    review_after=None, version=1, reviewed_at=None, review_reason=None)
    return _state(row)


def request_deletion(db, user_id, body):
    user = _lock_user(db, user_id)
    if body.confirm_email.casefold() != user.email.casefold():
        raise HTTPException(422, "Confirm your current account email")
    row = _adopt_legacy(db, user)
    if row is None:
        when = _now()
        row = AccountDeletionRequest(id=uuid4(), user_id=user.id, requested_at=when,
                                     status="pending_review", version=1)
        db.add(row)
        db.flush()
        _event(db, row, user.id, "requested", occurred_at=when)
    user.deletion_requested_at = row.requested_at
    result = _state(row)
    db.commit()
    return result


def cancel_deletion(db, user_id, body):
    user = _lock_user(db, user_id)
    _adopt_legacy(db, user)
    row = db.scalar(select(AccountDeletionRequest).where(
        AccountDeletionRequest.id == body.request_id, AccountDeletionRequest.user_id == user.id,
    ).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, "Account deletion request unavailable")
    if row.status == "cancelled":
        result = _state(row)
        db.commit()
        return result
    if row.version != body.expected_version:
        raise HTTPException(409, "This request changed. Reload its current status before continuing")
    row.status = "cancelled"
    row.cancelled_at = _now()
    row.version += 1
    user.deletion_requested_at = None
    _event(db, row, user.id, "cancelled", occurred_at=row.cancelled_at)
    result = _state(row)
    db.commit()
    return result


def admin_list(db, *, status, limit, offset):
    filters = [AccountDeletionRequest.status == status] if status else [AccountDeletionRequest.status != "cancelled"]
    total = db.scalar(select(func.count()).select_from(AccountDeletionRequest).where(*filters))
    rows = db.execute(select(AccountDeletionRequest, User).join(
        User, User.id == AccountDeletionRequest.user_id,
    ).where(*filters).order_by(AccountDeletionRequest.requested_at, AccountDeletionRequest.id)
                       .offset(offset).limit(limit)).all()
    return dict(total=total, items=[dict(_state(row), user_id=user.id,
                                        display_name=user.display_name, email=user.email) for row, user in rows])


def review(db, request_id, admin_id, body):
    user_id = db.scalar(select(AccountDeletionRequest.user_id).where(AccountDeletionRequest.id == request_id))
    if user_id is None:
        raise HTTPException(404, "Account deletion request unavailable")
    _lock_user(db, user_id)
    row = db.scalar(select(AccountDeletionRequest).where(AccountDeletionRequest.id == request_id)
                     .with_for_update().execution_options(populate_existing=True))
    if row.status == "cancelled":
        raise HTTPException(409, "This request was cancelled")
    if row.status == body.status and row.review_reason == body.reason and row.reviewed_by_id == admin_id:
        return _state(row)
    if row.version != body.expected_version:
        raise HTTPException(409, "This request changed. Reload its current status before continuing")
    row.status = body.status
    row.reviewed_at = _now()
    row.reviewed_by_id = admin_id
    row.review_reason = body.reason
    row.version += 1
    _event(db, row, admin_id, body.status, reason=body.reason, occurred_at=row.reviewed_at)
    result = _state(row)
    db.commit()
    return result
