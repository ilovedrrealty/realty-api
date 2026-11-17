"""Private shortlists shared between a buyer and their assigned Realtor."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from database import get_db
from schemas.shortlist import (EmptyBody, OwnerShortlistView, ReceivedShortlistView, ShortlistArchive, ShortlistList,
                               ShortlistShare, ShortlistUpdate, ShortlistWrite)
from services import shortlist as service
from utils.permission import require_role
from utils.request_security import require_json_mutation


NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Vary": "Cookie, Authorization"}
MAX_BODY = 64 * 1024


class PrivateShortlistRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                require_json_mutation(request)
                if request.method in {"POST", "PUT"}:
                    receive, consumed = request.receive, 0

                    async def bounded_receive():
                        nonlocal consumed
                        message = await receive()
                        consumed += len(message.get("body", b""))
                        if consumed > MAX_BODY:
                            raise HTTPException(413, "Shortlist request is too large")
                        return message

                    request = Request(request.scope, receive=bounded_receive)
                    await request.body()
                response = await original(request)
            except RequestValidationError:
                response = JSONResponse({"detail": "Check the shortlist fields"}, status_code=422)
            except StarletteHTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            except Exception:
                response = JSONResponse({"detail": "Shortlists are temporarily unavailable"}, status_code=503)
            response.headers.update(NO_STORE)
            return response

        return handler


router = APIRouter(prefix="/shortlists", tags=["private shortlists"], route_class=PrivateShortlistRoute)
require_member = require_role("buyer", "realtor")


@router.get("/owned", response_model=ShortlistList)
def owned(user=Depends(require_member), db: Session = Depends(get_db)):
    return service.owned(db, user)


@router.get("/received", response_model=ShortlistList)
def received(user=Depends(require_member), db: Session = Depends(get_db)):
    return service.received(db, user)


@router.post("/owned", response_model=OwnerShortlistView, status_code=201)
def create(body: ShortlistWrite, user=Depends(require_member), db: Session = Depends(get_db)):
    return service.create(db, user, body)


@router.get("/owned/{shortlist_id}", response_model=OwnerShortlistView)
def get_owned(shortlist_id: UUID, user=Depends(require_member), db: Session = Depends(get_db)):
    return service.read_owned(db, user, shortlist_id)


@router.put("/owned/{shortlist_id}", response_model=OwnerShortlistView)
def update_owned(shortlist_id: UUID, body: ShortlistUpdate, user=Depends(require_member), db: Session = Depends(get_db)):
    return service.update_owned(db, user, shortlist_id, body)


@router.get("/received/{grant_id}", response_model=ReceivedShortlistView)
def get_received(grant_id: UUID, user=Depends(require_member), db: Session = Depends(get_db)):
    return service.read_received(db, user, grant_id)


@router.put("/received/{grant_id}", response_model=ReceivedShortlistView)
def update_received(grant_id: UUID, body: ShortlistUpdate, user=Depends(require_member), db: Session = Depends(get_db)):
    return service.update_received(db, user, grant_id, body)


@router.post("/owned/{shortlist_id}/share", response_model=OwnerShortlistView)
def share(shortlist_id: UUID, body: ShortlistShare, user=Depends(require_member), db: Session = Depends(get_db)):
    return service.share(db, user, shortlist_id, body)


@router.post("/owned/{shortlist_id}/shares/{grant_id}/revoke", response_model=OwnerShortlistView)
def revoke(shortlist_id: UUID, grant_id: UUID, body: EmptyBody,
           user=Depends(require_member), db: Session = Depends(get_db)):
    return service.revoke(db, user, shortlist_id, grant_id)


@router.post("/owned/{shortlist_id}/archive", status_code=204)
def archive(shortlist_id: UUID, body: ShortlistArchive, user=Depends(require_member), db: Session = Depends(get_db)):
    service.archive(db, user, shortlist_id, body.expected_version)
    return Response(status_code=204)
