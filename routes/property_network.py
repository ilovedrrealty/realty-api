"""Private collaboration routes; GETs never send messages or modify records."""
import logging
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Response, Query, UploadFile, File, Form
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.concurrency import run_in_threadpool
from starlette.formparsers import MultiPartException
from sqlalchemy.orm import Session
from slowapi.errors import RateLimitExceeded
from database import get_db
from models.listing import Listing
from models.property_network import NetworkProject as Project, NetworkMilestone as Milestone, NetworkProjectUpdate as Update, NetworkProjectComment as ProjectComment
from schemas.property_network import (ProjectCreate, ProjectEdit, MemberWrite, MilestoneCreate, MilestoneEdit,
    ProgressWrite, CommentWrite, ProfileWrite, PostWrite, ModeratePost, BlockWrite, Version)
from services import property_network as service
from utils.auth import get_current_user
from utils.limiter import limiter
from utils.request_security import require_json_mutation
from utils.private_network_media import MAX_BYTES

logger = logging.getLogger(__name__)
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff"}
UPLOAD_LIMIT = "Network photo exceeds the size limit"


class NetworkRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                multipart = request.headers.get("content-type", "").startswith("multipart/")
                if multipart:
                    # Reuse the JSON endpoint's exact cookie/origin validation;
                    # only media type differs for this bounded upload endpoint.
                    scope = dict(request.scope)
                    scope["headers"] = [(key, value) for key, value in request.scope["headers"] if key.lower() != b"content-type"]
                    scope["headers"].append((b"content-type", b"application/json"))
                    require_json_mutation(Request(scope))
                else:
                    require_json_mutation(request)
                ceiling = MAX_BYTES + 65536 if multipart else 64 * 1024
                consumed, receive = 0, request.receive

                async def bounded_receive():
                    nonlocal consumed
                    message = await receive()
                    consumed += len(message.get("body", b""))
                    if consumed > ceiling:
                        if multipart:
                            raise MultiPartException(UPLOAD_LIMIT)
                        raise HTTPException(413, "Network request exceeds the size limit")
                    return message

                request = Request(request.scope, receive=bounded_receive)
                if not multipart:
                    await request.body()
                result = await original(request)
                result.headers.update(NO_STORE)
                return result
            except StarletteHTTPException as exc:
                status = 413 if exc.detail == UPLOAD_LIMIT else exc.status_code
                return JSONResponse(status_code=status, content={"detail": exc.detail}, headers={**(exc.headers or {}), **NO_STORE})
            except RequestValidationError:
                return JSONResponse(status_code=422, content={"detail": "Check the network request fields"}, headers=NO_STORE)
            except RateLimitExceeded:
                return JSONResponse(status_code=429, content={"detail": "Too many network requests. Please wait."}, headers=NO_STORE)
            except Exception:
                logger.error("property_network_operation_unavailable")
                return JSONResponse(status_code=503, content={"detail": "Network records or private photos are temporarily unavailable. Refresh before retrying."}, headers=NO_STORE)
        return handler


router = APIRouter(prefix="/property-network", tags=["property network"], route_class=NetworkRoute)
Page = Query(default=1, ge=1, le=1000)


@router.get("/properties")
def properties(page: int = Page, user=Depends(get_current_user), db: Session = Depends(get_db)):
    query = service.listings_for(db, user).order_by(Listing.title, Listing.id)
    return service.page(query, page, lambda row: {"id": str(row.id), "title": row.title, "location": row.location, "type": row.type})


@router.get("/projects")
def projects(listing_id: UUID | None = None, page: int = Page, user=Depends(get_current_user), db: Session = Depends(get_db)):
    query = service.accessible_projects(db, user)
    if listing_id:
        query = query.filter(Project.listing_id == listing_id)
    return service.page(query.order_by(Project.updated_at.desc(), Project.id), page, lambda row: service.project_view(db, user, row))


