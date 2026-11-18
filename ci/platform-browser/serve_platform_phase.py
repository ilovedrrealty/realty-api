"""Bind reviewed synthetic journeys to one exact isolated application checkout."""

import importlib
import json
import os
from pathlib import Path
import re
import sys
import traceback
from urllib import request as urllib_request
from unittest.mock import patch

COMMIT = "796f25ff52724db56313916e129df4b252a682fd"
TREE = "10d76a3de555b567fde93a74b5f90b8d9ba76978"
HEAD = "b19d8e4c720a"
PHASES = {
    "owner": "serve_owner_project_fixture",
    "members": "serve_member_community_fixture",
    "negotiation": "serve_service_negotiation_fixture",
    "visits": "serve_service_visit_fixture",
    "review": "serve_service_review_fixture",
    "history": "serve_history_share_fixture",
    "custody": "serve_private_custody_fixture",
    "conversation": "serve_service_conversation_fixture",
    "core": "serve_communications_fixture",
}


def run():
    phase = os.environ.get("ILDR_PLATFORM_BROWSER_PHASE")
    if phase not in PHASES or len(sys.argv) != 1:
        raise RuntimeError("Exact owned phase required")
    root = Path(__file__).resolve().parent
    sys.path[:0] = [str(root), str(root / "accepted"), str(root / "accepted-core")]
    module = importlib.import_module(PHASES[phase])
    # Importing accepted fixtures performs no DB/application imports. Their
    # reusable checkout verifier still checks the exact read-only Git tree.
    for name, value in tuple(sys.modules.items()):
        if name.startswith("serve_") and name.endswith("_fixture"):
            for key, pin in (("APP_COMMIT", COMMIT), ("APP_TREE", TREE),
                             ("MIGRATION_HEAD", HEAD), ("MIGRATION", HEAD)):
                if hasattr(value, key):
                    setattr(value, key, pin)
    for prefix in ("REVIEW", "CONVERSATION", "NEGOTIATION", "HISTORY_SHARE", "PRIVATE_CUSTODY"):
        os.environ.update({f"ILDR_{prefix}_APP_COMMIT": COMMIT,
                           f"ILDR_{prefix}_APP_TREE": TREE,
                           f"ILDR_{prefix}_MIGRATION_HEAD": HEAD})
    if phase == "core":
        fixture = module.fixture
        fixture.APP_COMMIT, fixture.APP_TREE, fixture.MIGRATION_HEAD = COMMIT, TREE, HEAD
        fixture.API_PORT = 8000
        fixture.configure_application = module.configure
        fixture.migrate_and_seed = module.migrate_and_seed
        fixture.confined_api = module.confined_api
        import httpx
        import requests
        attempts = []

        def refuse(*_args, **_kwargs):
            attempts.append(True)
            raise RuntimeError("External transport unavailable")

        with (patch.object(httpx.HTTPTransport, "handle_request", refuse),
              patch.object(httpx.AsyncHTTPTransport, "handle_async_request", refuse),
              patch.object(requests.Session, "send", refuse),
              patch.object(urllib_request, "urlopen", refuse)):
            fixture.run()
        if attempts:
            raise RuntimeError("External core transport was attempted")
        if len(module.CALLS) != 3:
            raise RuntimeError("Exact synthetic SDK calls required")
        from database import SessionLocal
        from sqlalchemy import text
        with SessionLocal() as db:
            facts = db.execute(text("""SELECT json_build_object(
                'users',(SELECT count(*) FROM users),
                'active_users',(SELECT count(*) FROM users WHERE status='active'),
                'pending_users',(SELECT count(*) FROM pending_users),
                'leads',(SELECT count(*) FROM leads),
                'listings',(SELECT count(*) FROM listings),
                'legacy_inquiries',(SELECT count(*) FROM inquiries),
                'delivery_jobs',(SELECT count(*) FROM lead_delivery_jobs),
                'customer_jobs',(SELECT count(*) FROM lead_delivery_jobs WHERE step='customer_email'),
                'uncertain_jobs',(SELECT count(*) FROM lead_delivery_jobs WHERE status='needs_review' AND outcome_uncertain),
                'authorized_retries',(SELECT count(*) FROM lead_delivery_jobs WHERE error_code LIKE 'manual_retry_authorized:%'),
                'historical_jobs',(SELECT count(*) FROM lead_delivery_jobs j JOIN leads l ON l.id=j.lead_id
                    WHERE l.name IN ('Browser Share Contact','Foreign private contact sentinel')),
                'worker_records',(SELECT count(*) FROM lead_delivery_worker_state))""")).scalar_one()
        if facts != {"users": 6, "active_users": 6, "pending_users": 0, "leads": 12, "listings": 2,
                     "legacy_inquiries": 1, "delivery_jobs": 91, "customer_jobs": 11, "uncertain_jobs": 1,
                     "authorized_retries": 2, "historical_jobs": 0, "worker_records": 0}:
            raise RuntimeError("Exact core database parity required")
        print("COMMUNICATIONS_BROWSER_DATABASE " + json.dumps(facts))
        print("COMMUNICATIONS_BROWSER_PROVIDER_COUNTS " + json.dumps({"fake_sdk_calls": 3, "live_calls": 0}))
    else:
        module.run()
    if Path("/browser-fixtures/fixtures.json").exists():
        raise RuntimeError("Private fixture cleanup required")
    print("PLATFORM_PHASE_API_RESULT " + json.dumps({"phase": phase, "status": "PASS",
        "source": COMMIT, "tree": TREE, "head": HEAD, "private_file_absent": True,
        "live_provider_calls": 0}), flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        phase = os.environ.get("ILDR_PLATFORM_BROWSER_PHASE", "unknown")
        frames = [frame.lineno for frame in traceback.extract_tb(error.__traceback__)
                  if Path(frame.filename).name.startswith("serve_")]
        stages = [getattr(value, "stage") for name, value in tuple(sys.modules.items())
                  if name.startswith("serve_") and isinstance(getattr(value, "stage", None), str)]
        stage = stages[-1] if stages else "initialize"
        value = {"phase": phase if phase in PHASES else "unknown",
                 "stage": stage if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", stage) else "unknown",
                 "error_type": type(error).__name__ if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", type(error).__name__) else "OtherError",
                 "line": frames[-1] if frames else None}
        print("PLATFORM_PHASE_API_FAILURE " + json.dumps(value), flush=True)
        raise SystemExit(1) from None
