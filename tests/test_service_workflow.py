"""Synthetic private service API and real separate-session concurrency contracts.

Only an explicitly supplied reviewed disposable loopback Postgres is accepted.
No lifespan, production data, accounts, mail, storage or external providers.
"""
import importlib.util
import os
import re
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-service-only",
                  GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
                  LEAD_DELIVERY_WORKER_ENABLED="false", CUSTOMER_MESSAGES_ENABLED="false",
                  SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")

import httpx
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
import models
from database import Base, get_db
from models.user import User
from models.listing import Listing
from models.professional_profile import ProfessionalProfile
from models.property_network import NetworkProject, NetworkProjectMember, NetworkMedia, NetworkProjectUpdate
from models.service_workflow import (NetworkServicePreference, NetworkServiceRequest, NetworkServiceRecipient,
    NetworkServiceProposal, NetworkServiceAction)
from routes import service_workflow, property_network
from schemas.service_workflow import AwardWrite
from schemas.property_network import RegistryEdit
from services import service_workflow as service, property_network as network
from utils.auth import get_current_user
from utils import private_network_media as storage


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class PrivateServiceWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host != "127.0.0.1" or url.port != 55449 or url.database != "ildr_platform_test_20261025" or url.get_backend_name() != "postgresql":
            raise ValueError("Use the fixed disposable loopback Postgres only")
        cls.schema = "service_test_" + uuid4().hex
        cls.bootstrap = create_engine(url)
        cls.addClassCleanup(cls.cleanup_owned_schema)
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema + " -clock_timeout=5000 -cstatement_timeout=10000"})
        Base.metadata.create_all(cls.engine)
        for filename in ("d1e3f7a9b521_private_property_network.py", "f3a5b9c1d723_private_property_registry.py", "a4b6c0d2e834_private_service_workflow.py"):
            path = Path(__file__).resolve().parents[1] / "alembic/versions" / filename
            spec = importlib.util.spec_from_file_location("service_fixture_" + filename[:-3], path)
            revision = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(revision)
            with cls.engine.begin() as connection:
                revision.op = Operations(MigrationContext.configure(connection))
                revision.upgrade()
        app = FastAPI()
        app.include_router(property_network.router)
        app.include_router(service_workflow.router)

        def local_db():
            with Session(cls.engine) as db:
                yield db

        def identity(request: Request, db=Depends(get_db)):
            key = request.headers.get("x-test-role")
            if key not in cls.ids:
                raise HTTPException(401, "Synthetic authentication required")
            user = db.get(User, cls.ids[key])
            if user.status != "active" or user.email_verified is not True:
                raise HTTPException(403, "Account unavailable")
            return user
        app.dependency_overrides[get_db] = local_db
        app.dependency_overrides[get_current_user] = identity
        cls.client = TestClient(app)
        cls.old_limiter = property_network.limiter.enabled
        property_network.limiter.enabled = False

    @classmethod
    def cleanup_owned_schema(cls):
        if hasattr(cls, "client"):
            cls.client.close()
        if hasattr(cls, "old_limiter"):
            property_network.limiter.enabled = cls.old_limiter
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        assert re.fullmatch(r"service_test_[0-9a-f]{32}", cls.schema)
        try:
            with cls.bootstrap.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE'))
        finally:
            cls.bootstrap.dispose()

    def setUp(self):
        with self.engine.begin() as connection:
            tables = ",".join(f'"{self.schema}"."{table.name}"' for table in Base.metadata.sorted_tables)
            connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        self.__class__.ids, self.codes = {}, {}
        with Session(self.engine) as db:
            for code, (key, role) in enumerate((("owner", "owner"), ("admin", "admin"), ("manager", "realtor"),
                    ("new_manager", "realtor"), ("pro1", "buyer"), ("pro2", "buyer"), ("outsider", "buyer"),
                    ("suspended", "buyer"), ("unverified", "buyer")), 6001):
                self.ids[key], self.codes[key] = uuid4(), code
                db.add(User(id=self.ids[key], user_code=code, email=f"service-{key}@example.com", display_name="PRIVATE-ACCOUNT-" + key,
                    role=role, status="suspended" if key == "suspended" else "active", email_verified=key != "unverified"))
            db.flush()
            self.listing = uuid4()
            db.add(Listing(id=self.listing, title="Private-owner listing", type="villa", transaction="sale", price=100,
                location="EXACT-LOCATION-MARKER", owner_id=self.ids["owner"], submitted_by=self.ids["owner"],
                assigned_realtor_id=self.ids["manager"], status="active"))
            for _ in range(46):
                db.add(ProfessionalProfile(id=uuid4(), user_id=None, draft_data={"public_contact_consent": False},
                    version=1, review_status="draft", listing_status="hidden"))
            db.commit()
        self.patchers = []
        self.addCleanup(self.stop_patches)
        self.patched(httpx.HTTPTransport, "handle_request", side_effect=AssertionError("No real HTTP"))
        self.patched(storage, "_get_client", side_effect=AssertionError("No Supabase provider"))

    def patched(self, target, name, *args, **kwargs):
        item = patch.object(target, name, *args, **kwargs)
        self.patchers.append(item)
        return item.start()

    def stop_patches(self):
        for item in reversed(self.patchers):
            item.stop()

    def send(self, method, path, role="owner", body=None):
        return self.client.request(method, "/property-network" + path,
            headers={"x-test-role": role, "Authorization": "Bearer synthetic-only"}, json=body)

    def ok(self, response, status=200):
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def opt(self, role, **changes):
        old = self.ok(self.send("GET", "/me/service-preferences", role))
        body = {"submission_id": str(uuid4()), "expected_version": old["version"], "service_opt_in": True,
            "member_alias": "Deliberate alias " + role, "category_ids": ["pool_service"], "property_class_ids": ["house_villa"], **changes}
        return self.ok(self.send("PUT", "/me/service-preferences", role, body))

    def private(self):
        return self.ok(self.send("POST", "/registry", body={"submission_id": str(uuid4()), "title": "PRIVATE-PROPERTY-TITLE",
            "location": "PRIVATE-LOCATION", "address": "PRIVATE-ADDRESS", "notes": "PRIVATE-NOTES", "property_class": "house_villa",
            "relationship": "manager", "authority_acknowledged": True}), 201)

    def request_body(self, private=None, **changes):
        source = {"property_id": private["id"]} if private else {"listing_id": str(self.listing)}
        return {**source, "submission_id": str(uuid4()), "category_id": "pool_service", "property_class": "house_villa",
            "title": "Shared work", "brief": "Deliberately shared scope", "area_label": "Coarse area", "requester_alias": "Property custodian",
            "recipient_user_codes": sorted([self.codes["pro1"], self.codes["pro2"]]), "shared_brief_acknowledged": True, **changes}

    def request(self, private=None, role="owner", **changes):
        self.opt("pro1"); self.opt("pro2")
        body = self.request_body(private, **changes)
        return self.ok(self.send("POST", "/service-requests", role, body), 201), body

    def received(self, request, role="pro1"):
        return self.ok(self.send("GET", "/service-requests/" + request["id"] + "?view=received", role))

    def accepted(self, request, role="pro1"):
        row = self.received(request, role)
        return self.ok(self.send("POST", f"/service-requests/{request['id']}/recipients/{row['recipient']['id']}/accept", role,
            {"submission_id": str(uuid4()), "expected_recipient_version": row["recipient"]["version"]}))

    def terms(self, **changes):
        return {"amount": "123.45", "currency": "USD", "pricing_basis": "estimate", "scope_text": "Proposed exact work",
            "exclusions": "No extra materials", "estimated_days": 3, "valid_until": (service.today_dr() + timedelta(days=10)).isoformat(), **changes}

    def quote(self, request, role="pro1", **changes):
        row = self.received(request, role)
        if row["recipient"]["status"] == "pending":
            row = self.accepted(request, role)
        current = row["current_proposal"]
        body = {"submission_id": str(uuid4()), "expected_recipient_version": row["recipient"]["version"],
            "current_proposal_id": current["id"] if current else None, "expected_proposal_version": current["version"] if current else None,
            "terms": self.terms(**changes)}
        response = self.send("POST", f"/service-requests/{request['id']}/recipients/{row['recipient']['id']}/proposals", role, body)
        return self.ok(response), body

    def award_body(self, request, role="pro1", **changes):
        view = self.ok(self.send("GET", "/service-requests/" + request["id"]))
        recipient = next(r for r in view["recipients"] if r["user_code"] == self.codes[role])
        return {"submission_id": str(uuid4()), "expected_request_version": view["version"], "recipient_id": recipient["id"],
            "expected_recipient_version": recipient["version"], "proposal_id": recipient["current_proposal"]["id"],
            "expected_proposal_version": recipient["current_proposal"]["version"], "project_title": "Approved project title",
            "project_summary": "Approved participant-visible scope", "project_scope_acknowledged": True, **changes}

    def counts(self):
        with Session(self.engine) as db:
            return tuple(db.query(cls).count() for cls in (NetworkServiceRequest, NetworkServiceRecipient, NetworkServiceProposal,
                NetworkServiceAction, NetworkProject, NetworkProjectMember, NetworkProjectUpdate))

    def parallel(self, calls):
        barrier = threading.Barrier(len(calls), timeout=5)
        def run(call):
            barrier.wait()
            return call()
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            return [future.result(timeout=15) for future in [pool.submit(run, call) for call in calls]]

    def test_preferences_default_read_only_consent_strict_identity_and_imports_unchanged(self):
        before = self.counts()
        row = self.ok(self.send("GET", "/me/service-preferences", "pro1"))
        self.assertEqual((row["service_opt_in"], row["version"], row["updated_at"]), (False, 0, None))
        self.assertEqual(self.counts(), before)
        self.opt("pro1")
        for role in ("suspended", "unverified"):
            self.assertEqual(self.send("GET", "/me/service-preferences", role).status_code, 403)
        body = {"submission_id": str(uuid4()), "expected_version": 0, "service_opt_in": True, "member_alias": "Alias",
            "category_ids": ["pool_service"], "property_class_ids": ["house_villa"]}
        self.assertEqual(self.send("PUT", "/me/service-preferences", "admin", {**body, "user_id": str(self.ids["pro2"])}).status_code, 422)
        self.assertEqual(self.send("PUT", "/me/service-preferences", "pro2", {**body, "service_opt_in": 1}).status_code, 422)
        with Session(self.engine) as db:
            db.get(User, self.ids["pro2"]).user_code = None; db.commit()
        self.assertEqual(self.send("PUT", "/me/service-preferences", "pro2", body).status_code, 409)
        with Session(self.engine) as db:
            imports = db.query(ProfessionalProfile).all()
            self.assertEqual(len(imports), 46)
            self.assertTrue(all(p.user_id is None and p.published_data is None and p.review_status == "draft" and p.listing_status == "hidden" and p.draft_data["public_contact_consent"] is False for p in imports))

    def test_discovery_exact_projection_current_consent_and_matching(self):
        path = "/service-providers?category_id=pool_service&property_class=house_villa"
        self.assertEqual(self.ok(self.send("GET", path))["items"], [])
        self.opt("pro1"); self.opt("pro2", category_ids=["plumbing"])
        items = self.ok(self.send("GET", path))["items"]
        self.assertEqual(items, [{"user_code": self.codes["pro1"], "member_alias": "Deliberate alias pro1", "category_ids": ["pool_service"]}])
        self.opt("pro1", service_opt_in=False)
        self.assertEqual(self.ok(self.send("GET", path))["total"], 0)
        self.assertEqual(self.send("GET", path + "&page=0").status_code, 422)

    def test_request_source_authority_selection_xor_and_strict_codes(self):
        self.opt("pro1"); self.opt("pro2")
        private = self.private()
        body = self.request_body(private)
        self.assertEqual(self.send("POST", "/service-requests", "outsider", body).status_code, 404)
        for change in ({"listing_id": str(self.listing)}, {"property_id": None}, {"shared_brief_acknowledged": False},
                {"recipient_user_codes": [True]}, {"recipient_user_codes": [self.codes["pro1"]] * 2}, {"category_id": "invented"}):
            self.assertEqual(self.send("POST", "/service-requests", body={**body, **change}).status_code, 422)
        self.opt("pro2", service_opt_in=False)
        self.assertEqual(self.send("POST", "/service-requests", body=body).status_code, 409)
        self.assertEqual(self.counts()[:3], (0, 0, 0))

    def test_create_replay_original_fingerprint_before_new_archive_preference_and_quota_gates(self):
        private = self.private()
        request, body = self.request(private)
        fields = {k: private[k] for k in ("title", "location", "address", "property_class", "relationship", "notes")}
        self.ok(self.send("PUT", "/registry/" + private["id"], body={**fields, "status": "archived", "expected_version": 1}))
        self.opt("pro1", service_opt_in=False)
        with Session(self.engine) as db:
            db.get(User, self.ids["pro2"]).user_code = 9001; db.commit()
        self.patched(service, "MAX_SOURCE_REQUESTS", 0)
        before = self.counts()
        self.assertEqual(self.ok(self.send("POST", "/service-requests", body=body), 201)["id"], request["id"])
        self.assertEqual(self.send("POST", "/service-requests", body={**body, "brief": "Changed original"}).status_code, 409)
        self.assertEqual(self.counts(), before)

    def test_managed_acl_original_custodian_and_requester_without_new_manager_inheritance(self):
        request, _ = self.request(role="manager")
        self.assertEqual(self.send("GET", "/service-requests/" + request["id"]).status_code, 200)
        with Session(self.engine) as db:
            db.get(Listing, self.listing).assigned_realtor_id = self.ids["new_manager"]; db.commit()
        for suffix in ("", "/comparison", "/events"):
            self.assertEqual(self.send("GET", "/service-requests/" + request["id"] + suffix, "new_manager").status_code, 404)
            self.assertEqual(self.send("GET", "/service-requests/" + request["id"] + suffix, "manager").status_code, 404)
        self.assertEqual(self.ok(self.send("GET", "/service-requests?view=managed", "new_manager"))["total"], 0)
        self.assertEqual(self.send("GET", "/service-requests/" + request["id"], "admin").status_code, 200)

    def test_received_projection_events_and_result_versions_never_leak_competitor_or_dossier(self):
        private = self.private()
        request, _ = self.request(private)
        first, first_body = self.quote(request)
        before = self.received(request)
        self.quote(request, "pro2", amount="333.00", currency="DOP")
        after = self.received(request)
        self.assertEqual(before, after)
        for forbidden in (private["id"], "PRIVATE-ADDRESS", "PRIVATE-LOCATION", "PRIVATE-NOTES", "PRIVATE-PROPERTY-TITLE", "service-pro2@example.com", "333.00"):
            self.assertNotIn(forbidden, str(after))
        self.assertNotIn("version", after)
        self.assertNotIn("updated_at", after)
        result = self.ok(self.send("GET", "/service-actions/submissions/" + first_body["submission_id"], "pro1"))
        self.assertEqual(set(result["result_versions"]), {"recipient_version", "proposal_version"})
        self.assertEqual(self.send("GET", "/service-actions/submissions/" + first_body["submission_id"], "pro2").status_code, 404)
        events = self.ok(self.send("GET", f"/service-requests/{request['id']}/events", "pro1"))["items"]
        self.assertTrue(all(set(e["result_versions"]) <= {"recipient_version", "proposal_version"} for e in events))

    def test_exact_decimal_currency_bounds_and_immutable_proposal_terms(self):
        request, _ = self.request()
        row = self.accepted(request)
        base = {"submission_id": str(uuid4()), "expected_recipient_version": row["recipient"]["version"], "current_proposal_id": None, "expected_proposal_version": None}
        path = f"/service-requests/{request['id']}/recipients/{row['recipient']['id']}/proposals"
        for amount in (0, True, 1.25, "0", "-1", "1.001", "10000000000", "NaN", "01.00"):
            self.assertEqual(self.send("POST", path, "pro1", {**base, "terms": self.terms(amount=amount)}).status_code, 422)
        quote, _ = self.quote(request, amount="9999999999.99", currency="DOP")
        self.assertEqual(quote["current_proposal"]["amount"], "9999999999.99")
        with self.assertRaises(DBAPIError), self.engine.begin() as connection:
            connection.execute(text("UPDATE network_service_proposals SET amount=1,version=version+1 WHERE id=:id"), {"id": quote["current_proposal"]["id"]})

    def test_counter_decision_replacement_and_partitioned_immutable_history(self):
        request, _ = self.request()
        first, _ = self.quote(request)
        managed = self.ok(self.send("GET", "/service-requests/" + request["id"]))
        recipient = next(r for r in managed["recipients"] if r["user_code"] == self.codes["pro1"])
        counter = {"submission_id": str(uuid4()), "expected_request_version": managed["version"],
            "expected_recipient_version": recipient["version"], "current_proposal_id": recipient["current_proposal"]["id"],
            "expected_proposal_version": recipient["current_proposal"]["version"], "terms": self.terms(amount="100.00", pricing_basis="fixed")}
        self.ok(self.send("POST", f"/service-requests/{request['id']}/recipients/{recipient['id']}/counter", body=counter))
        for role, permitted in (("owner", True), ("admin", False)):
            view = self.ok(self.send("GET", "/service-requests/" + request["id"], role))
            target = next(r for r in view["recipients"] if r["id"] == recipient["id"])
            self.assertEqual(target["capabilities"]["can_withdraw"], permitted)
        received = self.received(request)
        proposal = received["current_proposal"]
        withdrawal = {"submission_id": str(uuid4()), "expected_recipient_version": received["recipient"]["version"], "expected_proposal_version": proposal["version"]}
        self.assertEqual(self.send("POST", "/service-proposals/" + proposal["id"] + "/withdraw", "admin", withdrawal).status_code, 404)
        self.ok(self.send("POST", "/service-proposals/" + proposal["id"] + "/accept-counter", "pro1", {
            "submission_id": str(uuid4()), "expected_recipient_version": received["recipient"]["version"], "expected_proposal_version": proposal["version"]}))
        self.assertEqual(self.counts()[4], 0)
        replacement, _ = self.quote(request, amount="110.00")
        history_path = f"/service-requests/{request['id']}/recipients/{recipient['id']}/proposals"
        history = self.ok(self.send("GET", history_path, "pro1"))
        self.assertEqual([p["revision_no"] for p in history["items"]], [3, 2, 1])
        self.assertEqual([p["amount"] for p in history["items"]], ["110.00", "100.00", "123.45"])
        self.assertEqual(self.send("GET", history_path, "pro2").status_code, 404)
        self.assertEqual(self.send("GET", history_path + "?page=0", "pro1").status_code, 422)

    def test_revocation_excludes_every_received_filter_count_history_and_result(self):
        request, _ = self.request()
        received, body = self.quote(request)
        managed = self.ok(self.send("GET", "/service-requests/" + request["id"]))
        recipient = next(r for r in managed["recipients"] if r["user_code"] == self.codes["pro1"])
        self.ok(self.send("POST", f"/service-requests/{request['id']}/recipients/{recipient['id']}/revoke", body={
            "submission_id": str(uuid4()), "expected_request_version": managed["version"], "expected_recipient_version": recipient["version"]}))
        for status in ("all", "actionable", "closed", "awarded"):
            self.assertEqual(self.ok(self.send("GET", "/service-requests?view=received&status=" + status, "pro1"))["total"], 0)
        for path in ("/service-requests/" + request["id"], f"/service-requests/{request['id']}/events",
                f"/service-requests/{request['id']}/recipients/{recipient['id']}/proposals", "/service-actions/submissions/" + body["submission_id"]):
            self.assertEqual(self.send("GET", path, "pro1").status_code, 404)

    def test_counter_withdraw_capability_and_action_belong_only_to_original_current_author(self):
        request, _ = self.request(role="manager")
        self.quote(request)
        managed = self.ok(self.send("GET", "/service-requests/" + request["id"], "manager"))
        recipient = next(r for r in managed["recipients"] if r["user_code"] == self.codes["pro1"])
        counter = {"submission_id": str(uuid4()), "expected_request_version": managed["version"],
            "expected_recipient_version": recipient["version"], "current_proposal_id": recipient["current_proposal"]["id"],
            "expected_proposal_version": recipient["current_proposal"]["version"], "terms": self.terms()}
        current = self.ok(self.send("POST", f"/service-requests/{request['id']}/recipients/{recipient['id']}/counter", "manager", counter))
        target = next(r for r in current["recipients"] if r["id"] == recipient["id"])
        self.assertTrue(target["capabilities"]["can_withdraw"])
        proposal = target["current_proposal"]
        body = {"submission_id": str(uuid4()), "expected_recipient_version": target["version"], "expected_proposal_version": proposal["version"]}
        for role in ("owner", "admin"):
            view = self.ok(self.send("GET", "/service-requests/" + request["id"], role))
            self.assertFalse(next(r for r in view["recipients"] if r["id"] == recipient["id"])["capabilities"]["can_withdraw"])
            self.assertEqual(self.send("POST", "/service-proposals/" + proposal["id"] + "/withdraw", role, body).status_code, 404)
        self.assertEqual(self.send("POST", "/service-proposals/" + proposal["id"] + "/withdraw", "manager", {**body, "expected_proposal_version": 99}).status_code, 409)
        result = self.ok(self.send("POST", "/service-proposals/" + proposal["id"] + "/withdraw", "manager", body))
        self.assertEqual(next(r for r in result["recipients"] if r["id"] == recipient["id"])["current_proposal"]["status"], "withdrawn")

    def test_expired_original_counter_can_withdraw_and_replay_but_lost_acl_cannot_resolve(self):
        request, _ = self.request(role="manager")
        self.quote(request)
        managed = self.ok(self.send("GET", "/service-requests/" + request["id"], "manager"))
        recipient = next(r for r in managed["recipients"] if r["user_code"] == self.codes["pro1"])
        counter = {"submission_id": str(uuid4()), "expected_request_version": managed["version"],
            "expected_recipient_version": recipient["version"], "current_proposal_id": recipient["current_proposal"]["id"],
            "expected_proposal_version": recipient["current_proposal"]["version"], "terms": self.terms(valid_until=service.today_dr().isoformat())}
        self.ok(self.send("POST", f"/service-requests/{request['id']}/recipients/{recipient['id']}/counter", "manager", counter))
        next_day = service.today_dr() + timedelta(days=1)
        self.patched(service, "today_dr", return_value=next_day)
        managed = self.ok(self.send("GET", "/service-requests/" + request["id"], "manager"))
        target = next(r for r in managed["recipients"] if r["id"] == recipient["id"])
        self.assertTrue(target["current_proposal"]["is_expired"])
        self.assertTrue(target["capabilities"]["can_withdraw"])
        proposal = target["current_proposal"]
        body = {"submission_id": str(uuid4()), "expected_recipient_version": target["version"], "expected_proposal_version": proposal["version"]}
        path = "/service-proposals/" + proposal["id"] + "/withdraw"
        first = self.ok(self.send("POST", path, "manager", body))
        self.assertEqual(self.ok(self.send("POST", path, "manager", body)), first)
        self.assertEqual(self.ok(self.send("GET", "/service-actions/submissions/" + body["submission_id"], "manager"))["operation"], "proposal_withdraw")
        with Session(self.engine) as db:
            db.get(Listing, self.listing).assigned_realtor_id = self.ids["new_manager"]; db.commit()
        self.assertEqual(self.send("POST", path, "manager", body).status_code, 404)
        self.assertEqual(self.send("GET", "/service-actions/submissions/" + body["submission_id"], "manager").status_code, 404)

    def test_explicit_received_audience_stays_own_thread_even_for_selected_admin(self):
        self.opt("admin")
        request, _ = self.request(recipient_user_codes=sorted([self.codes["admin"], self.codes["pro1"]]))
        own, body = self.quote(request, "admin", amount="250")
        other, _ = self.quote(request, "pro1", amount="9999")
        received = self.received(request, "admin")
        self.assertEqual(received["view"], "received")
        self.assertNotIn("version", received)
        self.assertNotIn("recipients", received)
        self.assertNotIn(other["current_proposal"]["id"], str(received))
        self.assertNotIn("9999.00", str(received))
        self.assertEqual(self.ok(self.send("GET", "/service-requests/" + request["id"], "admin"))["view"], "managed")
        events = self.ok(self.send("GET", f"/service-requests/{request['id']}/events?view=received", "admin"))["items"]
        self.assertTrue(all(set(e["result_versions"]) <= {"recipient_version", "proposal_version"} for e in events))
        self.assertNotIn(other["current_proposal"]["id"], str(events))
        other_history = f"/service-requests/{request['id']}/recipients/{other['recipient']['id']}/proposals"
        self.assertEqual(self.send("GET", other_history + "?view=received", "admin").status_code, 404)
        self.assertEqual(self.send("GET", other_history + "?view=managed", "admin").status_code, 200)
        resolved = self.ok(self.send("GET", "/service-actions/submissions/" + body["submission_id"], "admin"))
        self.assertEqual(resolved["current"]["view"], "received")
        ordinary, _ = self.request()
        for suffix in ("?view=received", "/events?view=received"):
            self.assertEqual(self.send("GET", "/service-requests/" + ordinary["id"] + suffix, "admin").status_code, 404)

    def test_history_page30_total_and_revision_order_are_exactly_own_thread(self):
        request, _ = self.request()
        for number in range(31):
            current, _ = self.quote(request, amount=str(number + 1))
        path = f"/service-requests/{request['id']}/recipients/{current['recipient']['id']}/proposals"
        first = self.ok(self.send("GET", path, "pro1"))
        second = self.ok(self.send("GET", path + "?page=2", "pro1"))
        self.assertEqual((first["total"], first["page_size"], len(first["items"])), (31, 30, 30))
        self.assertEqual([p["revision_no"] for p in first["items"]], list(range(31, 1, -1)))
        self.assertEqual([p["revision_no"] for p in second["items"]], [1])
        self.assertEqual(self.send("GET", path + "?page=2", "pro2").status_code, 404)

    def test_optout_pauses_new_actions_but_allows_own_decline_and_history(self):
        private = self.private()
        request, _ = self.request(private)
        received, _ = self.quote(request)
        self.opt("pro1", service_opt_in=False)
        row = self.received(request)
        self.assertFalse(row["capabilities"]["can_quote"])
        self.assertTrue(row["capabilities"]["can_decline"])
        self.assertEqual(self.send("POST", "/service-requests/" + request["id"] + "/award", body=self.award_body(request)).status_code, 409)
        self.ok(self.send("POST", f"/service-requests/{request['id']}/recipients/{row['recipient']['id']}/decline", "pro1", {
            "submission_id": str(uuid4()), "expected_recipient_version": row["recipient"]["version"]}))
        self.assertEqual(self.received(request)["recipient"]["status"], "declined")

    def test_expiry_is_server_dr_date_and_expired_quote_cannot_award(self):
        request, _ = self.request()
        today = service.today_dr()
        quoted, _ = self.quote(request, valid_until=today.isoformat())
        self.assertFalse(quoted["current_proposal"]["is_expired"])
        body = self.award_body(request)
        self.patched(service, "today_dr", return_value=today + timedelta(days=1))
        self.assertTrue(self.received(request)["current_proposal"]["is_expired"])
        self.assertEqual(self.send("POST", "/service-requests/" + request["id"] + "/award", body=body).status_code, 409)
        self.assertEqual(self.counts()[4], 0)

    def test_award_atomic_project_handoff_contains_only_approved_work_and_no_fake_progress(self):
        private = self.private()
        request, _ = self.request(private)
        self.quote(request); self.quote(request, "pro2", amount="9876.54")
        result = self.ok(self.send("POST", "/service-requests/" + request["id"] + "/award", body=self.award_body(request)))
        project = result["project"]
        self.assertEqual((project["property_id"], project["title"], project["summary"]), (private["id"], "Approved project title", "Approved participant-visible scope"))
        self.assertEqual(project["progress"], {"completed": 0, "total": 0, "percent": None})
        winner = self.received(request)
        loser = self.received(request, "pro2")
        self.assertEqual(winner["recipient"]["status"], "awarded")
        self.assertEqual(loser["recipient"]["status"], "closed")
        self.assertIsNone(loser["project_id"])
        self.assertEqual(self.send("GET", "/projects/" + project["id"], "pro2").status_code, 404)
        self.assertEqual(self.send("GET", "/registry/" + private["id"], "pro1").status_code, 404)
        for forbidden in (request["id"], "123.45", "9876.54", "PRIVATE-ADDRESS", "Coarse area", "proposal"):
            self.assertNotIn(forbidden, str(self.ok(self.send("GET", "/projects/" + project["id"], "pro1"))))
        self.assertEqual(self.counts()[4:], (1, 1, 0))
        closed = self.ok(self.send("GET", f"/service-requests/{request['id']}/events", "pro2"))["items"][-1]
        self.assertEqual(closed["action"], "request_closed")
        self.assertLessEqual(set(closed["result_versions"]), {"recipient_version", "proposal_version"})

    def test_award_replay_no_duplicate_and_project_revoke_never_restored(self):
        request, _ = self.request()
        self.quote(request)
        body = self.award_body(request)
        result = self.ok(self.send("POST", "/service-requests/" + request["id"] + "/award", body=body))
        before = self.counts()
        replay = self.ok(self.send("POST", "/service-requests/" + request["id"] + "/award", body=body))
        self.assertEqual(replay["project"]["id"], result["project"]["id"])
        self.assertEqual(self.counts(), before)
        project = result["project"]
        photo = uuid4()
        with Session(self.engine) as db:
            db.add(NetworkMedia(id=photo, uploader_id=self.ids["pro1"], project_id=UUID(project["id"]),
                object_key="synthetic-private-object", sha256="d" * 64, byte_count=24, width=1, height=1))
            db.commit()
        self.patched(storage, "read_image", return_value=b"synthetic-private-image")
        self.assertEqual(self.send("GET", "/media/" + str(photo), "pro1").status_code, 200)
        self.assertEqual(self.send("GET", "/media/" + str(photo), "pro2").status_code, 404)
        self.ok(self.send("POST", f"/projects/{project['id']}/members/{self.codes['pro1']}/revoke", body={"expected_version": project["version"]}))
        self.assertFalse(self.received(request)["can_open_project"])
        self.assertEqual(self.send("GET", "/projects/" + project["id"], "pro1").status_code, 404)
        self.assertEqual(self.send("GET", "/media/" + str(photo), "pro1").status_code, 404)
        self.ok(self.send("POST", "/service-requests/" + request["id"] + "/award", body=body))
        self.assertEqual(self.send("GET", "/projects/" + project["id"], "pro1").status_code, 404)
        self.assertEqual(self.send("POST", "/service-requests/" + request["id"] + "/award", body={**body, "project_summary": "Changed original"}).status_code, 409)

    def test_cancel_terminal_states_and_no_unreadable_action_message(self):
        request, _ = self.request()
        self.quote(request)
        body = {"submission_id": str(uuid4()), "expected_request_version": request["version"]}
        self.assertEqual(self.send("POST", "/service-requests/" + request["id"] + "/cancel", body={**body, "action_message": "Not accepted"}).status_code, 422)
        self.ok(self.send("POST", "/service-requests/" + request["id"] + "/cancel", body=body))
        self.assertEqual(self.received(request)["recipient"]["status"], "closed")
        self.assertEqual(self.counts()[4], 0)
        self.assertEqual(self.send("POST", "/service-requests/" + request["id"] + "/cancel", body={**body, "submission_id": str(uuid4())}).status_code, 409)

    def test_gets_origin_errors_no_store_and_provider_free_read_paths(self):
        request, body = self.request()
        baseline = self.counts()
        for path in ("/service-requests?view=managed", "/service-requests/" + request["id"], f"/service-requests/{request['id']}/comparison",
                f"/service-requests/{request['id']}/events", "/service-actions/submissions/" + body["submission_id"]):
            result = self.send("GET", path)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.headers["cache-control"], "no-store")
        denied = self.send("GET", "/service-requests/" + request["id"], "outsider")
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied.headers["cache-control"], "no-store")
        for origin in (None, "https://foreign.example.com"):
            headers = {"x-test-role": "owner", "Cookie": "ildr_token=synthetic"}
            if origin:
                headers["Origin"] = origin
            self.assertEqual(self.client.post("/property-network/service-requests", json=body, headers=headers).status_code, 403)
        self.assertEqual(self.counts(), baseline)

    def test_real_concurrent_identical_and_changed_request_keys(self):
        self.opt("pro1"); self.opt("pro2")
        body = self.request_body()
        results = self.parallel([lambda: self.send("POST", "/service-requests", body=body)] * 2)
        self.assertEqual([r.status_code for r in results], [201, 201])
        self.assertEqual(results[0].json()["id"], results[1].json()["id"])
        second = self.request_body()
        results = self.parallel([lambda: self.send("POST", "/service-requests", body=second),
            lambda: self.send("POST", "/service-requests", body={**second, "brief": "Different original"})])
        self.assertEqual(sorted(r.status_code for r in results), [201, 409])
        self.assertEqual(self.counts()[0], 2)

    def test_real_concurrent_distinct_awards_have_one_winner_and_project(self):
        request, _ = self.request()
        self.quote(request); self.quote(request, "pro2")
        first, second = self.award_body(request), self.award_body(request, "pro2")
        results = self.parallel([lambda: self.send("POST", "/service-requests/" + request["id"] + "/award", body=first),
            lambda: self.send("POST", "/service-requests/" + request["id"] + "/award", body=second)])
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])
        self.assertEqual(self.counts()[4], 1)
        with Session(self.engine) as db:
            self.assertEqual(db.query(NetworkServiceRecipient).filter_by(status="awarded").count(), 1)

    def test_real_concurrent_quote_same_key_and_changed_original_are_one_revision(self):
        request, _ = self.request()
        received = self.accepted(request)
        path = f"/service-requests/{request['id']}/recipients/{received['recipient']['id']}/proposals"
        body = {"submission_id": str(uuid4()), "expected_recipient_version": received["recipient"]["version"],
            "current_proposal_id": None, "expected_proposal_version": None, "terms": self.terms()}
        results = self.parallel([lambda: self.send("POST", path, "pro1", body)] * 2)
        self.assertEqual([r.status_code for r in results], [200, 200])
        self.assertEqual(results[0].json()["current_proposal"]["id"], results[1].json()["current_proposal"]["id"])
        current = results[0].json()
        changed = {"submission_id": str(uuid4()), "expected_recipient_version": current["recipient"]["version"],
            "current_proposal_id": current["current_proposal"]["id"], "expected_proposal_version": current["current_proposal"]["version"], "terms": self.terms(amount="200")}
        results = self.parallel([lambda: self.send("POST", path, "pro1", changed),
            lambda: self.send("POST", path, "pro1", {**changed, "terms": self.terms(amount="201")})])
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])
        self.assertEqual(self.counts()[2], 2)

    def test_real_concurrent_identical_award_key_returns_same_single_project(self):
        request, _ = self.request()
        self.quote(request)
        body = self.award_body(request)
        results = self.parallel([lambda: self.send("POST", "/service-requests/" + request["id"] + "/award", body=body)] * 2)
        self.assertEqual([r.status_code for r in results], [200, 200])
        self.assertEqual(results[0].json()["project"]["id"], results[1].json()["project"]["id"])
        self.assertEqual(self.counts()[4:], (1, 1, 0))

    def test_real_concurrent_award_and_cancel_have_one_terminal_outcome(self):
        request, _ = self.request()
        self.quote(request)
        award = self.award_body(request)
        cancel = {"submission_id": str(uuid4()), "expected_request_version": request["version"]}
        results = self.parallel([lambda: self.send("POST", "/service-requests/" + request["id"] + "/award", body=award),
            lambda: self.send("POST", "/service-requests/" + request["id"] + "/cancel", body=cancel)])
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])
        status = self.ok(self.send("GET", "/service-requests/" + request["id"]))["status"]
        self.assertEqual(self.counts()[4], int(status == "awarded"))

    def test_real_concurrent_award_and_quote_withdraw_preserve_current_terms(self):
        request, _ = self.request()
        quoted, _ = self.quote(request)
        award = self.award_body(request)
        proposal = quoted["current_proposal"]
        withdraw = {"submission_id": str(uuid4()), "expected_recipient_version": quoted["recipient"]["version"], "expected_proposal_version": proposal["version"]}
        results = self.parallel([lambda: self.send("POST", "/service-requests/" + request["id"] + "/award", body=award),
            lambda: self.send("POST", "/service-proposals/" + proposal["id"] + "/withdraw", "pro1", withdraw)])
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])
        self.assertEqual(self.counts()[4], int(results[0].status_code == 200))

    def test_real_concurrent_optout_and_award_protect_eligibility_without_project_revocation(self):
        request, _ = self.request()
        self.quote(request)
        award = self.award_body(request)
        preference = self.ok(self.send("GET", "/me/service-preferences", "pro1"))
        off = {k: preference[k] for k in ("service_opt_in", "member_alias", "category_ids", "property_class_ids")}
        off.update(submission_id=str(uuid4()), expected_version=preference["version"], service_opt_in=False)
        results = self.parallel([lambda: self.send("POST", "/service-requests/" + request["id"] + "/award", body=award),
            lambda: self.send("PUT", "/me/service-preferences", "pro1", off)])
        self.assertIn(results[0].status_code, (200, 409))
        self.assertEqual(results[1].status_code, 200)
        self.assertEqual(self.counts()[4], int(results[0].status_code == 200))
        if results[0].status_code == 200:
            self.assertTrue(self.received(request)["can_open_project"])

    def test_award_waits_for_actual_registry_archive_lock_and_reloads_source(self):
        private = self.private()
        request, _ = self.request(private)
        self.quote(request)
        body = AwardWrite(**self.award_body(request))
        started, pids = threading.Event(), []
        def award():
            with Session(self.engine) as db:
                pids.append(db.execute(text("SELECT pg_backend_pid()")).scalar_one())
                actor = db.get(User, self.ids["admin"])
                started.set()
                try:
                    return service.close_request(db, actor, UUID(request["id"]), body, award=True)
                except HTTPException as exc:
                    db.rollback()
                    return exc.status_code
        with Session(self.engine) as owner_db:
            owner = owner_db.get(User, self.ids["owner"])
            network.registry_for(owner_db, owner, UUID(private["id"]), lock=True)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(award)
                try:
                    self.assertTrue(started.wait(3))
                    deadline, blocked = time.monotonic() + 3, False
                    while time.monotonic() < deadline:
                        with self.bootstrap.connect() as observe:
                            blocked = bool(observe.execute(text("SELECT cardinality(pg_blocking_pids(:pid))>0"), {"pid": pids[0]}).scalar_one())
                        if blocked:
                            break
                        time.sleep(0.01)
                    self.assertTrue(blocked)
                    fields = {k: private[k] for k in ("title", "location", "address", "property_class", "relationship", "notes")}
                    network.edit_registry(owner_db, owner, UUID(private["id"]),
                        RegistryEdit(**fields, status="archived", expected_version=private["version"]))
                finally:
                    owner_db.rollback()
                self.assertEqual(future.result(timeout=10), 409)
        self.assertEqual(self.counts()[4], 0)

    def test_real_concurrent_source_quota_does_not_overshoot(self):
        self.opt("pro1"); self.opt("pro2")
        self.patched(service, "MAX_OPEN_REQUESTS", 1)
        first, second = self.request_body(), self.request_body()
        results = self.parallel([lambda: self.send("POST", "/service-requests", body=first),
            lambda: self.send("POST", "/service-requests", "manager", second)])
        self.assertEqual(sorted(r.status_code for r in results), [201, 409])
        self.assertEqual(self.counts()[0], 1)

    def test_project_construction_failure_rolls_back_whole_award(self):
        request, _ = self.request()
        self.quote(request)
        before = self.counts()
        original = network.construct_project
        def fail_after_insert(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("Synthetic failure after project flush")
        self.patched(network, "construct_project", side_effect=fail_after_insert)
        result = self.send("POST", "/service-requests/" + request["id"] + "/award", body=self.award_body(request))
        self.assertEqual(result.status_code, 503)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.ok(self.send("GET", "/service-requests/" + request["id"]))["status"], "open")

    def test_same_quote_key_replay_after_archive_and_scope_edit_never_rewrites_terms(self):
        private = self.private()
        request, _ = self.request(private)
        quoted, body = self.quote(request)
        fields = {k: private[k] for k in ("title", "location", "address", "property_class", "relationship", "notes")}
        self.ok(self.send("PUT", "/registry/" + private["id"], body={**fields, "property_class": "office", "status": "archived", "expected_version": 1}))
        before = self.counts()
        path = f"/service-requests/{request['id']}/recipients/{quoted['recipient']['id']}/proposals"
        replay = self.ok(self.send("POST", path, "pro1", body))
        self.assertEqual(replay["current_proposal"]["id"], quoted["current_proposal"]["id"])
        self.assertEqual(self.send("POST", path, "pro1", {**body, "terms": self.terms(amount="999.99")}).status_code, 409)
        new_body = {**body, "submission_id": str(uuid4()), "expected_recipient_version": quoted["recipient"]["version"],
            "current_proposal_id": quoted["current_proposal"]["id"], "expected_proposal_version": quoted["current_proposal"]["version"]}
        self.assertEqual(self.send("POST", path, "pro1", new_body).status_code, 409)
        self.assertFalse(self.received(request)["capabilities"]["can_quote"])
        self.assertEqual(self.counts(), before)
