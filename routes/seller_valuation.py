"""Authorized staff endpoints for valuation work on an assigned seller lead."""

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
from schemas.seller_valuation import AskingCandidates, AskingChange, DraftChange, FollowUpChange, PropertyType, ReportChange, SoldChange, SoldReviewChange, ValuationWorkspace, VersionChange
from services import seller_valuation as service
from utils.auth import get_current_user
from utils.request_security import require_json_mutation


NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Vary": "Cookie, Authorization"}
MAX_BODY = 16 * 1024


class ValuationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                require_json_mutation(request, bodyless_delete=True)
                if request.method in {"PUT", "POST"}:
                    receive, consumed = request.receive, 0

                    async def bounded_receive():
                        nonlocal consumed
                        message = await receive()
                        consumed += len(message.get("body", b""))
                        if consumed > MAX_BODY:
                            raise HTTPException(413, "Valuation request is too large.")
                        return message

                    request = Request(request.scope, receive=bounded_receive)
                    await request.body()
                response = await original(request)
            except RequestValidationError:
                response = JSONResponse({"detail": "Check the valuation fields and dates."}, status_code=422)
            except StarletteHTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            except Exception:
                response = JSONResponse({"detail": "Seller valuation is temporarily unavailable."}, status_code=503)
            response.headers.update(NO_STORE)
            return response

        return handler


router = APIRouter(
    prefix="/staff/leads/{lead_id}/valuation", tags=["seller-valuation"], route_class=ValuationRoute,
)

Actor = Annotated[User, Depends(get_current_user)]
Database = Annotated[Session, Depends(get_db)]


def _read_scope(lead_id: UUID, actor: Actor, db: Database):
    service.scoped_seller(db, actor, lead_id)


def _write_scope(lead_id: UUID, actor: Actor, db: Database):
    service.scoped_seller(db, actor, lead_id, write=True)


ReadScope = Depends(_read_scope)
WriteScope = Depends(_write_scope)


@router.get("", response_model=ValuationWorkspace, dependencies=[ReadScope])
def read(lead_id: UUID, db: Database):
    return service.read(db, lead_id)


@router.put("/draft", response_model=ValuationWorkspace, dependencies=[WriteScope])
def save_draft(lead_id: UUID, body: DraftChange, db: Database):
    return service.save_draft(db, lead_id, body)


@router.get("/asking-candidates", response_model=AskingCandidates, dependencies=[ReadScope])
def asking_candidates(lead_id: UUID, db: Database, property_type: PropertyType | None = None,
                      q: str | None = Query(default=None, max_length=80), limit: int = Query(default=20, ge=1, le=40)):
    return AskingCandidates(items=service.asking_candidates(db, property_type, q.strip() if q else None, limit))


@router.post("/comparables/asking", response_model=ValuationWorkspace, dependencies=[WriteScope])
def add_asking(lead_id: UUID, body: AskingChange, db: Database):
    return service.add_asking(db, lead_id, body)


@router.post("/comparables/sold", response_model=ValuationWorkspace, dependencies=[WriteScope])
def add_sold(lead_id: UUID, body: SoldChange, db: Database):
    return service.add_sold(db, lead_id, body)


@router.put("/comparables/{comparator_id}/sold-review", response_model=ValuationWorkspace, dependencies=[WriteScope])
def review_sold(lead_id: UUID, comparator_id: UUID, body: SoldReviewChange, actor: Actor, db: Database):
    return service.review_sold(db, actor, lead_id, comparator_id, body)


@router.delete("/comparables/{comparator_id}", response_model=ValuationWorkspace, dependencies=[WriteScope])
def remove_comparable(lead_id: UUID, comparator_id: UUID, db: Database,
                      expected_version: int = Query(ge=0, le=2147483646)):
    return service.remove_comparable(db, lead_id, comparator_id, expected_version)


@router.post("/review", response_model=ValuationWorkspace, dependencies=[WriteScope])
def review(lead_id: UUID, body: ReportChange, actor: Actor, db: Database):
    return service.review(db, actor, lead_id, body)


@router.post("/follow-ups", response_model=ValuationWorkspace, dependencies=[WriteScope])
def add_follow_up(lead_id: UUID, body: FollowUpChange, db: Database):
    return service.add_follow_up(db, lead_id, body)


@router.post("/follow-ups/{follow_up_id}/complete", response_model=ValuationWorkspace, dependencies=[WriteScope])
def complete_follow_up(lead_id: UUID, follow_up_id: UUID, body: VersionChange, db: Database):
    return service.complete_follow_up(db, lead_id, follow_up_id, body)
