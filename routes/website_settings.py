"""Versioned website content. Platform/payment settings remain separate."""

from copy import deepcopy
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.routing import APIRoute
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from database import get_db
from models.activity_log import ActivityLog
from models.user import User
from models.website_settings import WebsiteSettings, WebsiteSettingsRevision
from schemas.website_settings import (
    MAX_SETTINGS_BODY_BYTES, RealtorTool, WebsiteContentSettings, WebsiteSettingsAdmin,
    WebsiteSettingsHistory, WebsiteSettingsPublic, WebsiteSettingsRestore, WebsiteSettingsWrite,
)
from utils.permission import require_admin, require_role


class ContentBodyLimitRoute(APIRoute):
    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def limited_handler(request: Request):
            if request.method in ("PUT", "POST"):
                declared = request.headers.get("content-length")
                if declared is not None:
                    try:
                        if int(declared) < 0:
                            raise ValueError
                        if int(declared) > MAX_SETTINGS_BODY_BYTES:
                            raise HTTPException(status_code=413, detail="Website settings body exceeds 256 KB")
                    except ValueError:
                        raise HTTPException(status_code=400, detail="Invalid Content-Length")
                if len(await request.body()) > MAX_SETTINGS_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="Website settings body exceeds 256 KB")
            return await original_handler(request)

        return limited_handler


router = APIRouter(tags=["website-content"], route_class=ContentBodyLimitRoute)


def _no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


def _snapshot(data):
    return WebsiteContentSettings.model_validate(data or {}).model_dump(mode="json", exclude_unset=True)


def _public_snapshot(data):
    snapshot = _snapshot(data)
    if "realtorTools" in snapshot:
        snapshot["realtorTools"] = [tool for tool in snapshot["realtorTools"] if tool.get("access", "realtor") == "public"]
    return snapshot


def _envelope(row, *, public=False):
    return {
        "version": row.version if row else 0,
        "settings": (_public_snapshot(row.data) if public else _snapshot(row.data)) if row else {},
    }


@router.get("/site/settings", response_model=WebsiteSettingsPublic, response_model_exclude_unset=True)
def get_public_website_settings(response: Response, db: Session = Depends(get_db)):
    _no_store(response)
    row = db.query(WebsiteSettings).filter(WebsiteSettings.id == 1).first()
    return _envelope(row, public=True)


@router.get("/site/realtor-tools", response_model=list[RealtorTool])
def get_realtor_tools(response: Response, user=Depends(require_role("admin", "realtor")), db: Session = Depends(get_db)):
    _no_store(response)
    row = db.query(WebsiteSettings).filter(WebsiteSettings.id == 1).first()
    return _snapshot(row.data).get("realtorTools", []) if row else []


@router.get("/admin/site-settings", response_model=WebsiteSettingsAdmin, response_model_exclude_unset=True)
def get_admin_website_settings(response: Response, user=Depends(require_admin), db: Session = Depends(get_db)):
    _no_store(response)
    row = db.query(WebsiteSettings).filter(WebsiteSettings.id == 1).first()
    return {**_envelope(row), "updated_at": row.updated_at if row else None}


def _publish(db: Session, settings: dict, expected_version: int, actor: User, *, restored_version: int | None = None):
    """Serialize concurrent first publishes and reject stale editor snapshots."""
    try:
        db.execute(insert(WebsiteSettings).values(id=1, version=0, data={}).on_conflict_do_nothing(index_elements=[WebsiteSettings.id]))
        current = db.query(WebsiteSettings).filter(WebsiteSettings.id == 1).populate_existing().with_for_update().one()
        if current.version != expected_version:
            raise HTTPException(status_code=409, detail={
                "message": "Website settings changed. Reload before saving.", "current_version": current.version,
            })
        # Preserve the initial built-in configuration as an explicit undo point.
        db.execute(insert(WebsiteSettingsRevision).values(
            version=current.version, snapshot=deepcopy(current.data), actor_id=current.updated_by,
            created_at=current.updated_at,
        ).on_conflict_do_nothing(index_elements=[WebsiteSettingsRevision.version]))
        current.version += 1
        current.data = deepcopy(settings)
        current.updated_by = actor.id
        current.updated_at = datetime.now(timezone.utc)
        db.add(WebsiteSettingsRevision(
            version=current.version, snapshot=deepcopy(settings), actor_id=actor.id, created_at=current.updated_at,
        ))
        description = f"Published website content version {current.version}"
        if restored_version is not None:
            description += f" from revision {restored_version}"
        db.add(ActivityLog(event_type="website_content_published", description=description, actor_id=actor.id))
        db.commit()
        return {**_envelope(current), "updated_at": current.updated_at}
    except Exception:
        db.rollback()
        raise


@router.put("/admin/site-settings", response_model=WebsiteSettingsAdmin, response_model_exclude_unset=True)
def put_website_settings(body: WebsiteSettingsWrite, response: Response, user=Depends(require_admin), db: Session = Depends(get_db)):
    _no_store(response)
    settings = body.settings.model_dump(mode="json", exclude_unset=True)
    return _publish(db, settings, body.expected_version, user)


@router.get("/admin/site-settings/history", response_model=WebsiteSettingsHistory)
def get_website_settings_history(
    response: Response,
    limit: int = Query(50, ge=1, le=100),
    before_version: int | None = Query(None, ge=1),
    user=Depends(require_admin), db: Session = Depends(get_db),
):
    _no_store(response)
    query = db.query(WebsiteSettingsRevision, User.display_name).outerjoin(User, User.id == WebsiteSettingsRevision.actor_id)
    if before_version is not None:
        query = query.filter(WebsiteSettingsRevision.version < before_version)
    rows = query.order_by(WebsiteSettingsRevision.version.desc()).limit(limit).all()
    return {"items": [{"version": row.version, "created_at": row.created_at, "updated_by_name": name} for row, name in rows]}


@router.get("/admin/site-settings/revisions/{version}", response_model=WebsiteSettingsAdmin, response_model_exclude_unset=True)
def get_website_settings_revision(version: int, response: Response, user=Depends(require_admin), db: Session = Depends(get_db)):
    _no_store(response)
    row = db.query(WebsiteSettingsRevision).filter(WebsiteSettingsRevision.version == version).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Website content revision not found")
    return {"version": row.version, "settings": _snapshot(row.snapshot), "updated_at": row.created_at}


@router.post("/admin/site-settings/restore/{version}", response_model=WebsiteSettingsAdmin, response_model_exclude_unset=True)
def restore_website_settings(version: int, body: WebsiteSettingsRestore, response: Response, user=Depends(require_admin), db: Session = Depends(get_db)):
    _no_store(response)
    row = db.query(WebsiteSettingsRevision).filter(WebsiteSettingsRevision.version == version).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Website content revision not found")
    return _publish(db, _snapshot(row.snapshot), body.expected_version, user, restored_version=version)
