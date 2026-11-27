"""Public assistant endpoints with independent customer contact confirmation.

Both feature gates default disabled. No model-selected write tool is registered.
"""
import asyncio
from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from config import settings
from schemas.public_assistant_http import (
    AssistantConfirmationResponse, AssistantReviewResponse, AssistantSessionResponse,
    AssistantTurnResponse,
)
from services import public_assistant_http as boundary
from utils.limiter import limiter


def response(value, status=200):
    return JSONResponse(value, status_code=status, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


class PublicAssistantRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def safe(request):
            try:
                return await original(request)
            except boundary.PublicHTTPError as error:
                return response({"error": error.code}, error.status)
            except HTTPException as error:
                return response({"error": "assistant_busy" if error.status_code == 429 else "assistant_request_refused"}, 429 if error.status_code == 429 else 403)
            except RequestValidationError:
                return response({"error": "invalid_assistant_request"}, 422)
            except Exception:
                # No exception/provider/body/DB text leaves this boundary.
                code = "submission_unknown" if request.url.path == "/public/assistant/contact-confirm" else "assistant_unavailable"
                return response({"error": code}, 503)
        return safe


router = APIRouter(prefix="/public/assistant", tags=["public-assistant"], route_class=PublicAssistantRoute)


async def body(request, maximum):
    if request.scope.get("query_string"):
        raise boundary.PublicHTTPError(422, "invalid_assistant_request")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise boundary.PublicHTTPError(415, "invalid_assistant_request")
    async def collect():
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > maximum:
                raise boundary.PublicHTTPError(413, "invalid_assistant_request")
            data.extend(chunk)
        return bytes(data)
    try:
        raw = await asyncio.wait_for(collect(), timeout=3)
    except asyncio.TimeoutError:
        raise boundary.PublicHTTPError(408, "invalid_assistant_request") from None
    return boundary.parse_object(raw, maximum)


def session_for(request):
    boundary.require_origin(request.headers.getlist("origin"))
    session = boundary.parse_session(request.cookies.get(boundary.COOKIE))
    boundary.require_csrf(session, request.headers.getlist("x-assistant-csrf"))
    return session


@router.post("/session")
@limiter.limit("30/minute")
async def session(request: Request):
    boundary.require_origin(request.headers.getlist("origin"))
    value = await body(request, 128)
    if value:
        raise boundary.PublicHTTPError(422, "invalid_assistant_request")
    current = boundary.issue_session(request.cookies.get(boundary.COOKIE))
    result = AssistantSessionResponse(csrf_token=boundary.csrf(current), session_expires_at=current.expires_at,
                                      model_enabled=boundary.model_enabled(), contact_enabled=boundary.contact_enabled())
    output = response(result.model_dump())
    secure = settings.environment == "production"
    output.set_cookie(boundary.COOKIE, current.token, max_age=max(0, current.expires_at - boundary._now()),
                      path=boundary.COOKIE_PATH, httponly=True, secure=secure, samesite="lax")
    return output


@router.post("/turn")
@limiter.limit("12/minute")
async def turn(request: Request):
    session_for(request)
    value = await body(request, 4096)
    result = await run_in_threadpool(boundary.call_in_worker, "model", boundary.public_turn, value)
    return response(AssistantTurnResponse.model_validate(result).model_dump(mode="json"))


@router.post("/contact-review")
@limiter.limit("6/minute")
async def contact_review(request: Request):
    current = session_for(request)
    value = await body(request, 8192)
    result = await run_in_threadpool(boundary.call_in_worker, "contact", boundary.contact_review, value, current)
    return response(AssistantReviewResponse.model_validate(result).model_dump(mode="json"))


@router.post("/contact-confirm")
@limiter.limit("6/minute")
async def contact_confirm(request: Request):
    current = session_for(request)
    value = await body(request, 16384)
    result = await run_in_threadpool(boundary.call_in_worker, "contact", boundary.contact_confirm, value, current)
    return response(AssistantConfirmationResponse.model_validate(result).model_dump(mode="json"))
