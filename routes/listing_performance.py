"""Scope private listing performance to its current custodian and assigned staff."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from database import get_db
from models.user import User
from schemas.listing_performance import ListingPerformance, NewPerformanceEntry, PerformanceEntry
from services import listing_performance as service
from utils.auth import get_current_user
from utils.request_security import require_json_mutation


NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Vary": "Cookie, Authorization"}
MAX_BODY = 8192


class PerformanceRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                require_json_mutation(request)
                if request.method == "POST":
                    receive, consumed = request.receive, 0

                    async def bounded_receive():
                        nonlocal consumed
                        message = await receive()
                        consumed += len(message.get("body", b""))
                        if consumed > MAX_BODY:
                            raise HTTPException(413, "Performance entry is too large.")
                        return message

                    request = Request(request.scope, receive=bounded_receive)
                    await request.body()
                response = await original(request)
            except RequestValidationError:
                response = JSONResponse({"detail": "Check the listing performance fields and dates."}, status_code=422)
            except StarletteHTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            except Exception:
                response = JSONResponse({"detail": "Listing performance is temporarily unavailable."}, status_code=503)
            response.headers.update(NO_STORE)
            return response

        return handler


router = APIRouter(prefix="/listings/{listing_id}/performance", tags=["listing-performance"], route_class=PerformanceRoute)
Actor = Annotated[User, Depends(get_current_user)]
Database = Annotated[Session, Depends(get_db)]


@router.get("", response_model=ListingPerformance)
def report(listing_id: UUID, actor: Actor, db: Database, start: date | None = None, end: date | None = None):
    return service.report(db, actor, listing_id, start, end)


@router.post("/entries", response_model=PerformanceEntry)
def add_entry(listing_id: UUID, body: NewPerformanceEntry, actor: Actor, db: Database):
    return service.add_entry(db, actor, listing_id, body)
