"""Authenticated in-app referral records and decisions."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from routes.property_network import NetworkRoute
from schemas.service_referral import Action, ReferralAction, ReferralCreate, ReferralPage, ReferralQuery, ReferralView, RequestReferrals
from services import service_referral as service
from utils.auth import get_current_user
from utils.limiter import limiter


router = APIRouter(prefix="/property-network", tags=["private service referrals"], route_class=NetworkRoute)
Actor = Annotated[User, Depends(get_current_user)]
Database = Annotated[Session, Depends(get_db)]
Page = Annotated[int, Query(ge=1, le=1000)]


@router.get("/service-requests/{request_id}/referrals", response_model=RequestReferrals)
def for_request(request_id: UUID, actor: Actor, db: Database, page: Page = 1):
    return service.for_request(db, actor, request_id, page)


@router.get("/service-requests/{request_id}/referral-providers")
def providers(request_id: UUID, request: Request, filters: Annotated[ReferralQuery, Query()],
              actor: Actor, db: Database):
    if any(len(request.query_params.getlist(field)) > 1 for field in ReferralQuery.model_fields):
        raise HTTPException(422, "Use one value per referral filter.")
    return service.candidates(db, actor, request_id, filters)


@router.post("/service-requests/{request_id}/referrals", response_model=ReferralView, status_code=201)
@limiter.limit("15/minute")
def create(request: Request, response: Response, request_id: UUID, body: ReferralCreate, actor: Actor, db: Database):
    return service.create(db, actor, request_id, body)


@router.get("/service-referrals", response_model=ReferralPage)
def received(actor: Actor, db: Database, view: Literal["received"] = "received", page: Page = 1):
    return service.inbox(db, actor, page)


@router.get("/service-referral-submissions/{submission_id}", response_model=ReferralView)
def submission(submission_id: UUID, actor: Actor, db: Database):
    return service.submission(db, actor, submission_id)


@router.post("/service-referrals/{referral_id}/{operation}", response_model=ReferralView)
@limiter.limit("30/minute")
def change(request: Request, response: Response, referral_id: UUID, operation: Action, body: ReferralAction,
           actor: Actor, db: Database):
    return service.change(db, actor, referral_id, body, operation)
