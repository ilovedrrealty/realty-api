"""Admin-only directory of publicly sourced Dominican business contacts."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import settings
from database import get_db
from models.user import User
from schemas.trade_contacts import TradeContactImport, TradeContactImportResult, TradeContactList
from services import trade_contacts as directory
from utils.permission import require_admin


NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
MAX_IMPORT_BYTES = 2 * 1024 * 1024


class TradeContactRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                if request.method == "POST":
                    if request.headers.get("origin") not in settings.origins_list:
                        raise HTTPException(403, "A trusted Admin origin is required")
                    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                        raise HTTPException(415, "Send a JSON contact batch")
                    receive = request.receive
                    consumed = 0

                    async def bounded_receive():
                        nonlocal consumed
                        message = await receive()
                        consumed += len(message.get("body", b""))
                        if consumed > MAX_IMPORT_BYTES:
                            raise HTTPException(413, "Contact batch exceeds the size limit")
                        return message

                    request = Request(request.scope, receive=bounded_receive)
                    await request.body()
                response = await original(request)
                response.headers.update(NO_STORE)
                return response
            except RequestValidationError:
                return JSONResponse({"detail": "Check the contact fields and source evidence"}, 422, headers=NO_STORE)
            except StarletteHTTPException as exc:
                return JSONResponse({"detail": exc.detail}, exc.status_code, headers={**(exc.headers or {}), **NO_STORE})

        return handle


router = APIRouter(prefix="/admin/trade-contacts", tags=["admin-trade-contacts"], route_class=TradeContactRoute)
Admin = Annotated[User, Depends(require_admin)]
Database = Annotated[Session, Depends(get_db)]


@router.get("", response_model=TradeContactList)
def list_contacts(
    admin: Admin, db: Database, q: Annotated[str, Query(max_length=160)] = "",
    category: Annotated[str, Query(max_length=160)] = "",
    province: Annotated[str, Query(max_length=160)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
):
    return directory.search(db, q=q.strip(), category=category.strip(), province=province.strip(),
                            limit=limit, offset=offset)


@router.post("/import", response_model=TradeContactImportResult)
def import_contacts(batch: TradeContactImport, admin: Admin, db: Database):
    return directory.import_entries(db, batch)
