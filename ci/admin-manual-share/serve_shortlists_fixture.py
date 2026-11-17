"""Seed an assigned buyer and Realtor in the confined browser fixture."""

import json
import os
import re
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_admin_share_fixture as fixture


seed_base = fixture.migrate_and_seed
base_confined_api = fixture.confined_api
UUID = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"
MUTATIONS = {
    "POST": re.compile(rf"/shortlists/owned(?:/{UUID}/(?:share|archive|shares/{UUID}/revoke))?"),
    "PUT": re.compile(rf"/shortlists/(?:owned|received)/{UUID}"),
}


def seed_shortlists():
    from database import SessionLocal
    from models.listing import Listing
    from models.research_workspace import ResearchWorkspace
    from models.saved_home import SavedHome
    from models.user import User

    initial = seed_base()
    fixture.advance("seed_shortlists")
    with SessionLocal.begin() as db:
        buyer = db.query(User).filter(User.email == initial["accounts"]["buyer"]["email"], User.role == "buyer").one()
        agent = db.query(User).filter(User.email == initial["accounts"]["realtor"]["email"], User.role == "realtor").one()
        fixture.require(isinstance(agent.user_code, int) and agent.user_code > 0, "Expected a synthetic Realtor account code")
        buyer.assigned_realtor_id = agent.id
        visible = db.query(Listing).filter(Listing.title == "Browser rehearsal villa", Listing.status == "active").one()
        second = Listing(id=uuid4(), title="Second synthetic shortlist home", type="villa", transaction="rent",
                         price=200, location="Cabarete", status="active", submitted_by=agent.id, images=[])
        hidden = Listing(id=uuid4(), title="Synthetic archived shortlist property", type="villa", transaction="sale",
                         price=300, location="Cabarete", status="archived", submitted_by=agent.id, images=[])
        db.add_all((second, hidden))
        db.flush()
        db.add_all((
            SavedHome(user_id=buyer.id, listing_id=visible.id), SavedHome(user_id=buyer.id, listing_id=hidden.id),
            SavedHome(user_id=agent.id, listing_id=second.id),
            ResearchWorkspace(user_id=buyer.id, title="Private synthetic research",
                              data={"notes": "PRIVATE_SYNTHETIC_RESEARCH_SENTINEL"}),
        ))
        return {
            "accounts": initial["accounts"], "realtorCode": agent.user_code,
            "activeListing": str(visible.id), "secondListing": str(second.id), "inactiveListing": str(hidden.id),
        }


def confined_shortlist_api(api):
    baseline = base_confined_api(api)

    async def serve(scope, receive, send):
        if scope["type"] == "http":
            rule = MUTATIONS.get(scope["method"])
            if rule is not None and rule.fullmatch(scope["path"]):
                return await api(scope, receive, send)
        return await baseline(scope, receive, send)

    return serve


def pin(name, size):
    value = os.environ.get(name, "")
    fixture.require(re.fullmatch(rf"[a-f0-9]{{{size}}}", value), "Exact shortlist application pin required")
    return value


if __name__ == "__main__":
    try:
        fixture.APP_COMMIT = pin("ILDR_SHORTLIST_APP_COMMIT", 40)
        fixture.APP_TREE = pin("ILDR_SHORTLIST_APP_TREE", 40)
        fixture.MIGRATION_HEAD = pin("ILDR_SHORTLIST_MIGRATION", 12)
        fixture.migrate_and_seed = seed_shortlists
        fixture.confined_api = confined_shortlist_api
        fixture.run()
    except Exception as error:
        kind = type(error).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", kind):
            kind = "UnhandledError"
        print("BROWSER_API_FIXTURE_FAILURE " + json.dumps({
            "stage": fixture.stage, "error_type": kind, "fixture_line": None,
        }), flush=True)
        raise SystemExit(1) from None
