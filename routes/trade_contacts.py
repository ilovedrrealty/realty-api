"""Admin-only directory of publicly sourced Dominican business contacts."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import settings
from database import get_db
from models.user import User
from schemas.trade_contacts import TradeContactImport, TradeContactImportResult, TradeContactList
from schemas.trade_contact_files import ContactFileMapping, ContactFileTable, ContactFilterOptions, ContactMappingPreview
from schemas.directory_maps import DirectoryMapSummary, LocationStatus
from utils.directory_geo import AREA_KEYS
from services import trade_contacts as directory
from services import trade_contact_files as files
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
                    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    expected = "multipart/form-data" if request.url.path.endswith("/preview-file") else "application/json"
                    if content_type != expected:
                        raise HTTPException(415, "Use the supported contact upload format")
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
    locality: Annotated[str, Query(max_length=160)] = "",
    subtype: Annotated[str, Query(max_length=160)] = "",
    service_area: Annotated[str, Query(max_length=320)] = "",
    verification_status: Literal["", "owner_source", "public_source", "official_registry"] = "",
    contact_channel: Literal["", "phone", "email", "website", "no_contact"] = "",
    location_status: LocationStatus = "all",
    map_area: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
):
    if map_area and map_area not in AREA_KEYS:
        raise HTTPException(422, "Choose a known map reference area")
    return directory.search(db, q=q.strip(), category=category.strip(), province=province.strip(),
                            locality=locality.strip(), verification_status=verification_status, contact_channel=contact_channel,
                            subtype=subtype.strip(), service_area=service_area.strip(),
                            location_status=location_status, map_area=map_area,
                            limit=limit, offset=offset)


@router.get("/options", response_model=ContactFilterOptions)
def filter_options(admin: Admin, db: Database):
    return directory.filter_options(db)


@router.get("/map", response_model=DirectoryMapSummary)
def contact_map(
    admin: Admin, db: Database, q: Annotated[str, Query(max_length=160)] = "",
    category: Annotated[str, Query(max_length=160)] = "",
    province: Annotated[str, Query(max_length=160)] = "",
    locality: Annotated[str, Query(max_length=160)] = "",
    subtype: Annotated[str, Query(max_length=160)] = "",
    service_area: Annotated[str, Query(max_length=320)] = "",
    verification_status: Literal["", "owner_source", "public_source", "official_registry"] = "",
    contact_channel: Literal["", "phone", "email", "website", "no_contact"] = "",
    location_status: LocationStatus = "all", map_area: Annotated[str, Query(max_length=100)] = "",
):
    if map_area and map_area not in AREA_KEYS:
        raise HTTPException(422, "Choose a known map reference area")
    return directory.directory_map(db, q=q.strip(), category=category.strip(), province=province.strip(),
        locality=locality.strip(), subtype=subtype.strip(), service_area=service_area.strip(),
        verification_status=verification_status, contact_channel=contact_channel,
        location_status=location_status, map_area=map_area)


@router.post("/preview-file", response_model=ContactFileTable)
async def preview_file(admin: Admin, file: UploadFile = File(...), sheet: str | None = Form(None),
                       delimiter: Literal["auto", "comma", "semicolon", "tab"] = Form("auto")):
    try:
        data = await file.read(files.FILE_LIMIT + 1)
        return await run_in_threadpool(files.read_file, data, file.filename, sheet, delimiter)
    except files.ContactFileError as error:
        raise HTTPException(413 if error.detail["code"] == "file_size" else 422, error.detail) from None
    finally:
        await file.close()


@router.post("/preview-mapping", response_model=ContactMappingPreview)
def preview_mapping(body: ContactFileMapping, admin: Admin, db: Database):
    try:
        return files.preview_mapping(db, body)
    except files.ContactFileError as error:
        raise HTTPException(422, error.detail) from None


@router.post("/import", response_model=TradeContactImportResult)
def import_contacts(batch: TradeContactImport, admin: Admin, db: Database):
    try:
        return directory.import_entries(db, batch)
    except directory.SourceNotesOverflow:
        raise HTTPException(422, "Existing source notes would exceed the 20-field limit. Review the batch again; nothing was imported.") from None
