"""Session-authenticated native messages; no external messaging provider calls."""
import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from slowapi.errors import RateLimitExceeded
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from database import get_db
from schemas.native_inbox import BlockWrite, ConversationCreate, EntryWrite, ReadWrite, ReviewCreate, VisibilityWrite
from services import native_inbox as service
from utils.auth import get_current_user
from utils.limiter import limiter
from utils.request_security import require_json_mutation

logger = logging.getLogger(__name__)
PRIVATE = {"Cache-Control": "no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff",
           "Vary": "Cookie, Authorization"}
Page = Query(default=1, ge=1, le=1000)
Cursor = Query(default=None, max_length=512)


class InboxRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                require_json_mutation(request)
                received, receive = 0, request.receive

                async def bounded_receive():
                    nonlocal received
                    message = await receive()
                    received += len(message.get("body", b""))
                    if received > 64*1024:
                        raise HTTPException(413, "Inbox request exceeds the size limit")
                    return message

                request = Request(request.scope, receive=bounded_receive)
                await request.body()
                result = await original(request)
                result.headers.update(PRIVATE)
                return result
            except RateLimitExceeded:
                return JSONResponse(status_code=429, content={"detail": "Too many inbox requests. Please wait."}, headers=PRIVATE)
            except StarletteHTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers={**(exc.headers or {}), **PRIVATE})
            except RequestValidationError:
                return JSONResponse(status_code=422, content={"detail": "Check the inbox request fields"}, headers=PRIVATE)
            except Exception:
                logger.error("native_inbox_operation_unavailable")
                return JSONResponse(status_code=503, content={"detail": "Messages are temporarily unavailable. Refresh before retrying."}, headers=PRIVATE)

        return handler


router = APIRouter(prefix="/messaging", tags=["private native inbox"], route_class=InboxRoute)


def _query(request, allowed):
    if set(request.query_params)-set(allowed) or any(len(request.query_params.getlist(key))>1 for key in allowed):
        raise HTTPException(422, "Check the inbox query fields")


@router.get("/policy")
def policy(request: Request, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _query(request, ())
    return service.policy(db, user)


@router.get("/preferences")
def preferences(request: Request, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _query(request, ())
    return service.preferences(db, user)


@router.put("/preferences")
@limiter.limit("30/minute")
def save_preferences(request: Request, response: Response, body: VisibilityWrite,
                     user=Depends(get_current_user), db: Session = Depends(get_db)):
    _query(request, ())
    return service.save_preferences(db, user, body)


@router.get("/recipients")
def recipients(request: Request, q: str = Query(default="", max_length=80), page: int = Page,
               user=Depends(get_current_user), db: Session = Depends(get_db)):
    _query(request, ("q", "page"))
    return service.recipients(db, user, q, page)


@router.get("/blocks")
def blocks(request: Request, page: int = Page, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _query(request, ("page",))
    return service.blocks(db, user, page)


@router.put("/blocks/{user_id}")
@limiter.limit("30/minute")
def save_block(request: Request, response: Response, user_id: UUID, body: BlockWrite,
               user=Depends(get_current_user), db: Session = Depends(get_db)):
    _query(request, ())
    return service.save_block(db, user, user_id, body)


@router.get("/conversations")
def conversations(request: Request, view: Literal["mine", "oversight"] = "mine", page: int = Page,
                  user=Depends(get_current_user), db: Session = Depends(get_db)):
    _query(request, ("view", "page"))
    return service.conversations(db, user, view, page)


@router.post("/conversations", status_code=201)
@limiter.limit("30/minute")
def open_conversation(request: Request, response: Response, body: ConversationCreate,
                      user=Depends(get_current_user), db: Session = Depends(get_db)):
    _query(request, ())
    return service.open_conversation(db, user, body)


@router.get("/unread")
def unread(request: Request, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _query(request, ())
    return service.unread(db, user)


@router.get("/conversations/{conversation_id}")
def conversation(request: Request, conversation_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _query(request, ())
    return service.conversation(db, user, conversation_id)


@router.get("/conversations/{conversation_id}/entries")
def entries(request: Request, conversation_id: UUID, cursor: str | None = Cursor,
            user=Depends(get_current_user), db: Session = Depends(get_db)):
    _query(request, ("cursor",))
    return service.entries(db, user, conversation_id, cursor)


@router.post("/conversations/{conversation_id}/entries", status_code=201)
@limiter.limit("30/minute")
def post_entry(request: Request, response: Response, conversation_id: UUID, body: EntryWrite,
               user=Depends(get_current_user), db: Session = Depends(get_db)):
    _query(request, ())
    return service.post_entry(db, user, conversation_id, body)


@router.put("/conversations/{conversation_id}/read")
@limiter.limit("60/minute")
def mark_read(request: Request, response: Response, conversation_id: UUID, body: ReadWrite,
              user=Depends(get_current_user), db: Session = Depends(get_db)):
    _query(request, ())
    return service.mark_read(db, user, conversation_id, body)


@router.get("/commands/{submission_id}")
def command_receipt(request: Request, submission_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _query(request, ())
    return service.command_receipt(db, user, submission_id)


@router.get("/admin/legacy")
def legacy_histories(request: Request, page: int = Page, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _query(request, ("page",))
    return service.legacy_histories(db, user, page)


@router.post("/admin/reviews", status_code=201)
@limiter.limit("30/minute")
def open_review(request: Request, response: Response, body: ReviewCreate,
                user=Depends(get_current_user), db: Session = Depends(get_db)):
    _query(request, ())
    return service.open_review(db, user, body)


@router.get("/admin/reviews/{review_id}/entries")
def review_entries(request: Request, review_id: UUID, cursor: str | None = Cursor,
                   user=Depends(get_current_user), db: Session = Depends(get_db)):
    _query(request, ("cursor",))
    return service.review_entries(db, user, review_id, cursor)
