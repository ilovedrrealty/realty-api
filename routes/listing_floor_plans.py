"""Admin floorplan drafts and anonymous reads of the exact approved asset."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from database import get_db
from models.user import User
from schemas.listing_floor_plan import FloorPlanAsset, FloorPlanState, PublishFloorPlan, WithdrawFloorPlan
from services import listing_floor_plans as service
from utils.permission import require_admin
from utils.request_security import require_json_mutation, require_mutation_origin
from utils.transaction_document_storage import DOCUMENT_TYPES, MAX_DOCUMENT_BYTES, InvalidDocument, prepare_document


NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Vary": "Cookie, Authorization"}
ADMIN = "/admin/listings/{listing_id}/floor-plan"
PUBLIC = "/listings/{listing_id}/floor-plan"
EXTENSIONS = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"}
JSON_LIMIT = 8 * 1024


class FloorPlanRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                if request.method == "PUT":
                    require_mutation_origin(request)
                if request.method == "POST":
                    require_json_mutation(request)
                    receive, consumed = request.receive, 0

                    async def bounded_receive():
                        nonlocal consumed
                        message = await receive()
                        consumed += len(message.get("body", b""))
                        if consumed > JSON_LIMIT:
                            raise HTTPException(413, "Floor plan details are too large.")
                        return message

                    request = Request(request.scope, receive=bounded_receive)
                    await request.body()
                response = await original(request)
            except RequestValidationError:
                response = JSONResponse({"detail": "Check the floor plan details and confirm owner permission."},
                                        status_code=422)
            except InvalidDocument as exc:
                response = JSONResponse({"detail": str(exc)}, status_code=422)
            except StarletteHTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            except Exception:
                response = JSONResponse({"detail": "Floor plans are temporarily unavailable."}, status_code=503)
            response.headers.update(NO_STORE)
            return response

        return handler


router = APIRouter(route_class=FloorPlanRoute, tags=["listing-floor-plans"])
Admin = Annotated[User, Depends(require_admin)]
Database = Annotated[Session, Depends(get_db)]


def _file_response(data: bytes, mime: str):
    disposition = "attachment" if mime == "application/pdf" else "inline"
    return Response(data, media_type=mime, headers={
        "Content-Disposition": f'{disposition}; filename="floor-plan.{EXTENSIONS[mime]}"',
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    })


@router.get(ADMIN, response_model=FloorPlanState)
def admin_read(listing_id: UUID, admin: Admin, db: Database):
    return service.admin_read(db, listing_id)


@router.put(ADMIN + "/draft", response_model=FloorPlanState)
async def stage(listing_id: UUID, request: Request, admin: Admin, db: Database, replaces: UUID | None = None):
    mime = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if mime not in DOCUMENT_TYPES:
        raise HTTPException(415, "Upload a PDF, JPEG, or PNG floor plan.")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_DOCUMENT_BYTES:
            raise HTTPException(413, "The floor plan exceeds 6 MiB.")
        data.extend(chunk)
    payload = bytes(data)
    content = await run_in_threadpool(prepare_document, payload, mime)
    return await run_in_threadpool(service.stage, db, listing_id, admin.id, replaces, payload, content)


@router.get(ADMIN + "/draft/{asset_id}/content", response_class=Response)
def draft_content(listing_id: UUID, asset_id: UUID, admin: Admin, db: Database):
    return _file_response(*service.read_content(db, listing_id, asset_id, draft=True))


@router.post(ADMIN + "/publish", response_model=FloorPlanState)
def publish(listing_id: UUID, body: PublishFloorPlan, admin: Admin, db: Database):
    return service.publish(db, listing_id, admin.id, body)


@router.post(ADMIN + "/withdraw", response_model=FloorPlanState)
def withdraw(listing_id: UUID, body: WithdrawFloorPlan, admin: Admin, db: Database):
    return service.withdraw(db, listing_id, body)


@router.get(PUBLIC, response_model=FloorPlanAsset)
def public_read(listing_id: UUID, db: Database):
    return service.public_read(db, listing_id)


@router.get(PUBLIC + "/{asset_id}/content", response_class=Response)
def public_content(listing_id: UUID, asset_id: UUID, db: Database):
    return _file_response(*service.read_content(db, listing_id, asset_id))
