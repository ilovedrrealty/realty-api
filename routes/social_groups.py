"""Authenticated group API; membership is independent of property/project access."""
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import Response as ByteResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from database import get_db
from routes.property_network import NetworkRoute, NO_STORE
from schemas.social_groups import (GroupCreate, GroupEdit, ArchiveGroup, MembershipAction, ManageMember,
    CreateInviteLink, RevokeInviteLink, ResolveInvite, AcceptInvite, GroupPostCreate,
    ModeratePost, GroupCommentCreate, HideComment)
from services import social_groups as service
from utils.auth import get_current_user
from utils.limiter import limiter
from utils.private_network_media import MAX_BYTES

router = APIRouter(prefix="/property-network", tags=["social groups"], route_class=NetworkRoute)
Page = Query(default=1, ge=1, le=1000)


@router.get("/groups")
def groups(view: Literal["discover", "mine"] = "mine", q: str = Query(default="", max_length=100),
           page: int = Page, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.listed(db, user, view, q.strip(), page)


@router.post("/groups", status_code=201)
@limiter.limit("15/minute")
def create_group(request: Request, response: Response, body: GroupCreate,
                 user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.create_group(db, user, body)


@router.get("/groups/{group_id}")
def group(group_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.group_view(db, user, service.group_for(db, user, group_id))


@router.put("/groups/{group_id}")
@limiter.limit("30/minute")
def edit_group(request: Request, response: Response, group_id: UUID, body: GroupEdit,
               user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.edit_group(db, user, group_id, body)


@router.post("/groups/{group_id}/actions")
@limiter.limit("30/minute")
def archive(request: Request, response: Response, group_id: UUID, body: ArchiveGroup,
            user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.archive(db, user, group_id, body)


@router.get("/group-memberships")
def own_memberships(state: Literal["all", "invited", "requested", "active"] = "all", page: int = Page,
                    user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.own_memberships(db, user, state, page)


@router.get("/groups/{group_id}/members")
def members(group_id: UUID, state: Literal["active", "pending", "all"] = "active", page: int = Page,
            user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.members(db, user, group_id, state, page)


@router.post("/groups/{group_id}/membership/actions")
@limiter.limit("30/minute")
def membership_action(request: Request, response: Response, group_id: UUID, body: MembershipAction,
                      user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.own_membership_action(db, user, group_id, body)


@router.post("/groups/{group_id}/members/{user_code}/actions")
@limiter.limit("30/minute")
def manage_member(request: Request, response: Response, group_id: UUID, user_code: int, body: ManageMember,
                  user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.manage_member(db, user, group_id, user_code, body)


@router.get("/groups/{group_id}/invite-links")
def links(group_id: UUID, page: int = Page, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.links(db, user, group_id, page)


@router.post("/groups/{group_id}/invite-links", status_code=201)
@limiter.limit("15/minute")
def create_link(request: Request, response: Response, group_id: UUID, body: CreateInviteLink,
                user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.create_link(db, user, group_id, body)


@router.post("/groups/{group_id}/invite-links/{link_id}/revoke")
@limiter.limit("30/minute")
def revoke_link(request: Request, response: Response, group_id: UUID, link_id: UUID, body: RevokeInviteLink,
                user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.revoke_link(db, user, group_id, link_id, body)


@router.post("/group-invites/resolve")
@limiter.limit("30/minute")
def resolve_link(request: Request, response: Response, body: ResolveInvite,
                 user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.resolve_link(db, user, body)


@router.post("/group-invites/accept")
@limiter.limit("30/minute")
def accept_link(request: Request, response: Response, body: AcceptInvite,
                user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.accept_link(db, user, body)


@router.get("/groups/{group_id}/posts")
def posts(group_id: UUID, page: int = Page, author_visuals: bool = Query(default=False),
          user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.posts(db, user, group_id, page, author_visuals=author_visuals)


@router.post("/groups/{group_id}/posts", status_code=201)
@limiter.limit("30/minute")
def create_post(request: Request, response: Response, group_id: UUID, body: GroupPostCreate,
                author_visuals: bool = Query(default=False), user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.create_post(db, user, group_id, body, author_visuals=author_visuals)


@router.post("/group-posts/{post_id}/actions")
@limiter.limit("30/minute")
def moderate_post(request: Request, response: Response, post_id: UUID, body: ModeratePost,
                  author_visuals: bool = Query(default=False), user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.moderate_post(db, user, post_id, body, author_visuals=author_visuals)


@router.get("/group-posts/{post_id}/comments")
def comments(post_id: UUID, page: int = Page, author_visuals: bool = Query(default=False),
             user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.comments(db, user, post_id, page, author_visuals=author_visuals)


@router.post("/group-posts/{post_id}/comments", status_code=201)
@limiter.limit("30/minute")
def create_comment(request: Request, response: Response, post_id: UUID, body: GroupCommentCreate,
                   author_visuals: bool = Query(default=False), user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.create_comment(db, user, post_id, body, author_visuals=author_visuals)


@router.post("/group-comments/{comment_id}/actions")
@limiter.limit("30/minute")
def hide_comment(request: Request, response: Response, comment_id: UUID, body: HideComment,
                 author_visuals: bool = Query(default=False), user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.hide_comment(db, user, comment_id, body, author_visuals=author_visuals)


@router.post("/groups/{group_id}/media", status_code=201)
@limiter.limit("15/minute")
async def upload(request: Request, response: Response, group_id: UUID, submission_id: UUID = Form(...),
                 kind: Literal["group_image", "post"] = Form(...), file: UploadFile = File(...),
                 user=Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        data = await file.read(MAX_BYTES + 1)
        return await run_in_threadpool(service.upload_media, db, user, group_id, kind, submission_id, data)
    finally:
        await file.close()


@router.get("/group-media/{media_id}")
def media(media_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return ByteResponse(service.media_bytes(db, user, media_id), media_type="image/webp", headers=NO_STORE)


@router.get("/group-submissions/{submission_id}")
def submission(submission_id: UUID, author_visuals: bool = Query(default=False),
               user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.own_submission(db, user, submission_id, author_visuals=author_visuals)
