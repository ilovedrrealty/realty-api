"""Use the real transaction API with a disposable database and in-memory object transport."""

import base64
import io
import json
import os
import re
import secrets
import sys
from pathlib import Path
from threading import Lock
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_admin_share_fixture as fixture


base_seed = fixture.migrate_and_seed
base_api = fixture.confined_api
synthetic_lead = None
synthetic_professionals = ()
IDENTIFIER = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"
ROOM_WRITE = {
    "POST": re.compile(rf"(?:invitations|tasks|milestones|members/{IDENTIFIER}/revoke|documents|documents/{IDENTIFIER}/versions/{IDENTIFIER}/review)"),
    "PATCH": re.compile(rf"tasks/{IDENTIFIER}"),
    "PUT": re.compile(rf"(?:milestones/{IDENTIFIER}|documents/{IDENTIFIER}/versions/{IDENTIFIER}/content)"),
}


def room_anchor(grant_id=None):
    from database import SessionLocal
    from models.transaction_room import TransactionRoom, TransactionRoomGrant

    with SessionLocal() as db:
        if grant_id is None:
            return db.query(TransactionRoom.id).filter(TransactionRoom.lead_id == synthetic_lead).scalar()
        return db.query(TransactionRoom.id).join(
            TransactionRoomGrant, TransactionRoomGrant.room_id == TransactionRoom.id,
        ).filter(TransactionRoom.lead_id == synthetic_lead, TransactionRoomGrant.id == grant_id,
                 TransactionRoomGrant.recipient_id.in_(synthetic_professionals)).scalar()


class OwnedSyntheticBucket:
    """Only the provider boundary is substituted. Product MIME, digest and ACL code still runs."""

    def __init__(self):
        self._payloads = {}
        self._lock = Lock()

    def _require_key(self, key):
        owned_room = room_anchor()
        fixture.require(owned_room is not None and re.fullmatch(
            rf"rooms/{owned_room.hex}/[a-f0-9]{{32}}/[a-f0-9]{{32}}", key,
        ), "Fixture document key outside the owned room")

    def upload(self, key, data, options):
        self._require_key(key)
        fixture.require(isinstance(data, bytes) and 0 < len(data) <= 16 * 1024
                        and options.get("x-upsert") == "false", "Exact immutable synthetic document required")
        with self._lock:
            fixture.require(key not in self._payloads and len(self._payloads) < 4, "Synthetic storage quota or duplicate")
            self._payloads[key] = data

    def download(self, key):
        self._require_key(key)
        with self._lock:
            return self._payloads[key]


def migrate_and_bind():
    from database import SessionLocal
    from models.lead import Lead
    from models.listing import Listing
    from models.transaction_room import TransactionRoom
    from models.user import User
    from PIL import Image
    from utils.security import hash_password
    from utils import transaction_document_storage as storage

    global synthetic_lead, synthetic_professionals
    data = base_seed()
    fixture.advance("seed_transaction_room")
    professional_account = {"email": "browser-closing-professional@example.com", "password": secrets.token_urlsafe(24)}
    other_account = {"email": "browser-closing-other@example.com", "password": secrets.token_urlsafe(24)}
    unassigned_account = {"email": "browser-unassigned-closing@example.com", "password": secrets.token_urlsafe(24)}
    with SessionLocal.begin() as db:
        source = db.get(Lead, UUID(data["shareLeadId"]))
        listing = db.get(Listing, source.property_id)
        fixture.require(source.name == "Browser Share Contact" and source.type == "property_inquiry"
                        and source.assigned_realtor_id is not None and source.from_user_id is not None
                        and listing.status == "active" and db.query(TransactionRoom.id).count() == 0,
                        "The owned active synthetic source lead is required")
        listing.transaction = "sale"
        professional = User(id=uuid4(), email=professional_account["email"],
                            password_hash=hash_password(professional_account["password"]), role="owner",
                            status="active", email_verified=True, display_name="Browser Closing Professional")
        other_professional = User(id=uuid4(), email=other_account["email"],
                                  password_hash=hash_password(other_account["password"]), role="owner",
                                  status="active", email_verified=True, display_name="Browser Other Closing Professional")
        unassigned = User(id=uuid4(), email=unassigned_account["email"],
                          password_hash=hash_password(unassigned_account["password"]), role="realtor",
                          status="active", email_verified=True, display_name="Browser Unassigned Realtor")
        db.add_all((professional, other_professional, unassigned))
        db.flush()
        data["transactionProfessionalCode"] = professional.user_code
        data["transactionOtherProfessionalCode"] = other_professional.user_code
        synthetic_lead, synthetic_professionals = source.id, (professional.id, other_professional.id)
    output = io.BytesIO()
    Image.new("RGB", (2, 2), (54, 88, 115)).save(output, format="PNG")
    data["transactionPng"] = base64.b64encode(output.getvalue()).decode("ascii")
    data["transactionProfessional"] = professional_account
    data["transactionOtherProfessional"] = other_account
    data["transactionUnassigned"] = unassigned_account
    owned_bucket = OwnedSyntheticBucket()
    storage._private_bucket = lambda: owned_bucket
    return data


def confined_transaction_api(api):
    from fastapi.concurrency import run_in_threadpool

    fixture.require(synthetic_lead is not None and len(set(synthetic_professionals)) == 2, "Owned synthetic users required")
    baseline = base_api(api)
    invitation = re.compile(rf"/transactions/invitations/({IDENTIFIER})/accept")
    create_path = f"/transactions/leads/{synthetic_lead}"

    async def serve(scope, receive, send):
        if scope["type"] != "http":
            return await baseline(scope, receive, send)
        method, path = scope["method"], scope["path"]
        if method == "POST" and path == create_path:
            return await api(scope, receive, send)
        accept = invitation.fullmatch(path) if method == "POST" else None
        if accept and await run_in_threadpool(room_anchor, UUID(accept[1])) is not None:
            return await api(scope, receive, send)
        action = ROOM_WRITE.get(method)
        if action is not None and path.startswith("/transactions/"):
            room_id = await run_in_threadpool(room_anchor)
            prefix = f"/transactions/{room_id}/"
            if room_id is not None and path.startswith(prefix) and action.fullmatch(path[len(prefix):]):
                return await api(scope, receive, send)
        return await baseline(scope, receive, send)

    return serve


def pin(name, size):
    value = os.environ.get(name, "")
    fixture.require(re.fullmatch(rf"[a-f0-9]{{{size}}}", value), "Exact transaction application pin required")
    return value


if __name__ == "__main__":
    try:
        fixture.APP_COMMIT = pin("ILDR_TRANSACTION_APP_COMMIT", 40)
        fixture.APP_TREE = pin("ILDR_TRANSACTION_APP_TREE", 40)
        fixture.MIGRATION_HEAD = pin("ILDR_TRANSACTION_MIGRATION", 12)
        fixture.migrate_and_seed = migrate_and_bind
        fixture.confined_api = confined_transaction_api
        fixture.run()
    except Exception as error:
        kind = type(error).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", kind):
            kind = "UnhandledError"
        sqlstate = getattr(getattr(error, "orig", None), "pgcode", None)
        if fixture.stage != "apply_alembic" or not isinstance(sqlstate, str) or not re.fullmatch(r"[0-9A-Z]{5}", sqlstate):
            sqlstate = None
        print("BROWSER_API_FIXTURE_FAILURE " + json.dumps({
            "stage": fixture.stage, "error_type": kind, "fixture_line": None, "sqlstate": sqlstate,
        }), flush=True)
        raise SystemExit(1) from None