@router.post("/projects", status_code=201)
@limiter.limit("15/minute")
def create_project(request: Request, response: Response, body: ProjectCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.create_project(db, user, body)


@router.get("/projects/{project_id}")
def project(project_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    row, _ = service.project_for(db, user, project_id)
    return service.project_view(db, user, row)


@router.put("/projects/{project_id}")
@limiter.limit("30/minute")
def edit_project(request: Request, response: Response, project_id: UUID, body: ProjectEdit, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.edit_project(db, user, project_id, body)


@router.get("/projects/{project_id}/members")
def members(project_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.members(db, user, project_id)


@router.post("/projects/{project_id}/members")
@limiter.limit("30/minute")
def assign(request: Request, response: Response, project_id: UUID, body: MemberWrite, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.assign(db, user, project_id, body)


@router.post("/projects/{project_id}/members/{user_code}/revoke")
@limiter.limit("30/minute")
def revoke(request: Request, response: Response, project_id: UUID, user_code: int, body: Version, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.revoke(db, user, project_id, user_code, body)


@router.get("/projects/{project_id}/milestones")
def milestones(project_id: UUID, page: int = Page, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service.project_for(db, user, project_id)
    return service.page(db.query(Milestone).filter(Milestone.project_id == project_id).order_by(Milestone.id), page, service.milestone_view)


@router.post("/projects/{project_id}/milestones", status_code=201)
@limiter.limit("30/minute")
def create_milestone(request: Request, response: Response, project_id: UUID, body: MilestoneCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.create_milestone(db, user, project_id, body)


@router.put("/milestones/{milestone_id}")
@limiter.limit("30/minute")
def edit_milestone(request: Request, response: Response, milestone_id: UUID, body: MilestoneEdit, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.edit_milestone(db, user, milestone_id, body)


@router.get("/projects/{project_id}/updates")
def updates(project_id: UUID, page: int = Page, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service.project_for(db, user, project_id)
    return service.page(db.query(Update).filter(Update.project_id == project_id).order_by(Update.created_at.desc(), Update.id), page,
                        lambda row: service.update_view(db, row))


@router.post("/projects/{project_id}/updates", status_code=201)
@limiter.limit("30/minute")
def create_update(request: Request, response: Response, project_id: UUID, body: ProgressWrite, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.create_update(db, user, project_id, body)


@router.get("/updates/{update_id}/comments")
def update_comments(update_id: UUID, page: int = Page, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service.update_for(db, user, update_id)
    return service.page(db.query(ProjectComment).filter(ProjectComment.update_id == update_id).order_by(ProjectComment.created_at, ProjectComment.id), page,
                        lambda row: service.comment_view(db, user, row))


@router.post("/updates/{update_id}/comments", status_code=201)
@limiter.limit("30/minute")
def create_update_comment(request: Request, response: Response, update_id: UUID, body: CommentWrite, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.project_comment(db, user, update_id, body)


@router.get("/properties/{listing_id}/history")
def service_history(listing_id: UUID, page: int = Page, user=Depends(get_current_user), db: Session = Depends(get_db)):
    visible = service.accessible_projects(db, user).filter(Project.listing_id == listing_id)
    if visible.first() is None:
        raise HTTPException(404, "Property service history not found")
    query = db.query(Update, Project).join(Project, Project.id == Update.project_id).filter(Update.project_id.in_(visible.with_entities(Project.id)))
    return service.page(query.order_by(Update.created_at.desc(), Update.id), page, lambda pair: {
        "id": str(pair[0].id), "project_id": str(pair[1].id), "project_title": pair[1].title, "service_category_ids": pair[1].service_category_ids,
        "project_status": pair[1].status, "kind": pair[0].kind, "body": pair[0].body,
        "occurred_on": pair[0].occurred_on, "media_ids": pair[0].media_ids, "created_at": pair[0].created_at})


@router.get("/me/profile")
def my_profile(user=Depends(get_current_user), db: Session = Depends(get_db)):
    target, row = service.profile_for(db, user, user.user_code)
    return service.profile_view(user, target, row)


@router.put("/me/profile")
@limiter.limit("20/minute")
def save_profile(request: Request, response: Response, body: ProfileWrite, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.save_profile(db, user, body)


@router.get("/profiles")
def member_profiles(q: str = Query(default="", max_length=100), page: int = Page, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.member_profiles(db, user, q, page)


@router.get("/profiles/{user_code}")
def profile(user_code: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    target, row = service.profile_for(db, user, user_code)
    return service.profile_view(user, target, row)


@router.get("/profiles/{user_code}/posts")
def posts(user_code: int, page: int = Page, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.wall_posts(db, user, user_code, page)


@router.post("/profiles/{user_code}/posts", status_code=201)
@limiter.limit("20/minute")
def create_post(request: Request, response: Response, user_code: int, body: PostWrite, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.create_post(db, user, user_code, body)


@router.post("/posts/{post_id}/moderate")
@limiter.limit("30/minute")
def moderate_post(request: Request, response: Response, post_id: UUID, body: ModeratePost, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.moderate_post(db, user, post_id, body)


@router.get("/posts/{post_id}/comments")
def comments(post_id: UUID, page: int = Page, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.post_comments(db, user, post_id, page)


@router.post("/posts/{post_id}/comments", status_code=201)
@limiter.limit("30/minute")
def create_comment(request: Request, response: Response, post_id: UUID, body: CommentWrite, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.create_post_comment(db, user, post_id, body)


@router.post("/comments/{comment_id}/hide")
@limiter.limit("30/minute")
def hide_comment(request: Request, response: Response, comment_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.hide_comment(db, user, comment_id)


@router.put("/me/blocks/{user_code}")
@limiter.limit("30/minute")
def block(request: Request, response: Response, user_code: int, body: BlockWrite, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.set_block(db, user, user_code, body)


@router.post("/media", status_code=201)
@limiter.limit("10/minute")
async def upload(request: Request, response: Response, file: UploadFile = File(...), project_id: UUID | None = Form(None),
                 user=Depends(get_current_user), db: Session = Depends(get_db)):
    raw = await file.read(MAX_BYTES + 1)
    return await run_in_threadpool(service.upload_media, db, user, project_id, raw)


@router.get("/media/{media_id}")
def media(media_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    data = service.media_bytes(db, user, media_id)
    return Response(content=data, media_type="image/webp", headers={**NO_STORE, "Content-Disposition": "inline"})
