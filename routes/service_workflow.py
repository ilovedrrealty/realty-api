"""Private service request routes; no notifications or provider execution."""
from uuid import UUID
from typing import Literal
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session
from database import get_db
from routes.property_network import NetworkRoute, Page
from schemas.service_conversation import ServiceConversationWrite
from schemas.service_review import ServiceReviewWrite
from schemas.service_visit import ServiceWorkWrite
from schemas.service_workflow import (PreferenceWrite, RequestCreate, RecipientAction, ManagedRecipientAction,
    QuoteWrite, CounterWrite, ProposalAction, RequestAction, AwardWrite, ServiceFavoriteWrite)
from services import service_conversation, service_favorites, service_negotiation, service_rebooking, service_review, service_visit, service_workflow as service
from utils.auth import get_current_user
from utils.limiter import limiter

router = APIRouter(prefix="/property-network", tags=["private service workflow"], route_class=NetworkRoute)


@router.get("/me/service-preferences")
def preferences(user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.preference_view(db, user)


@router.put("/me/service-preferences")
@limiter.limit("30/minute")
def save_preferences(request: Request, response: Response, body: PreferenceWrite,
                     user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.save_preference(db, user, body)


@router.get("/service-providers")
def providers(category_id: str, property_class: str, page: int = Page,
              user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.discovery(db, user, category_id, property_class, page)


@router.post("/service-requests", status_code=201)
@limiter.limit("15/minute")
def create_request(request: Request, response: Response, body: RequestCreate,
                   user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.create_request(db, user, body)


@router.get("/service-requests")
def requests(view: Literal["managed", "received"], status: str = "all", page: int = Page,
             user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.requests_page(db, user, view, status, page)


@router.get("/service-actions/submissions/{submission_id}")
def submission(submission_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.submission(db, user, submission_id)


@router.get("/service-requests/{request_id}")
def detail(request_id: UUID, view: Literal["managed", "received"] | None = None,
           user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.get_request(db, user, request_id, view)


@router.get("/service-requests/{request_id}/comparison")
def comparison(request_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.comparison(db, user, request_id)


@router.get("/service-requests/{request_id}/events")
def events(request_id: UUID, page: int = Page, view: Literal["managed", "received"] | None = None,
           user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.events(db, user, request_id, page, view)


@router.get("/service-requests/{request_id}/recipients/{recipient_id}/proposals")
def proposal_history(request_id: UUID, recipient_id: UUID, page: int = Page, view: Literal["managed", "received"] | None = None,
                     user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.proposal_history(db, user, request_id, recipient_id, page, view)


@router.post("/service-requests/{request_id}/recipients/{recipient_id}/accept")
@limiter.limit("30/minute")
def accept(request: Request, response: Response, request_id: UUID, recipient_id: UUID, body: RecipientAction,
           user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.recipient_action(db, user, request_id, recipient_id, body, "recipient_accept")


@router.post("/service-requests/{request_id}/recipients/{recipient_id}/decline")
@limiter.limit("30/minute")
def decline(request: Request, response: Response, request_id: UUID, recipient_id: UUID, body: RecipientAction,
            user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.recipient_action(db, user, request_id, recipient_id, body, "recipient_decline")


@router.post("/service-requests/{request_id}/recipients/{recipient_id}/revoke")
@limiter.limit("30/minute")
def revoke(request: Request, response: Response, request_id: UUID, recipient_id: UUID, body: ManagedRecipientAction,
           user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.recipient_action(db, user, request_id, recipient_id, body, "recipient_revoke")


@router.post("/service-requests/{request_id}/recipients/{recipient_id}/proposals")
@limiter.limit("30/minute")
def quote(request: Request, response: Response, request_id: UUID, recipient_id: UUID, body: QuoteWrite,
          user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.write_proposal(db, user, request_id, recipient_id, body)


@router.post("/service-requests/{request_id}/recipients/{recipient_id}/counter")
@limiter.limit("30/minute")
def counter(request: Request, response: Response, request_id: UUID, recipient_id: UUID, body: CounterWrite,
            user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.write_proposal(db, user, request_id, recipient_id, body, counter=True)


@router.post("/service-proposals/{proposal_id}/accept-counter")
@limiter.limit("30/minute")
def accept_counter(request: Request, response: Response, proposal_id: UUID, body: ProposalAction,
                   user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.proposal_action(db, user, proposal_id, body, "counter_accept")


@router.post("/service-proposals/{proposal_id}/decline-counter")
@limiter.limit("30/minute")
def decline_counter(request: Request, response: Response, proposal_id: UUID, body: ProposalAction,
                    user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.proposal_action(db, user, proposal_id, body, "counter_decline")


@router.post("/service-proposals/{proposal_id}/withdraw")
@limiter.limit("30/minute")
def withdraw(request: Request, response: Response, proposal_id: UUID, body: ProposalAction,
             user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.proposal_action(db, user, proposal_id, body, "proposal_withdraw")


@router.post("/service-requests/{request_id}/award")
@limiter.limit("15/minute")
def award(request: Request, response: Response, request_id: UUID, body: AwardWrite,
          user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.close_request(db, user, request_id, body, award=True)


@router.post("/service-requests/{request_id}/cancel")
@limiter.limit("30/minute")
def cancel(request: Request, response: Response, request_id: UUID, body: RequestAction,
           user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.close_request(db, user, request_id, body)


@router.get("/service-requests/{request_id}/work")
def work_detail(request_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service_visit.get_work(db, user, request_id)


@router.post("/service-requests/{request_id}/work")
@limiter.limit("30/minute")
def save_work(request: Request, response: Response, request_id: UUID, body: ServiceWorkWrite,
              user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service_visit.mutate_work(db, user, request_id, body)


@router.get("/service-requests/{request_id}/work/submissions/{submission_id}")
def work_submission(request_id: UUID, submission_id: UUID, user=Depends(get_current_user),
                    db: Session = Depends(get_db)):
    return service_visit.get_submission(db, user, request_id, submission_id)


@router.get("/service-requests/{request_id}/conversation")
def conversation(request_id: UUID, page: int = Page, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service_conversation.list_messages(db, user, request_id, page)


@router.post("/service-requests/{request_id}/conversation", status_code=201)
@limiter.limit("30/minute")
def send_conversation_message(request: Request, response: Response, request_id: UUID, body: ServiceConversationWrite,
                              user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service_conversation.post_message(db, user, request_id, body)


@router.get("/service-requests/{request_id}/conversation/submissions/{submission_id}")
def conversation_submission(request_id: UUID, submission_id: UUID, user=Depends(get_current_user),
                            db: Session = Depends(get_db)):
    return service_conversation.own_submission(db, user, request_id, submission_id)


@router.get("/service-requests/{request_id}/recipients/{recipient_id}/conversation/access")
def negotiation_conversation_access(request_id: UUID, recipient_id: UUID, user=Depends(get_current_user),
                                    db: Session = Depends(get_db)):
    return service_negotiation.access(db, user, request_id, recipient_id)


@router.get("/service-requests/{request_id}/recipients/{recipient_id}/conversation")
def negotiation_conversation(request_id: UUID, recipient_id: UUID, page: int = Page,
                             user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service_negotiation.list_messages(db, user, request_id, recipient_id, page)


@router.post("/service-requests/{request_id}/recipients/{recipient_id}/conversation", status_code=201)
@limiter.limit("30/minute")
def send_negotiation_message(request: Request, response: Response, request_id: UUID, recipient_id: UUID,
                             body: ServiceConversationWrite, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service_negotiation.post_message(db, user, request_id, recipient_id, body)


@router.get("/service-requests/{request_id}/recipients/{recipient_id}/conversation/submissions/{submission_id}")
def negotiation_conversation_submission(request_id: UUID, recipient_id: UUID, submission_id: UUID,
                                        user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service_negotiation.own_submission(db, user, request_id, recipient_id, submission_id)


@router.get("/service-requests/{request_id}/review")
def service_review_detail(request_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service_review.detail(db, user, request_id)


@router.post("/service-requests/{request_id}/review", status_code=201)
@limiter.limit("30/minute")
def submit_service_review(request: Request, response: Response, request_id: UUID, body: ServiceReviewWrite,
                          user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service_review.submit(db, user, request_id, body)


@router.get("/service-requests/{request_id}/review/submissions/{submission_id}")
def service_review_submission(request_id: UUID, submission_id: UUID, user=Depends(get_current_user),
                              db: Session = Depends(get_db)):
    return service_review.own_submission(db, user, request_id, submission_id)


@router.get("/service-requests/{request_id}/rebook-context")
def service_rebook_context(request_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service_rebooking.context(db, user, request_id)


@router.get("/service-favorites")
def service_favorite_list(page: int = Page, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service_favorites.list_favorites(db, user, page)


@router.post("/service-favorites", status_code=201)
@limiter.limit("30/minute")
def save_service_favorite(request: Request, response: Response, body: ServiceFavoriteWrite,
                          user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service_favorites.save(db, user, body.provider_user_code)


@router.delete("/service-favorites/{favorite_id}", status_code=204, response_class=Response)
@limiter.limit("30/minute")
def remove_service_favorite(request: Request, response: Response, favorite_id: UUID,
                            user=Depends(get_current_user), db: Session = Depends(get_db)):
    service_favorites.remove(db, user, favorite_id)
    return Response(status_code=204)
