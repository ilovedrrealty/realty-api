"""Private lifecycle for versioned transaction documents and authenticated downloads."""

from typing import Annotated
from urllib.parse import quote
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
from schemas.transaction_document import DocumentList, DocumentReceipt, ReserveDocument, ReviewDocument
from services import transaction_documents as service
from utils.auth import get_current_user
from utils.request_security import require_json_mutation, require_mutation_origin
from utils.transaction_document_storage import DOCUMENT_TYPES, InvalidDocument, prepare_document, store_document


NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Vary": "Cookie, Authorization"}
JSON_LIMIT = 8 * 1024
PATH = "/{document_id}/versions/{version_id}"


class TransactionDocumentRoute(APIRoute):
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
                            raise HTTPException(413, "Document details are too large.")
                        return message

                    request = Request(request.scope, receive=bounded_receive)
                    await request.body()
                response = await original(request)
            except RequestValidationError:
                response = JSONResponse({"detail": "Check the document details."}, status_code=422)
            except InvalidDocument as exc:
                response = JSONResponse({"detail": str(exc)}, status_code=422)
            except StarletteHTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            except Exception:
                response = JSONResponse({"detail": "Transaction documents are temporarily unavailable."}, status_code=503)
            response.headers.update(NO_STORE)
            return response

        return handler


router = APIRouter(prefix="/transactions/{room_id}/documents", tags=["transaction-documents"],
                   route_class=TransactionDocumentRoute)
Actor = Annotated[User, Depends(get_current_user)]
Database = Annotated[Session, Depends(get_db)]


@router.get("", response_model=DocumentList)
def documents(room_id: UUID, actor: Actor, db: Database):
    return service.list_documents(db, actor, room_id)


@router.post("", response_model=DocumentReceipt)
def reserve(room_id: UUID, body: ReserveDocument, actor: Actor, db: Database):
    return service.reserve(db, actor, room_id, body)


@router.put(PATH + "/content", response_model=DocumentReceipt)
async def upload(room_id: UUID, document_id: UUID, version_id: UUID, request: Request, actor: Actor, db: Database):
    mime = request.headers.get("content-type", "").strip().lower()
    if mime not in DOCUMENT_TYPES:
        raise HTTPException(415, "Upload a PDF, JPEG, or PNG document.")
    expected, receipt = await run_in_threadpool(service.pending_upload, db, actor, room_id, document_id, version_id)
    if mime != expected.content_type:
        raise HTTPException(415, "The upload type does not match the document reservation.")

    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > expected.byte_count:
            raise HTTPException(413, "The upload exceeds the reserved document size.")
        data.extend(chunk)
    if len(data) != expected.byte_count:
        raise HTTPException(422, "The upload size does not match the document reservation.")
    payload = bytes(data)
    content = await run_in_threadpool(prepare_document, payload, mime)
    if content != expected:
        raise HTTPException(409, "The upload does not match the reserved document bytes.")
    if receipt["status"] == "reserved":
        await run_in_threadpool(store_document, room_id, document_id, version_id, payload, expected)
    return await run_in_threadpool(service.complete_upload, db, actor, room_id, document_id, version_id, expected)


@router.get(PATH + "/download", response_class=Response)
def download(room_id: UUID, document_id: UUID, version_id: UUID, actor: Actor, db: Database):
    data, mime, file_name = service.download(db, actor, room_id, document_id, version_id)
    encoded = quote(file_name, safe="")
    return Response(data, media_type=mime, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
        "X-Content-Type-Options": "nosniff",
    })


@router.post(PATH + "/review", response_model=DocumentReceipt)
def review(room_id: UUID, document_id: UUID, version_id: UUID, body: ReviewDocument, actor: Actor, db: Database):
    return service.review(db, actor, room_id, document_id, version_id, body)
