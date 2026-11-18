"""Private rental instructions for current owners and confirmed guests."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from database import get_db
from schemas.arrival_guide import ArrivalGuideWrite, GuestArrivalGuide, OwnerArrivalGuide
from services import arrival_guide
from utils.auth import get_current_user
from utils.permission import require_role
from utils.request_security import require_json_mutation


NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Vary": "Cookie, Authorization"}
# Eight bounded text fields still fit when all non-BMP characters are escaped.
MAX_GUIDE_BODY = 160 * 1024


class PrivateArrivalGuideRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                require_json_mutation(request)
                if request.method == "PUT":
                    receive, consumed = request.receive, 0

                    async def bounded_receive():
                        nonlocal consumed
                        message = await receive()
                        consumed += len(message.get("body", b""))
                        if consumed > MAX_GUIDE_BODY:
                            raise HTTPException(413, "Arrival guide is too large")
                        return message

                    request = Request(request.scope, receive=bounded_receive)
                    await request.body()
                response = await original(request)
            except RequestValidationError:
                response = JSONResponse({"detail": "Check the arrival guide fields"}, status_code=422)
            except StarletteHTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            except Exception:
                # No guide content, validation input, or driver data in the response or logs.
                response = JSONResponse({"detail": "Arrival guide is temporarily unavailable"}, status_code=503)
            response.headers.update(NO_STORE)
            return response

        return handler


router = APIRouter(prefix="/bookings", tags=["arrival guides"], route_class=PrivateArrivalGuideRoute)
require_owner = require_role("owner")


@router.get("/listings/{listing_id}/arrival-guide", response_model=OwnerArrivalGuide)
def get_owner_arrival_guide(listing_id: UUID, user=Depends(require_owner), db: Session = Depends(get_db)):
    return arrival_guide.read_owner_guide(db, user.id, listing_id)


@router.put("/listings/{listing_id}/arrival-guide", response_model=OwnerArrivalGuide)
def put_owner_arrival_guide(listing_id: UUID, body: ArrivalGuideWrite,
                            user=Depends(require_owner), db: Session = Depends(get_db)):
    return arrival_guide.update_owner_guide(db, user.id, listing_id, body)


@router.get("/{booking_id}/arrival-guide", response_model=GuestArrivalGuide)
def get_guest_arrival_guide(booking_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return arrival_guide.read_guest_guide(db, user.id, booking_id)
