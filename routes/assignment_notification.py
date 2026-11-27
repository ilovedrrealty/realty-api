"""Admin-only assignment history, never recipients/messages or a send endpoint."""
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from database import get_db
from routes.lead_delivery import PrivateDeliveryRoute
from schemas.assignment_notification import AssignmentHistoryPage
from services.assignment_notification import history
from utils.permission import require_admin

router = APIRouter(tags=["assignment history"], route_class=PrivateDeliveryRoute)


@router.get("/admin/leads/{lead_id}/assignment-history", response_model=AssignmentHistoryPage)
def lead_assignment_history(lead_id: UUID, limit: int = Query(10, ge=1, le=50),
                            offset: int = Query(0, ge=0, le=10000),
                            user=Depends(require_admin), db: Session = Depends(get_db)):
    return history(db, source_kind="lead", source_id=lead_id, limit=limit, offset=offset)


@router.get("/admin/users/{user_id}/assignment-history", response_model=AssignmentHistoryPage)
def account_assignment_history(user_id: UUID, limit: int = Query(10, ge=1, le=50),
                               offset: int = Query(0, ge=0, le=10000),
                               user=Depends(require_admin), db: Session = Depends(get_db)):
    return history(db, source_kind="account", source_id=user_id, limit=limit, offset=offset)
