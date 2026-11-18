"""Private transaction-room metadata, invitations and closing coordination."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from database import get_db
from models.user import User
from schemas.transaction_room import (
    AcceptInvitation, ChangeMilestone, ChangeTask, CreateRoom, CreateTask, History,
    InviteProfessional, LeadRoom, PendingInvitations, RoomDetail, Rooms, VersionChange,
)
from services import transaction_room as service
from utils.auth import get_current_user
from utils.request_security import require_json_mutation


NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Vary": "Cookie, Authorization"}
MAX_BODY = 8 * 1024


class TransactionRoomRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                require_json_mutation(request)
                if request.method in {"PUT", "POST", "PATCH"}:
                    receive, consumed = request.receive, 0

                    async def bounded_receive():
                        nonlocal consumed
                        message = await receive()
                        consumed += len(message.get("body", b""))
                        if consumed > MAX_BODY:
                            raise HTTPException(413, "Transaction room request is too large.")
                        return message

                    request = Request(request.scope, receive=bounded_receive)
                    await request.body()
                response = await original(request)
            except RequestValidationError:
                response = JSONResponse({"detail": "Check the transaction room fields and dates."}, status_code=422)
            except StarletteHTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            except Exception:
                response = JSONResponse({"detail": "Transaction rooms are temporarily unavailable."}, status_code=503)
            response.headers.update(NO_STORE)
            return response

        return handler


router = APIRouter(prefix="/transactions", tags=["transaction-rooms"], route_class=TransactionRoomRoute)
Actor = Annotated[User, Depends(get_current_user)]
Database = Annotated[Session, Depends(get_db)]
Page = Annotated[int, Query(ge=1, le=10000)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get("", response_model=Rooms)
def rooms(actor: Actor, db: Database, page: Page = 1, page_size: PageSize = 50):
    return service.list_rooms(db, actor, page, page_size)


@router.get("/leads/{lead_id}", response_model=LeadRoom)
def for_lead(lead_id: UUID, actor: Actor, db: Database):
    return service.for_lead(db, actor, lead_id)


@router.post("/leads/{lead_id}", response_model=RoomDetail, status_code=201)
def create(lead_id: UUID, body: CreateRoom, actor: Actor, db: Database):
    return service.create(db, actor, lead_id, body)


@router.get("/invitations", response_model=PendingInvitations)
def invitations(actor: Actor, db: Database, page: Page = 1, page_size: PageSize = 50):
    return service.pending(db, actor, page, page_size)


@router.post("/invitations/{grant_id}/accept", response_model=RoomDetail)
def accept(grant_id: UUID, body: AcceptInvitation, actor: Actor, db: Database):
    return service.accept(db, actor, grant_id)


@router.get("/{room_id}", response_model=RoomDetail)
def read(room_id: UUID, actor: Actor, db: Database):
    return service.read(db, actor, room_id)


@router.get("/{room_id}/history", response_model=History)
def history(room_id: UUID, actor: Actor, db: Database, before: Annotated[int | None, Query(ge=1)] = None,
            limit: PageSize = 100):
    return service.history(db, actor, room_id, before, limit)


@router.post("/{room_id}/invitations", response_model=RoomDetail)
def invite(room_id: UUID, body: InviteProfessional, actor: Actor, db: Database):
    return service.invite(db, actor, room_id, body)


@router.post("/{room_id}/members/{grant_id}/revoke", response_model=RoomDetail)
def revoke(room_id: UUID, grant_id: UUID, body: VersionChange, actor: Actor, db: Database):
    return service.revoke(db, actor, room_id, grant_id, body)


@router.post("/{room_id}/tasks", response_model=RoomDetail, status_code=201)
def create_task(room_id: UUID, body: CreateTask, actor: Actor, db: Database):
    return service.create_task(db, actor, room_id, body)


@router.patch("/{room_id}/tasks/{task_id}", response_model=RoomDetail)
def update_task(room_id: UUID, task_id: UUID, body: ChangeTask, actor: Actor, db: Database):
    return service.update_task(db, actor, room_id, task_id, body)


@router.post("/{room_id}/milestones", response_model=RoomDetail, status_code=201)
def create_milestone(room_id: UUID, body: ChangeMilestone, actor: Actor, db: Database):
    return service.save_milestone(db, actor, room_id, body)


@router.put("/{room_id}/milestones/{milestone_id}", response_model=RoomDetail)
def update_milestone(room_id: UUID, milestone_id: UUID, body: ChangeMilestone, actor: Actor, db: Database):
    return service.save_milestone(db, actor, room_id, body, milestone_id)


@router.post("/{room_id}/close", response_model=RoomDetail)
def close(room_id: UUID, body: VersionChange, actor: Actor, db: Database):
    return service.close(db, actor, room_id, body)
