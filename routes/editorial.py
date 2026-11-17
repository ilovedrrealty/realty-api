"""Admin publishing workflow and the current public editorial projection."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from database import get_db
from schemas.editorial import (EditorialChange, EditorialCreate, EditorialDetail, EditorialList, EditorialRevisionChange,
                               EditorialWrite, Locale, PublicEditorialDetail, PublicEditorialList, PublicNeighborhoodDetail,
                               PublicNeighborhoodList, RevisionList, RevisionPreview, Slug)
from services import editorial as service
from utils.permission import require_admin
from utils.request_security import require_json_mutation


NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Vary": "Cookie, Authorization"}
MAX_BODY = 512 * 1024
Page = Query(default=1, ge=1, le=1000)
PageSize = Query(default=20, ge=1, le=50)


class EditorialRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                require_json_mutation(request)
                if request.method in {"POST", "PUT"}:
                    receive, consumed = request.receive, 0

                    async def bounded_receive():
                        nonlocal consumed
                        message = await receive()
                        consumed += len(message.get("body", b""))
                        if consumed > MAX_BODY:
                            raise HTTPException(413, "Editorial request is too large")
                        return message

                    request = Request(request.scope, receive=bounded_receive)
                    await request.body()
                response = await original(request)
            except RequestValidationError:
                response = JSONResponse({"detail": "Check the editorial fields and source links"}, status_code=422)
            except StarletteHTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            except Exception:
                response = JSONResponse({"detail": "Editorial content is temporarily unavailable"}, status_code=503)
            response.headers.update(NO_STORE)
            return response

        return handler


router = APIRouter(tags=["editorial"], route_class=EditorialRoute)


@router.get("/admin/editorial", response_model=EditorialList)
def admin_list(page: int = Page, page_size: int = PageSize, user=Depends(require_admin), db: Session = Depends(get_db)):
    return service.admin_list(db, page, page_size)


@router.post("/admin/editorial", response_model=EditorialDetail, status_code=201)
def create(body: EditorialCreate, user=Depends(require_admin), db: Session = Depends(get_db)):
    return service.create(db, user, body)


@router.get("/admin/editorial/{identifier}", response_model=EditorialDetail)
def read_admin(identifier: UUID, user=Depends(require_admin), db: Session = Depends(get_db)):
    return service.read_admin(db, identifier)


@router.put("/admin/editorial/{identifier}/draft", response_model=EditorialDetail)
def write_draft(identifier: UUID, body: EditorialWrite, user=Depends(require_admin), db: Session = Depends(get_db)):
    return service.write_draft(db, user, identifier, body)


@router.get("/admin/editorial/{identifier}/revisions", response_model=RevisionList)
def revisions(identifier: UUID, page: int = Page, page_size: int = PageSize,
              user=Depends(require_admin), db: Session = Depends(get_db)):
    return service.revisions(db, identifier, page, page_size)


@router.get("/admin/editorial/{identifier}/revisions/{revision}/preview", response_model=RevisionPreview)
def preview(identifier: UUID, revision: int = Path(ge=1, le=2147483647),
            user=Depends(require_admin), db: Session = Depends(get_db)):
    return service.preview(db, identifier, revision)


@router.post("/admin/editorial/{identifier}/review", response_model=EditorialDetail)
def review(identifier: UUID, body: EditorialRevisionChange, user=Depends(require_admin), db: Session = Depends(get_db)):
    return service.review(db, user, identifier, body)


@router.post("/admin/editorial/{identifier}/publish", response_model=EditorialDetail)
def publish(identifier: UUID, body: EditorialRevisionChange, user=Depends(require_admin), db: Session = Depends(get_db)):
    return service.publish(db, user, identifier, body)


@router.post("/admin/editorial/{identifier}/unpublish", response_model=EditorialDetail)
def unpublish(identifier: UUID, body: EditorialChange, user=Depends(require_admin), db: Session = Depends(get_db)):
    return service.unpublish(db, user, identifier, body)


@router.post("/admin/editorial/{identifier}/restore", response_model=EditorialDetail)
def restore(identifier: UUID, body: EditorialRevisionChange, user=Depends(require_admin), db: Session = Depends(get_db)):
    return service.restore(db, user, identifier, body)


@router.get("/site/editorial", response_model=PublicEditorialList)
def public_list(locale: Locale = "en", page: int = Page, page_size: int = PageSize, db: Session = Depends(get_db)):
    return service.public_list(db, locale, page, page_size)


@router.get("/site/editorial/{slug}", response_model=PublicEditorialDetail)
def public_detail(slug: Slug, locale: Locale = "en", db: Session = Depends(get_db)):
    return service.public_detail(db, slug, locale)


@router.get("/site/neighborhoods", response_model=PublicNeighborhoodList)
def public_neighborhood_list(locale: Locale = "en", page: int = Page, page_size: int = PageSize,
                             db: Session = Depends(get_db)):
    return service.public_neighborhood_list(db, locale, page, page_size)


@router.get("/site/neighborhoods/{slug}", response_model=PublicNeighborhoodDetail)
def public_neighborhood_detail(slug: Slug, locale: Locale = "en", db: Session = Depends(get_db)):
    return service.public_neighborhood_detail(db, slug, locale)
