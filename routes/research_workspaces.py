"""Private property research, scoped to the verified account on every query."""
from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.research_workspace import ResearchWorkspace
from models.user import User
from schemas.research_workspace import ResearchData, SaveResearchData, ResearchSummary, ResearchDetail
from utils.auth import get_current_user
from utils.limiter import limiter

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class PrivateResearchRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                consumed = 0
                receive = request.receive

                async def bounded_receive():
                    nonlocal consumed
                    message = await receive()
                    consumed += len(message.get("body", b""))
                    if consumed > 32 * 1024:
                        raise HTTPException(413, "Research workspace exceeds the size limit")
                    return message

                request = Request(request.scope, receive=bounded_receive)
                await request.body()
                response = await original(request)
                response.headers.update(NO_STORE)
                return response
            except StarletteHTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": jsonable_encoder(exc.detail)}, headers={**(exc.headers or {}), **NO_STORE})
            except RequestValidationError:
                # Validation errors must not echo private notes or document URLs.
                return JSONResponse(status_code=422, content={"detail": "Check the workspace fields and document links"}, headers=NO_STORE)
        return handler


router = APIRouter(prefix="/research-workspaces", tags=["research"], route_class=PrivateResearchRoute)


def own(db, user, workspace_id, lock=False):
    query = db.query(ResearchWorkspace).filter(ResearchWorkspace.id == workspace_id, ResearchWorkspace.user_id == user.id)
    row = (query.with_for_update() if lock else query).first()
    if row is None:
        raise HTTPException(404, "Research workspace not found")
    return row


@router.get("", response_model=list[ResearchSummary])
def list_workspaces(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(ResearchWorkspace).filter(ResearchWorkspace.user_id == user.id).order_by(ResearchWorkspace.updated_at.desc(), ResearchWorkspace.id).limit(100).all()


@router.get("/{workspace_id}", response_model=ResearchDetail)
def get_workspace(workspace_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return own(db, user, workspace_id)


@router.post("", response_model=ResearchDetail, status_code=201)
@limiter.limit("20/minute")
def create_workspace(request: Request, response: Response, body: ResearchData, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Serialize creates per account so the bounded list cannot hide extra records.
    db.query(User).filter(User.id == user.id).with_for_update().one()
    if db.query(ResearchWorkspace).filter(ResearchWorkspace.user_id == user.id).count() >= 100:
        raise HTTPException(409, "This account has reached the 100 saved-workspace limit")
    row = ResearchWorkspace(user_id=user.id, title=body.title, data=body.model_dump(mode="json"))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/{workspace_id}", response_model=ResearchDetail)
@limiter.limit("30/minute")
def save_workspace(request: Request, response: Response, workspace_id: UUID, body: SaveResearchData, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = own(db, user, workspace_id, lock=True)
    if row.version != body.expected_version:
        raise HTTPException(409, "This workspace changed in another tab. Reload it before saving.")
    row.title = body.title
    row.data = body.model_dump(mode="json", exclude={"expected_version"})
    row.version += 1
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row
