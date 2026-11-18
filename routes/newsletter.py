from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, EmailStr, StringConstraints, field_validator
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from database import get_db
from services import newsletter
from utils.limiter import limiter
from utils.request_security import require_json_mutation


NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


class NewsletterRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                require_json_mutation(request)
                receive = request.receive
                size = 0

                async def bounded_receive():
                    nonlocal size
                    item = await receive()
                    size += len(item.get("body", b""))
                    if size > 2048:
                        raise HTTPException(413, "Newsletter request is too large")
                    return item

                request = Request(request.scope, receive=bounded_receive)
                await request.body()
                response = await original(request)
            except RequestValidationError:
                response = JSONResponse({"detail": "Check your newsletter request"}, 400)
            except StarletteHTTPException as error:
                response = JSONResponse({"detail": error.detail}, error.status_code, headers=error.headers)
            response.headers.update(NO_STORE)
            return response

        return handle


class SubscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: EmailStr
    language: Literal["en", "es"]
    consent: Literal[True]

    @field_validator("consent", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("Explicit consent is required")
        return value


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Annotated[str, StringConstraints(min_length=101, max_length=101,
        pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.[0-9a-f]{64}$")]


router = APIRouter(prefix="/newsletter", tags=["newsletter"], route_class=NewsletterRoute)
Database = Annotated[Session, Depends(get_db)]


@router.post("/subscribe", status_code=202)
@limiter.limit("8/minute")
def subscribe(request: Request, body: SubscribeRequest, db: Database):
    try:
        newsletter.subscribe(db, email=str(body.email), language=body.language)
    except newsletter.ConfirmationUnavailable:
        raise HTTPException(503, "Newsletter confirmation is temporarily unavailable") from None
    return {"status": "accepted"}


def finish(db, action, token):
    status = newsletter.finish(db, action=action, token=token)
    if status is None:
        raise HTTPException(400, "The newsletter link is invalid or has expired")
    return {"status": status}


@router.post("/confirm")
@limiter.limit("12/minute")
def confirm(request: Request, body: ActionRequest, db: Database):
    return finish(db, "confirm", body.token)


@router.post("/unsubscribe")
@limiter.limit("12/minute")
def unsubscribe(request: Request, body: ActionRequest, db: Database):
    return finish(db, "unsubscribe", body.token)
