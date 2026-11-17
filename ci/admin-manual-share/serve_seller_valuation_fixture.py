"""Limit seller valuation writes to one seeded historical lead on the owned database."""

import json
import re
import secrets
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_development_inventory_fixture as development


fixture = development.fixture
synthetic_lead = None
UUID = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"


def migrate_and_bind():
    from database import SessionLocal
    from models.lead import Lead
    from models.seller_valuation import SellerValuationIntake
    from models.user import User
    from utils.security import hash_password

    global synthetic_lead
    data = development.migrate_and_bind()
    fixture.advance("seed_valuation")
    credentials = {"email": "browser-valuation-unrelated@example.com", "password": secrets.token_urlsafe(24)}
    with SessionLocal.begin() as db:
        assigned = db.query(User).filter(User.email == data["accounts"]["realtor"]["email"], User.role == "realtor").one()
        buyer = db.query(User).filter(User.email == data["accounts"]["buyer"]["email"], User.role == "buyer").one()
        unrelated = User(id=uuid4(), email=credentials["email"], password_hash=hash_password(credentials["password"]),
                         role="realtor", status="active", display_name="Unrelated Synthetic Realtor", email_verified=True)
        lead = Lead(id=uuid4(), type="seller_interest", name="Browser Seller Valuation",
                    email="browser-valuation-lead@example.com", phone="(809) 555-0195",
                    message="Historical synthetic seller request", status="assigned",
                    assigned_realtor_id=assigned.id, from_user_id=buyer.id)
        db.add_all((unrelated, lead))
        db.flush()
        fixture.require(db.get(SellerValuationIntake, lead.id) is None, "Historical synthetic seller without intake required")
        synthetic_lead = str(lead.id)
    data["valuationLeadId"] = synthetic_lead
    data["unrelatedValuationAccount"] = credentials
    return data


def confined_valuation_api(api):
    fixture.require(synthetic_lead is not None and re.fullmatch(UUID, synthetic_lead), "Owned seller lead required")
    baseline = development.confined_development_api(api)
    lead = re.escape(f"/staff/leads/{synthetic_lead}/valuation")
    writes = {
        "PUT": re.compile(rf"{lead}/(?:draft|comparables/{UUID}/sold-review)"),
        "POST": re.compile(rf"{lead}/(?:comparables/(?:asking|sold)|review|follow-ups(?:/{UUID}/complete)?)"),
    }

    async def serve(scope, receive, send):
        if scope["type"] == "http":
            allowed = writes.get(scope["method"])
            if allowed is not None and allowed.fullmatch(scope["path"]):
                return await api(scope, receive, send)
        return await baseline(scope, receive, send)

    return serve


if __name__ == "__main__":
    try:
        fixture.APP_COMMIT = development.editorial.pin("ILDR_VALUATION_APP_COMMIT", 40)
        fixture.APP_TREE = development.editorial.pin("ILDR_VALUATION_APP_TREE", 40)
        fixture.MIGRATION_HEAD = development.editorial.pin("ILDR_VALUATION_MIGRATION", 12)
        fixture.migrate_and_seed = migrate_and_bind
        fixture.confined_api = confined_valuation_api
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
