"""Owned source suites and real populated108 source-switch proof; never live data."""
import ast
import asyncio
from contextlib import ExitStack, closing, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import UUID

TOOLS = Path(__file__).resolve().parent
PLAN = json.loads((TOOLS / "PLAN.json").read_bytes())
SUITES = {"professionals", "catalog", "fallback"}
INTERNAL = {"seed106", "seed108", "paused_start", "active_start"}
PHASES = {"source", "claim", "inventory", "unit_suite", "unit_cleanup", "previous99", "baseline106",
          "seed106", "migrate108", "seed108", "paused_start", "active_start", "preservation"}
STAGE = "source"
IDS = {name: UUID("90000000-0000-4000-8000-" + str(n).zfill(12)) for n, name in enumerate(
    ("admin", "agent", "buyer", "signup", "lead", "own_profile", "managed_profile"), 1)}
FACETS = {"provider_kind_ids": ["cooperative"], "property_class_ids": ["hotel_hospitality"], "work_scope_ids": ["financing"]}
STATUSES = ("pending", "processing", "accepted", "failed", "needs_review", "skipped")
ERRORS = {"Failure", "AssertionError", "RuntimeError", "ValueError", "TypeError", "KeyError", "TimeoutExpired",
          "OperationalError", "ProgrammingError", "IntegrityError", "other"}


