"""Private current owner inquiries; no assignment/status mutation or replay."""
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException
from database import get_db
from utils.permission import require_role
from schemas.owner_leads import OwnerLeadPage
from services.owner_leads import owner_lead_page

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Vary": "Cookie, Authorization"}


class PrivateOwnerLeadRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request):
            try:
                response = await original(request)
            except RequestValidationError:
                response = JSONResponse({"detail": "Check the inquiry page and filters"}, status_code=422)
            except HTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            except Exception:
                # No driver statement, row, contact or attribution data in logs.
                response = JSONResponse({"detail": "Inquiries are temporarily unavailable. Refresh to try again."}, status_code=503)
            response.headers.update(NO_STORE)
            return response
        return handler


router = APIRouter(tags=["owner inquiries"], route_class=PrivateOwnerLeadRoute)


@router.get("/owner/leads", response_model=OwnerLeadPage)
def get_owner_leads(page: int = Query(1, ge=1, le=10000),
                    state: Literal["all", "open", "closed"] = "all", property_id: UUID | None = None,
                    user=Depends(require_role("owner", "admin")), db: Session = Depends(get_db)):
    return owner_lead_page(db, user, page=page, state=state, property_id=property_id)
