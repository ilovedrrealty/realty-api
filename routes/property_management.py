"""Private owner/assigned-agent operations. GETs never send messages or move money."""
from datetime import date
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Response, Query
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError
from database import get_db
from models.property_management import PropertyManagementRecord as Record, PropertyManagementRevision as Revision
from utils.auth import get_current_user
from utils.limiter import limiter
from utils.request_security import require_json_mutation
from schemas.property_management import ManagementWrite, ManagementUpdate, ApprovalAction, VersionAction, Kind
from services import property_management as service

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class PrivateManagementRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                require_json_mutation(request)
                consumed, receive = 0, request.receive

                async def bounded_receive():
                    nonlocal consumed
                    message = await receive()
                    consumed += len(message.get("body", b""))
                    if consumed > 64 * 1024:
                        raise HTTPException(413, "Management record exceeds the size limit")
                    return message

                request = Request(request.scope, receive=bounded_receive)
                await request.body()
                response = await original(request)
                response.headers.update(NO_STORE)
                return response
            except StarletteHTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": jsonable_encoder(exc.detail)}, headers={**(exc.headers or {}), **NO_STORE})
            except RequestValidationError:
                return JSONResponse(status_code=422, content={"detail": "Check the property, dates, amounts and management record fields"}, headers=NO_STORE)
            except SQLAlchemyError:
                # Do not echo driver exceptions: SQL parameters can include
                # private tenancy, vendor, financial or inspection records.
                return JSONResponse(status_code=503, content={"detail": "Management records are temporarily unavailable. Refresh before retrying a save."}, headers=NO_STORE)
        return handler


router = APIRouter(prefix="/property-management", tags=["property management"], route_class=PrivateManagementRoute)


@router.get("/properties")
def properties(q: str = Query(default="", max_length=160), page: int = Query(default=1, ge=1, le=10000),
               user=Depends(get_current_user), db: Session = Depends(get_db)):
    from models.listing import Listing
    query = service.listings_for(db, user)
    if q.strip():
        # Escape LIKE metacharacters; search is bounded and never changes scope.
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(or_(Listing.title.ilike("%" + term + "%", escape="\\"), Listing.location.ilike("%" + term + "%", escape="\\")))
    total = query.count()
    rows = query.order_by(Listing.title, Listing.id).offset((page - 1) * 50).limit(50).all()
    return {"items": [{"id": str(r.id), "title": r.title, "location": r.location, "status": r.status,
                       "can_approve": service.can_approve(user, r)} for r in rows], "total": total, "page": page, "page_size": 50}


@router.get("/properties/{listing_id}/statement")
def statement(listing_id: UUID, start: date | None = None, end: date | None = None,
              user=Depends(get_current_user), db: Session = Depends(get_db)):
    today = service.today_dr()
    return service.statement(db, user, listing_id, start or today.replace(day=1), end or today)


@router.get("/records")
def records(listing_id: UUID, kind: Kind | None = None, page: int = Query(default=1, ge=1, le=10000),
            user=Depends(get_current_user), db: Session = Depends(get_db)):
    service.property_for(db, user, listing_id)
    query = db.query(Record).filter(Record.listing_id == listing_id)
    if kind:
        query = query.filter(Record.kind == kind)
    total = query.count()
    rows = query.order_by(Record.updated_at.desc(), Record.id).offset((page - 1) * 50).limit(50).all()
    return {"items": [service.view(r) for r in rows], "total": total, "page": page, "page_size": 50}


@router.get("/records/{record_id}")
def record(record_id: UUID, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.view(service.record_for(db, user, record_id))


@router.get("/records/{record_id}/history")
def history(record_id: UUID, page: int = Query(default=1, ge=1, le=10000),
            user=Depends(get_current_user), db: Session = Depends(get_db)):
    service.record_for(db, user, record_id)
    query = db.query(Revision).filter(Revision.record_id == record_id)
    return {"items": [{"version": r.version, "action": r.action, "snapshot": r.snapshot, "created_at": r.created_at}
                      for r in query.order_by(Revision.version.desc()).offset((page - 1) * 50).limit(50)],
            "total": query.count(), "page": page, "page_size": 50}


@router.post("/records", status_code=201)
@limiter.limit("30/minute")
def create_record(request: Request, response: Response, body: ManagementWrite,
                  user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.create(db, user, body)


@router.put("/records/{record_id}")
@limiter.limit("60/minute")
def update_record(request: Request, response: Response, record_id: UUID, body: ManagementUpdate,
                  user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.update(db, user, record_id, body)


@router.post("/records/{record_id}/approval")
@limiter.limit("30/minute")
def approve_record(request: Request, response: Response, record_id: UUID, body: ApprovalAction,
                   user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.approve(db, user, record_id, body)


@router.post("/records/{record_id}/schedule-next")
@limiter.limit("30/minute")
def next_task(request: Request, response: Response, record_id: UUID, body: VersionAction,
              user=Depends(get_current_user), db: Session = Depends(get_db)):
    return service.schedule_next(db, user, record_id, body)
