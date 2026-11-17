"""Property viewing itineraries and per-assignee private request inboxes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from database import get_db
from schemas.viewing import AssignedPage, ContactView, ItineraryCreate, ItineraryPage, ItineraryView, ViewingAction, ViewingView
from services import viewing as service
from utils.permission import require_role
from utils.request_security import require_json_mutation


NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Vary": "Cookie, Authorization"}
MAX_BODY = 96 * 1024
Page = Query(default=1, ge=1, le=1000)
PageSize = Query(default=30, ge=1, le=50)


class PrivateViewingRoute(APIRoute):
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
                            raise HTTPException(413, "Viewing request is too large")
                        return message

                    request = Request(request.scope, receive=bounded_receive)
                    await request.body()
                response = await original(request)
            except RequestValidationError:
                response = JSONResponse({"detail": "Check the viewing and meeting fields"}, status_code=422)
            except StarletteHTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            except Exception:
                response = JSONResponse({"detail": "Viewings are temporarily unavailable"}, status_code=503)
            response.headers.update(NO_STORE)
            return response

        return handler


router = APIRouter(prefix="/viewings", tags=["private property viewings"], route_class=PrivateViewingRoute)
require_buyer = require_role("buyer")
require_agent = require_role(*service.AGENT_ROLES)
require_participant = require_role("buyer", *service.AGENT_ROLES)


@router.get("/mine", response_model=ItineraryPage)
def mine(page: int = Page, page_size: int = PageSize, user=Depends(require_buyer), db: Session = Depends(get_db)):
    return service.mine(db, user, page, page_size)


@router.get("/assigned", response_model=AssignedPage)
def assigned(page: int = Page, page_size: int = PageSize, user=Depends(require_agent), db: Session = Depends(get_db)):
    return service.assigned(db, user, page, page_size)


@router.get("/contact/{listing_id}", response_model=ContactView)
def contact(listing_id: UUID, user=Depends(require_buyer), db: Session = Depends(get_db)):
    return service.contact(db, listing_id)


@router.post("", response_model=ItineraryView, status_code=201)
def create(body: ItineraryCreate, user=Depends(require_buyer), db: Session = Depends(get_db)):
    return service.create(db, user, body)


@router.post("/{viewing_id}/action", response_model=ViewingView)
def action(viewing_id: UUID, body: ViewingAction, user=Depends(require_participant), db: Session = Depends(get_db)):
    return service.action(db, user, viewing_id, body)
