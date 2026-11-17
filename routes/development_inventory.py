"""Admin-controlled development availability and its public listing projection."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from database import get_db
from schemas.development_inventory import AdminInventory, DraftChange, PublishedInventory, RevisionChange, RevisionSnapshot, VersionChange
from services import development_inventory as service
from utils.permission import require_admin
from utils.request_security import require_json_mutation


NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Vary": "Cookie, Authorization"}
MAX_BODY = 1024 * 1024


class InventoryRoute(APIRoute):
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
                            raise HTTPException(413, "Development inventory request is too large")
                        return message

                    request = Request(request.scope, receive=bounded_receive)
                    await request.body()
                response = await original(request)
            except RequestValidationError:
                response = JSONResponse({"detail": "Check the inventory fields, dates and floor plan links"}, status_code=422)
            except StarletteHTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            except Exception:
                response = JSONResponse({"detail": "Development inventory is temporarily unavailable"}, status_code=503)
            response.headers.update(NO_STORE)
            return response

        return handler


router = APIRouter(tags=["development-inventory"], route_class=InventoryRoute)


@router.get("/admin/developments/{listing_id}", response_model=AdminInventory)
def admin_read(listing_id: UUID, user=Depends(require_admin), db: Session = Depends(get_db)):
    return service.admin_read(db, listing_id)


@router.put("/admin/developments/{listing_id}/draft", response_model=AdminInventory)
def save_draft(listing_id: UUID, body: DraftChange, user=Depends(require_admin), db: Session = Depends(get_db)):
    return service.save_draft(db, user, listing_id, body)


@router.get("/admin/developments/{listing_id}/revisions/{revision}", response_model=RevisionSnapshot)
def preview(listing_id: UUID, revision: int = Path(ge=1, le=2147483647),
            user=Depends(require_admin), db: Session = Depends(get_db)):
    return service.preview(db, listing_id, revision)


@router.post("/admin/developments/{listing_id}/review", response_model=AdminInventory)
def review(listing_id: UUID, body: RevisionChange, user=Depends(require_admin), db: Session = Depends(get_db)):
    return service.review(db, user, listing_id, body)


@router.post("/admin/developments/{listing_id}/publish", response_model=AdminInventory)
def publish(listing_id: UUID, body: RevisionChange, user=Depends(require_admin), db: Session = Depends(get_db)):
    return service.publish(db, user, listing_id, body)


@router.post("/admin/developments/{listing_id}/withdraw", response_model=AdminInventory)
def withdraw(listing_id: UUID, body: VersionChange, user=Depends(require_admin), db: Session = Depends(get_db)):
    return service.withdraw(db, user, listing_id, body)


@router.get("/listings/{listing_id}/development", response_model=PublishedInventory)
def public_read(listing_id: UUID, db: Session = Depends(get_db)):
    return service.public_read(db, listing_id)
