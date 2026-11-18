"""Private, read-only operational analytics. Mount router in the main app."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from database import get_db
from schemas.analytics import AdminAnalyticsResponse
from services.analytics import build_admin_analytics
from utils.permission import require_admin

logger = logging.getLogger(__name__)


class PrivateAnalyticsRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request: Request):
            try:
                response = await handler(request)
            except Exception as exc:
                # Preserve normal status/detail without echoing validation values
                # or turning a failed report into plausible all-zero metrics.
                if isinstance(exc, HTTPException):
                    response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
                elif isinstance(exc, RequestValidationError):
                    response = JSONResponse({"detail": "Choose a 7, 30 or 90 day window"}, status_code=422)
                else:
                    logger.warning("Admin analytics unavailable (%s)", type(exc).__name__)
                    response = JSONResponse({"detail": "Analytics are temporarily unavailable"}, status_code=503)
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["Vary"] = "Cookie, Authorization"
            return response

        return private_handler


router = APIRouter(prefix="/admin/analytics", tags=["admin"], route_class=PrivateAnalyticsRoute)


@router.get("", response_model=AdminAnalyticsResponse)
def get_admin_analytics(
    days: int = Query(default=30, ge=7, le=90),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    if days not in (7, 30, 90):
        raise HTTPException(status_code=422, detail="Choose a 7, 30 or 90 day window")
    return build_admin_analytics(db, days)