class Failure(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Failure(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def emit(kind, value):
    print("DIRECTORY_NATIVE_" + kind + " " + canonical(value), flush=True)


def load_legacy():
    for relative, digest in PLAN["helper_pins"].items():
        path = TOOLS.parent / relative.removeprefix("ci/")
        require(relative.startswith("ci/") and path.is_file() and not path.is_symlink()
                and os.statvfs(path).f_flag & os.ST_RDONLY and sha256(path.read_bytes()).hexdigest() == digest,
                "exact_readonly_helper_required")
    path = TOOLS.parent / "assignment-notifications-native/verify.py"
    spec = spec_from_file_location("directory_accepted_native_helpers", path)
    legacy = module_from_spec(spec); spec.loader.exec_module(legacy)
    legacy.inventory()
    return legacy


def configure(legacy, label):
    pin = PLAN["paused_application"] if label in {"fallback", "paused_start"} else PLAN["baseline106"] if label == "seed106" else PLAN["application"]
    require(all(type(pin.get(k)) is str and re.fullmatch("[a-f0-9]{40}", pin[k]) for k in ("commit", "tree")),
            "final_root_source_pins_required")
    native, base = legacy.helpers(pin["commit"], pin["tree"])
    os.environ["ILDR_BROWSER_APPLICATION_ROOT"] = "/paused" if label in {"fallback", "paused_start"} else "/baseline106" if label == "seed106" else "/application"
    application = base.verified_checkout()
    return native, base, application


def claim(legacy, base, mode, *, empty):
    global STAGE
    STAGE = "claim"
    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    require(re.fullmatch("[a-f0-9]{32}", nonce), "owned_nonce_required")
    name = "ildr_platform_test_20261025" if mode in SUITES else "ildr_directory_ci_" + nonce
    marker = "ildr-assignment-fallback-unit-ci:" if mode == "fallback" else "ildr-directory-unit-ci:" if mode in SUITES else "ildr-directory-canonical-ci:"
    catalog, _ = base.frozen_helpers()
    row = catalog.query(name, """SELECT current_database(),host(inet_server_addr()),inet_server_port(),
        current_setting('server_version_num')::int,shobj_description(oid,'pg_database'),
        NOT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),
        NOT EXISTS(SELECT FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND nspname NOT LIKE 'pg_%%')
        FROM pg_database WHERE datname=current_database()""")
    require(len(row) == 1 and row[0][:5] == (name, "127.0.0.1", 55449, 170006, marker + nonce)
            and (not empty or row[0][5:] == (True, True)), "marked_database_scope_required")
    return catalog, name, "postgresql+psycopg2://postgres@127.0.0.1:55449/" + name


def child(mode, application, url):
    require(mode in INTERNAL, "fixed_internal_mode_required")
    result = subprocess.run(["/usr/local/bin/python", "-I", "-B", str(TOOLS / "verify.py"), mode],
        cwd=application, env=dict(os.environ, DATABASE_URL=url), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=180)
    require(len(result.stdout) <= 4096, "bounded_internal_child_output_required")
    if result.returncode != 0:
        prefix = b"DIRECTORY_NATIVE_FAILURE "
        raw = result.stdout.strip()
        reasons = {n.value for n in ast.walk(ast.parse(Path(__file__).read_bytes())) if isinstance(n, ast.Constant)
                   and isinstance(n.value, str) and re.fullmatch("[a-z][a-z0-9_]{1,110}_required", n.value)} | {"unexpected_exception"}
        def pairs(items):
            value = {}
            for key, item in items:
                require(key not in value, "safe_internal_failure_required")
                value[key] = item
            return value
        require(raw.startswith(prefix) and b"\n" not in raw, "safe_internal_failure_required")
        value = json.loads(raw[len(prefix):], object_pairs_hook=pairs)
        require(type(value) is dict and set(value) == {"stage", "error_type", "reason", "sqlstate"}
                and value["stage"] in PHASES and value["error_type"] in ERRORS and value["reason"] in reasons
                and (value["sqlstate"] is None or type(value["sqlstate"]) is str and re.fullmatch("[A-Z0-9]{5}", value["sqlstate"])),
                "safe_internal_failure_required")
        emit("FAILURE", value)
        raise SystemExit(1)
    expected = {"mode": mode, "status": "PASS", "external_provider_attempts": 0}
    require(result.stdout == ("DIRECTORY_NATIVE_INTERNAL " + canonical(expected) + "\n").encode(),
            "exact_internal_safe_result_required")


def historical_draft(**changes):
    return {"business_name": "Owned synthetic business", "contact_name": "Synthetic contact", "category_ids": ["property_law"],
        "custom_services": [], "service_area_ids": ["sosua"], "descriptions": {"en": "Synthetic declared business", "es": ""},
        "languages": ["en", "es"], "public_phone": "+18095550101", "public_email": "public@example.com", "public_whatsapp": "",
        "website_url": "", "photo_url": "", "google_profile_url": "", "google_maps_embed_url": "", "public_contact_consent": True,
        **FACETS, **changes}


def seed106(native, base, catalog, database):
    """Only baseline106 imports; future JSON declarations need no schema change."""
    from database import SessionLocal
    from models.user import User
    from models.lead import Lead
    from models.lead_delivery import LeadDeliveryJob
    from models.professional_profile import ProfessionalProfile, ProfessionalProfileRevision
    _, retained = base.frozen_helpers()
    captured = StringIO()
    with redirect_stdout(captured), redirect_stderr(captured):
        counts = retained.seed_old()
    require(counts == native.EXPECTED_SEED and len(captured.getvalue().encode()) <= 2 * 1024 * 1024,
            "actual_original_retained_seed_required")
    now = datetime.now(timezone.utc)
    with SessionLocal.begin() as db:
        for name, role in (("admin", "admin"), ("agent", "realtor"), ("buyer", "buyer"), ("signup", "buyer")):
            db.add(User(id=IDS[name], user_code=980000 + list(IDS).index(name), display_name="Synthetic " + name,
                        email=name + "-directory-native@example.com", role=role, status="active", email_verified=True))
        db.flush()
        db.add(Lead(id=IDS["lead"], type="buyer_interest", name="Synthetic retained inquiry", email="buyer@example.com",
                    message="PRIVATE_DIRECTORY_LEAD", from_user_id=IDS["buyer"], assigned_realtor_id=IDS["agent"],
                    status="assigned", assigned_at=now))
        for n, step in enumerate(["email_" + str(i) for i in range(1, 11)] + ["customer_email"], 1):
            payload = {"private": "PRIVATE_DIRECTORY_SIGNUP"}
            db.add(LeadDeliveryJob(id=UUID(int=980000 + n), user_id=IDS["signup"], step=step, provider="resend",
                payload=payload, payload_sha256=sha256(canonical(payload).encode()).hexdigest(), status="pending", attempt_count=0,
                safe_to_retry=False, outcome_uncertain=False, next_attempt_at=now, created_at=now, updated_at=now))
        for name, user_id, draft, published in (
            ("own_profile", IDS["buyer"], historical_draft(provider_kind_ids=["contractor"], property_class_ids=["house_villa"], work_scope_ids=["repair"]), None),
            ("managed_profile", None, historical_draft(provider_kind_ids=["financial_institution"], property_class_ids=["farm_rural"], work_scope_ids=["advisory"]), historical_draft())):
            db.add(ProfessionalProfile(id=IDS[name], user_id=user_id, version=1, draft_data=draft, published_data=published,
                review_status="approved" if published else "draft", listing_status="published" if published else "hidden",
                published_at=now if published else None, created_at=now, updated_at=now))
            db.flush()
            db.add(ProfessionalProfileRevision(id=UUID(int=981000 + list(IDS).index(name)), profile_id=IDS[name], version=1,
                action="save", snapshot=draft, actor_id=IDS["admin"], created_at=now))
    require(catalog.query(database, "SELECT count(*) FROM lead_delivery_jobs WHERE user_id=%s", (str(IDS["signup"]),)) == [(11,)],
            "actual_eleven_signup_rows_required")


def seed108():
    """Explicit historical fixtures, not producer or provider acceptance proof."""
    from database import SessionLocal
    from models.assignment_notification import AssignmentEvent, AssignmentEmailJob
    now = datetime.now(timezone.utc)
    payload = {"recipient_email": "agent-directory-native@example.com", "body": {"from": "rehearsal@example.com", "to": ["agent-directory-native@example.com"],
                "subject": "PRIVATE_DIRECTORY_SUBJECT", "html": "PRIVATE_DIRECTORY_BODY"}}
    with SessionLocal.begin() as db:
        for n, status in enumerate(STATUSES, 1):
            request = UUID(int=982000 + n)
            event = AssignmentEvent(id=UUID(int=983000 + n), source_kind="lead", lead_id=IDS["lead"], actor_id=IDS["admin"],
                request_id=request, request_sha256=sha256(canonical({"source_kind": "lead", "source_id": str(IDS["lead"]),
                    "agent_id": str(IDS["agent"])}).encode()).hexdigest(), agent_id=IDS["agent"], previous_agent_id=IDS["agent"],
                cycle=1, action="assigned", created_at=now)
            db.add(event); db.flush()
            attempted = status in {"processing", "accepted", "failed", "needs_review"}
            db.add(AssignmentEmailJob(id=UUID(int=984000 + n), event_id=event.id, kind="lead_assigned_agent", recipient_user_id=IDS["agent"],
                payload=payload, payload_sha256=sha256(canonical(payload).encode()).hexdigest(), status=status, attempt_count=int(attempted),
                attempted_at=now if attempted else None, accepted_at=now if status == "accepted" else None,
                outcome_uncertain=status == "needs_review", lease_token=UUID(int=985000 + n) if status == "processing" else None,
                lease_until=now - timedelta(minutes=5) if status == "processing" else None,
                provider_id="synthetic-historical-acceptance" if status == "accepted" else None, created_at=now, updated_at=now))
        for n, agent_id in enumerate((IDS["agent"], None), 1):
            db.add(AssignmentEvent(id=UUID(int=986000 + n), source_kind="account", user_id=IDS["buyer"], actor_id=IDS["admin"],
                request_id=UUID(int=987000 + n), request_sha256=sha256(canonical({"source_kind": "account", "source_id": str(IDS["buyer"]),
                    "agent_id": str(agent_id) if agent_id else None}).encode()).hexdigest(), agent_id=agent_id,
                previous_agent_id=IDS["agent"] if agent_id is None else None, cycle=n, action="assigned" if agent_id else "cleared", created_at=now))


def source_start(mode, legacy):
    from database import Base
    import bootstrap_db
    from alembic import command
    from alembic.config import Config
    # A populated, genuinely migrated database must never use create_all.
    with patch.object(Base.metadata, "create_all", side_effect=Failure("no_model_create_during_startup_required")) as create:
        bootstrap_db.main(); command.upgrade(Config("alembic.ini"), "head"); create.assert_not_called()
    import jwt
    from fastapi.testclient import TestClient
    from config import settings
    import main
    from services import lead_delivery, assignment_notification
    require(len(Base.metadata.tables) == 107 and settings.lead_delivery_enabled is False
            and settings.customer_messages_enabled is True and not lead_delivery.enabled() and not lead_delivery.signup_enabled(),
            "real_startup_synthetic_delivery_policy_required")
    names = [(main._cleanup_pending_users.__code__, "cleanup"), (main._payout_cron.__code__, "payout"),
             (lead_delivery.worker_loop.__code__, "delivery"), (assignment_notification.worker_loop.__code__, "assignment")]
    observed, tasks = [], []
    original_create = asyncio.create_task
    def observe(coroutine, *args, **kwargs):
        task = original_create(coroutine, *args, **kwargs)
        label = next((label for code, label in names if getattr(coroutine, "cr_code", None) is code), None)
        if label is not None:
            observed.append(label); tasks.append(task)
        return task
    token = lambda name: {"Authorization": "Bearer " + jwt.encode({"sub": str(IDS[name]),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=10)}, settings.jwt_secret, algorithm="HS256")}
    admin, buyer, agent = token("admin"), token("buyer"), token("agent")
    from routes import leads
    with ExitStack() as probes, patch.object(asyncio, "create_task", observe):
        watched = [probes.enter_context(patch.object(owner, name, wraps=getattr(owner, name))) for owner, name in
            [(assignment_notification, "prepare_request"), (assignment_notification, "lock_users"),
             (assignment_notification, "record_event"), (assignment_notification, "run_batch"),
             (lead_delivery, "run_batch"), (leads.ghl, "update_contact_status")]]
        with TestClient(main.app, raise_server_exceptions=False) as client:
            expected = ["cleanup", "payout", "delivery"] + (["assignment"] if mode == "active_start" else [])
            require(sorted(observed) == sorted(expected), "actual_selected_worker_start_required")
            if mode == "paused_start":
                bodies = [({"realtor_id": str(IDS["agent"])}, "lead"),
                    ({"realtor_id": str(IDS["admin"]), "request_id": str(UUID(int=988001))}, "lead"),
                    ({"realtor_id": str(IDS["agent"]), "request_id": str(UUID(int=982001))}, "lead"),
                    ({"realtor_id": str(IDS["admin"]), "request_id": str(UUID(int=988002))}, "account"),
                    ({"realtor_id": None, "request_id": str(UUID(int=988003))}, "account"),
                    ({"realtor_id": str(IDS["agent"]), "request_id": str(UUID(int=987001))}, "account"),
                    ({"realtor_id": None}, "account")]
                paths = {"lead": f"/admin/leads/{IDS['lead']}/assign", "account": f"/admin/users/{IDS['buyer']}/assign-realtor"}
                for body, scope in bodies:
                    response = client.put(paths[scope], json=body, headers=admin)
                    require(response.status_code == 503 and response.headers.get("cache-control") == "no-store"
                            and response.json() == {"detail": "Assignment changes are temporarily paused"}, "real_paused503_required")
                for path in paths.values():
                    require(client.put(path, json=bodies[0][0]).status_code == 401
                            and client.put(path, json=bodies[0][0], headers=buyer).status_code == 403
                            and client.put(path, json=bodies[0][0], headers={**admin, "Origin": "https://untrusted.example"}).status_code == 403
                            and client.put(path, content=canonical(bodies[0][0]), headers={**admin, "Content-Type": "text/plain"}).status_code == 422,
                            "real_paused_auth_json_boundaries_required")
            histories = [(f"/admin/leads/{IDS['lead']}/assignment-history", 6),
                         (f"/admin/users/{IDS['buyer']}/assignment-history", 2)]
            for path, total in histories:
                response = client.get(path, headers=admin); value = response.json()
                require(response.status_code == 200 and response.headers.get("cache-control") == "no-store"
                        and set(value) == {"total", "limit", "offset", "tracked", "items"}
                        and value["total"] == total and value["tracked"] is True, "retained_real_history_metadata_required")
                notices = [notice for item in value["items"] for notice in item["notices"]]
                require(all(set(item) == {"id", "action", "created_at", "notices"} for item in value["items"])
                        and all(set(item) == {"kind", "status", "attempts", "created_at", "attempted_at", "accepted_at", "outcome_uncertain"} for item in notices)
                        and (sorted(item["status"] for item in notices) == sorted(STATUSES) if total == 6 else not notices),
                        "history_exact_safe_outcomes_required")
                require(not any(marker in response.text for marker in ("PRIVATE_", "@example.com", "provider_id", "recipient_email", "payload"))
                        and client.get(path).status_code == 401 and client.get(path, headers=buyer).status_code == 403
                        and client.get(path, headers=agent).status_code == 403, "real_history_privacy_required")
            signup = f"/admin/users/{IDS['signup']}/communications"
            ordered = []
            for offset in range(0, 11, 2):
                response = client.get(signup + f"?limit=2&offset={offset}", headers=admin); value = response.json()
                require(response.status_code == 200 and value["total"] == 11 and value["limit"] == 2 and value["offset"] == offset
                        and len(value["items"]) == min(2, 11 - offset) and "PRIVATE_" not in response.text, "real_signup_retention_paging_required")
                ordered.extend(item["step"] for item in value["items"])
            require(set(ordered) == {"email_" + str(i) for i in range(1, 11)} | {"customer_email"} and len(ordered) == 11,
                    "all_eleven_signup_metadata_rows_required")
            own = client.get("/professional-profile", headers=buyer)
            require(own.status_code == 200 and own.json()["draft"]["provider_kind_ids"] == ["contractor"], "private_facet_json_survives_required")
            for route in ("/professionals", "/professionals/map"):
                approved = client.get(route + "?provider_kind=cooperative&property_class=hotel_hospitality&work_scope=financing", headers=buyer)
                hidden = client.get(route + "?provider_kind=financial_institution", headers=buyer)
                require(approved.status_code == hidden.status_code == 200 and approved.json()["total"] == 1 and hidden.json()["total"] == 0,
                        "approved_facet_scope_survives_required")
            reviewed = client.get("/admin/professionals?provider_kind=financial_institution", headers=admin)
            require(reviewed.status_code == 200 and reviewed.json()["total"] == 1
                    and reviewed.json()["items"][0]["work_scope_ids"] == ["advisory"], "admin_private_facets_survive_required")
        require(all(operation.call_count == 0 for operation in watched),
                "startup_reads_must_not_prepare_assignment_or_dispatch_required")
    require(len(tasks) == len(expected) and all(task.done() for task in tasks) and sorted(observed) == sorted(expected),
            "all_real_selected_workers_drained_required")
    require(not legacy.EXTERNAL and not legacy.MOCK_CALLS, "zero_startup_provider_and_mock_calls_required")


def canonical_mode(legacy, native, base, application, catalog, database, url):
    global STAGE
    start = catalog.query(database, "SELECT pg_postmaster_start_time()")
    STAGE = "previous99"; native.previous_start(base, url)
    tables = tuple(sorted(catalog.OLD_TABLES + native.GROUPS))
    require(len(tables) == 99 and catalog.table_names(database) == tables, "genuine_previous99_required")
    STAGE = "baseline106"; legacy.migration(Path("/baseline106"), url, "upgrade", legacy.OLD_HEAD)
    tables = tuple(sorted(tables + native.INBOX))
    require(len(tables) == 106 and catalog.table_names(database) == tables, "genuine_baseline106_required")
    STAGE = "seed106"; child("seed106", Path("/baseline106"), url)
    before, definitions, access = catalog.snapshot(database, tables), catalog.catalog(database, tables), legacy.security(catalog, database)
    STAGE = "migrate108"; legacy.migration(application, url, "upgrade", legacy.HEAD)
    legacy.preserved(catalog, database, tables, before, definitions, access, True)
    full = tuple(sorted(tables + legacy.NEW))
    require(catalog.table_names(database) == full and len(full) == 108, "genuine_populated108_required")
    STAGE = "seed108"; child("seed108", application, url)
    require(catalog.query(database, "SELECT status,count(*) FROM assignment_email_jobs GROUP BY status ORDER BY status") == [(s, 1) for s in sorted(STATUSES)]
            and catalog.query(database, "SELECT count(*) FROM assignment_events") == [(8,)], "six_legal_retained_outcomes_eight_events_required")
    legacy.privacy(catalog, database)
    rows, definition, security = catalog.snapshot(database, full), catalog.catalog(database, full), legacy.security(catalog, database)
    namespaces = catalog.query(database, "SELECT nspname,nspowner,nspacl::text FROM pg_namespace ORDER BY nspname")
    STAGE = "paused_start"; child("paused_start", Path("/paused"), url)
    require(catalog.snapshot(database, full) == rows and catalog.catalog(database, full) == definition
            and legacy.security(catalog, database) == security
            and catalog.query(database, "SELECT nspname,nspowner,nspacl::text FROM pg_namespace ORDER BY nspname") == namespaces,
            "all108_paused_rows_catalog_security_required")
    STAGE = "active_start"; child("active_start", application, url)
    require(catalog.snapshot(database, full) == rows and catalog.catalog(database, full) == definition
            and legacy.security(catalog, database) == security
            and catalog.query(database, "SELECT nspname,nspowner,nspacl::text FROM pg_namespace ORDER BY nspname") == namespaces,
            "all108_active_source_equivalence_required")
    STAGE = "preservation"
    legacy.preserved(catalog, database, tables, before, definitions, access, True)
    require(catalog.query(database, "SELECT pg_postmaster_start_time()") == start and not legacy.EXTERNAL and not legacy.MOCK_CALLS,
            "same_server_zero_transport_required")
    emit("RESULT", {"status": "PASS", "mode": "canonical", "schema_tables": 108, "model_tables": 107, "old_tables": 106,
        "owner_contacts_unchanged": 82, "private_profiles_unchanged": 46, "accepted_jobs_unchanged": 9, "cms_version_unchanged": 7,
        "floorplans_unchanged": 2, "prepared_signup_jobs": 11, "prepared_facet_profiles": 2, "prepared_events": 8, "prepared_notices": 6,
        "all108_rows_catalog_sequences_security_unchanged": True, "old106_preserved_across_upgrade": True,
        "genuine_paused_bootstrap_startup": True, "paused503_requests": 7, "paused_auth_origin_json_checks": 8,
        "paused_assignment_worker_starts": 0, "active_assignment_worker_starts": 1, "prior_workers_started_drained_each": 3,
        "synthetic_delivery_master_enabled": False, "synthetic_customer_flag": True, "effective_signup_enabled": False,
        "active_forward_source_equivalent": True, "history_and_facets_private": True, "no_downgrade_reset_or_replay": True,
        "server_start_unchanged": True, "external_provider_attempts": 0, "mocked_provider_calls": 0})


def unit_mode(legacy, native, base, application, catalog, database, url, mode):
    global STAGE
    STAGE = "inventory"
    spec = PLAN["source_suites"][mode]; filename = application / spec["file"]
    require(sha256(filename.read_bytes()).hexdigest() == spec["test_file_sha256"], "exact_current_suite_bytes_required")
    methods = native.suite_inventory(application, filename.name, spec["class"], spec["count"])
    require(methods == set(spec["methods"]), "exact_current_method_inventory_required")
    os.environ["SEARCH_FILTER_TEST_DATABASE_URL"] = url
    start, schemas = catalog.query(database, "SELECT pg_postmaster_start_time()"), catalog.query(database, "SELECT nspname FROM pg_namespace ORDER BY nspname")
    original, sequences, access = catalog.catalog(database, ("users",)), catalog.snapshot(database, ()), legacy.security(catalog, database)
    captured = StringIO(); STAGE = "unit_suite"
    with redirect_stdout(captured), redirect_stderr(captured):
        module_spec = spec_from_file_location("owned_directory_" + mode, filename)
        module = module_from_spec(module_spec); module_spec.loader.exec_module(module)
        if mode != "catalog":
            import models.trade_contact
        case = getattr(module, spec["class"])
        require(getattr(case, "__unittest_skip__", False) is False, "source_suite_no_skip_required")
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(case)
        require(suite.countTestCases() == spec["count"] and {item._testMethodName for item in suite} == methods,
                "exact_loaded_source_suite_required")
        result = unittest.TestResult(); suite.run(result)
    emit("COUNTS", {"suite": mode, "expected": spec["count"], "run": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped)})
    for name in sorted({getattr(item, "_testMethodName", "fixture_setup_or_cleanup") for item, _ in result.failures + result.errors}):
        emit("FAILED_METHOD", {"method": name if name in methods else "fixture_setup_or_cleanup"})
    require(len(captured.getvalue().encode()) <= 2 * 1024 * 1024 and result.testsRun == spec["count"]
            and result.wasSuccessful() and not result.skipped and not result.expectedFailures and not result.unexpectedSuccesses
            and not legacy.EXTERNAL, "all_exact_source_cases_required")
    require(not legacy.MOCK_CALLS, "zero_mock_transports_in_source_suites_required")
    STAGE = "unit_cleanup"
    require(catalog.table_names(database) == () and catalog.query(database, "SELECT nspname FROM pg_namespace ORDER BY nspname") == schemas
            and catalog.catalog(database, ("users",)) == original and catalog.snapshot(database, ()) == sequences
            and legacy.security(catalog, database) == access and catalog.query(database, "SELECT pg_postmaster_start_time()") == start,
            "unit_schema_public_security_cleanup_required")
    base.verified_checkout()
    emit("RESULT", {"status": "PASS", "mode": mode, "tests": spec["count"], "exact_current_source": True,
        "model_created_test_schema": mode != "catalog", "pure_catalog_only": mode == "catalog", "genuine108_claim": False,
        "all_owned_schema_cleanup": True, "public_catalog_sequences_security_unchanged": True,
        "server_start_unchanged": True, "external_provider_attempts": 0,
        "mocked_provider_calls": len(legacy.MOCK_CALLS)})


