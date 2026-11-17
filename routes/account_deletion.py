"""Authenticated account deletion requests with a private Admin review queue."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from database import get_db
from models.user import User
from schemas.account_deletion import (
    AdminDeletionList, CancelDeletion, DeletionState, RequestDeletion, ReviewDeletion,
)
from services import account_deletion as deletion
from utils.auth import get_current_user
from utils.limiter import limiter
from utils.permission import require_admin
from utils.request_security import require_json_mutation


NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class AccountDeletionRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                if request.method == "POST":
                    require_json_mutation(request)
                    receive, consumed = request.receive, 0

                    async def bounded_receive():
                        nonlocal consumed
                        message = await receive()
                        consumed += len(message.get("body", b""))
                        if consumed > 8192:
                            raise HTTPException(413, "Account deletion request is too large")
                        return message

                    request = Request(request.scope, receive=bounded_receive)
                    await request.body()
                response = await original(request)
            except RequestValidationError:
                response = JSONResponse({"detail": "Check the account deletion request"}, status_code=422)
            except StarletteHTTPException as error:
                response = JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=error.headers)
            response.headers.update(NO_STORE)
            return response

        return handle


router = APIRouter(tags=["account-deletion"], route_class=AccountDeletionRoute)
Database = Annotated[Session, Depends(get_db)]
Account = Annotated[User, Depends(get_current_user)]
Admin = Annotated[User, Depends(require_admin)]


@router.get("/auth/me/delete-request", response_model=DeletionState)
def own_deletion(user: Account, db: Database):
    return deletion.own_state(db, user)


@router.post("/auth/me/delete-request", response_model=DeletionState)
@limiter.limit("6/minute")
def request_deletion(request: Request, body: RequestDeletion, user: Account, db: Database):
    return deletion.request_deletion(db, user.id, body)


@router.post("/auth/me/delete-request/cancel", response_model=DeletionState)
@limiter.limit("12/minute")
def cancel_deletion(request: Request, body: CancelDeletion, user: Account, db: Database):
    return deletion.cancel_deletion(db, user.id, body)


@router.get("/admin/account-deletion-requests", response_model=AdminDeletionList)
def list_requests(
    admin: Admin, db: Database,
    status: Literal["pending_review", "reviewed", "retention_hold", "cancelled"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
):
    return deletion.admin_list(db, status=status, limit=limit, offset=offset)


@router.post("/admin/account-deletion-requests/{request_id}/review", response_model=DeletionState)
@limiter.limit("20/minute")
def review_request(request: Request, request_id: UUID, body: ReviewDeletion, admin: Admin, db: Database):
    return deletion.review(db, request_id, admin.id, body)