def run():
    global STAGE
    logging.disable(logging.CRITICAL)
    require(len(sys.argv) == 2 and sys.argv[1] in {"canonical", *SUITES, *INTERNAL}, "explicit_fixed_mode_required")
    mode = sys.argv[1]; legacy = load_legacy()
    native, base, application = configure(legacy, mode)
    catalog, database, url = claim(legacy, base, mode, empty=mode in SUITES | {"canonical"})
    base.configure(application, url)
    os.environ["ALLOWED_ORIGINS"] = "http://testserver"
    os.environ["CUSTOMER_MESSAGES_ENABLED"] = "true"
    with ExitStack() as stack:
        for item in legacy.provider_patches(): stack.enter_context(item)
        if mode == "canonical": canonical_mode(legacy, native, base, application, catalog, database, url)
        elif mode in SUITES: unit_mode(legacy, native, base, application, catalog, database, url, mode)
        else:
            STAGE = mode
            if mode == "seed106": seed106(native, base, catalog, database)
            elif mode == "seed108": seed108()
            else: source_start(mode, legacy)
            require(not legacy.EXTERNAL and not legacy.MOCK_CALLS, "zero_internal_provider_calls_required")
            emit("INTERNAL", {"mode": mode, "status": "PASS", "external_provider_attempts": 0})


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        reason = str(error) if isinstance(error, Failure) else "unexpected_exception"
        kind = type(error).__name__ if type(error).__name__ in {"Failure", "AssertionError", "RuntimeError", "ValueError", "TypeError",
            "KeyError", "TimeoutExpired", "OperationalError", "ProgrammingError", "IntegrityError"} else "other"
        state = getattr(getattr(error, "orig", error), "pgcode", None)
        emit("FAILURE", {"stage": STAGE if STAGE in PHASES else "source", "error_type": kind, "reason": reason,
                         "sqlstate": state if isinstance(state, str) and re.fullmatch("[A-Z0-9]{5}", state) else None})
        raise SystemExit(1) from None
