"""Real session/ACL/SQL contracts in an owned disposable loopback PG schema.

No main lifespan, accounts, provider sends, storage or production DB. The
caller must explicitly supply the reviewed disposable database URL.
"""
import importlib.util
import json
import os
import re
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-native-inbox-only",
                  GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
                  LEAD_DELIVERY_WORKER_ENABLED="false", CUSTOMER_MESSAGES_ENABLED="false",
                  SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")

import httpx
import jwt
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

import models
from config import settings
from database import Base, get_db
from models.listing import Listing
from models.native_inbox import (InboxAudit as Audit, InboxCommand as Command,
    InboxEntry as Entry, InboxPreference as Preference)
from models.property_network import NetworkBlock, NetworkProject, SocialNetworkProfile
from models.service_conversation import NetworkServiceConversationMessage as AwardMessage
from models.service_negotiation import NetworkServiceNegotiationMessage as NegotiationMessage
from models.service_workflow import NetworkServiceRequest as ServiceRequest, NetworkServiceRecipient as Recipient
from models.user import User
from routes import native_inbox, service_workflow
from services import native_inbox as service
from utils import private_network_media as storage


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback PostgreSQL")
class NativeInboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.get_backend_name() != "postgresql" or url.host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Only the reviewed disposable loopback PostgreSQL is allowed")
        cls.schema = "native_inbox_test_" + uuid4().hex
        cls.bootstrap = create_engine(url)
        cls.addClassCleanup(cls.cleanup_owned_schema)
        with cls.bootstrap.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path="+cls.schema+" -clock_timeout=5000 -cstatement_timeout=15000"})
        # Install the legacy model catalog only. The reviewed migration must
        # genuinely create its seven tables, so parity is not a model self-test.
        legacy = [table for name, table in Base.metadata.tables.items() if not name.startswith("inbox_")]
        if len(legacy) != 98:
            raise AssertionError("Expected 98 legacy models plus Alembic bookkeeping in the full 99-table catalog")
        Base.metadata.create_all(cls.engine, tables=legacy)
        path = Path(__file__).resolve().parents[1] / "alembic/versions/d8f4c2a91e67_private_native_inbox.py"
        spec = importlib.util.spec_from_file_location("owned_native_inbox_revision", path)
        cls.revision = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.revision)
        with cls.engine.begin() as db:
            cls.revision.op = Operations(MigrationContext.configure(db))
            cls.revision.upgrade()
        cls.app = FastAPI()
        cls.app.include_router(native_inbox.router)
        cls.app.include_router(service_workflow.router)

        def owned_db():
            with Session(cls.engine) as db:
                yield db

        cls.app.dependency_overrides[get_db] = owned_db
        # Authentication is deliberately NOT replaced: signed tokens resolve real current Users.
        cls.client = TestClient(cls.app)
        cls.old_limiter = native_inbox.limiter.enabled
        native_inbox.limiter.enabled = False

    @classmethod
    def cleanup_owned_schema(cls):
        if hasattr(cls, "client"):
            cls.client.close()
        if hasattr(cls, "old_limiter"):
            native_inbox.limiter.enabled = cls.old_limiter
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        assert re.fullmatch(r"native_inbox_test_[0-9a-f]{32}", cls.schema)
        try:
            with cls.bootstrap.begin() as db:
                db.execute(text(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE'))
        finally:
            cls.bootstrap.dispose()

    def setUp(self):
        # This schema belongs only to this class; no public tables are named here.
        with self.engine.begin() as db:
            tables = ",".join(f'"{self.schema}"."{name}"' for name in Base.metadata.tables)
            db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        self.ids, self.headers = {}, {}
        with Session(self.engine) as db:
            for code, (key, role) in enumerate((("buyer", "buyer"), ("agent", "realtor"),
                    ("new_agent", "realtor"), ("admin", "admin"), ("other_admin", "admin"),
                    ("owner", "owner"), ("member", "owner"), ("private", "owner"),
                    ("professional", "buyer"), ("suspended", "owner"), ("unverified", "realtor")), 8101):
                self.ids[key] = uuid4()
                db.add(User(id=self.ids[key], user_code=code, email=key+"@example.invalid",
                            phone="PRIVATE-TELEPHONE", display_name="Synthetic "+key, role=role,
                            status="suspended" if key=="suspended" else "active", email_verified=key!="unverified"))
                self.headers[key] = {"Authorization": "Bearer "+jwt.encode({"sub": str(self.ids[key]),
                    "exp": datetime.now(timezone.utc)+timedelta(hours=1)}, settings.jwt_secret, algorithm="HS256")}
            db.flush()
            db.get(User, self.ids["buyer"]).assigned_realtor_id = self.ids["agent"]
            db.add_all([Preference(user_id=self.ids[key], visibility="members", version=1)
                        for key in ("owner", "member", "agent", "new_agent")])
            db.commit()
        self.provider_patches = []
        for target, attribute in ((httpx.HTTPTransport, "handle_request"),
                (httpx.AsyncHTTPTransport, "handle_async_request"), (storage, "_get_client")):
            item = patch.object(target, attribute, side_effect=AssertionError("No external provider activity"))
            mock = item.start()
            self.provider_patches.append((item, mock))
        self.addCleanup(self.stop_providers)

    def stop_providers(self):
        for item, mock in reversed(self.provider_patches):
            item.stop()
            self.assertEqual(mock.call_count, 0, "Native inbox attempted an external provider")

    def send(self, method, path, actor="buyer", body=None, expected=200, **kwargs):
        response = self.client.request(method, "/messaging"+path,
            headers=self.headers.get(actor, {}), json=body, **kwargs)
        self.assertEqual(response.status_code, expected, response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertEqual(response.headers.get("pragma"), "no-cache")
        self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(response.headers.get("vary"), "Cookie, Authorization")
        return response.json()

    def command(self, **fields):
        return {"submission_id": str(uuid4()), **fields}

    def receipt(self, value, action):
        self.assertEqual(set(value), {"submission_id", "action", "target_id"})
        self.assertEqual(value["action"], action)
        self.assertEqual(str(UUID(value["target_id"])), value["target_id"])
        return value["target_id"]

    def open(self, actor="buyer", recipient="agent"):
        return self.receipt(self.send("POST", "/conversations", actor,
            self.command(recipient_id=str(self.ids[recipient])), expected=201), "conversation_opened")

    def message(self, conversation, actor="buyer", **fields):
        body = self.command(kind="message", body="Synthetic multiline\nmessage\twith accents é", **fields)
        value = self.send("POST", f"/conversations/{conversation}/entries", actor, body, expected=201)
        return body, self.receipt(value, "entry_created")

    def change(self, actor, **changes):
        with Session(self.engine) as db:
            user = db.get(User, self.ids[actor])
            for key, value in changes.items():
                setattr(user, key, value)
            db.commit()

    def test_recipient_policy_minimal_projection_and_no_implicit_professional_exception(self):
        rows = self.send("GET", "/recipients")["items"]
        self.assertEqual({row["id"] for row in rows}, {str(self.ids[key]) for key in ("agent", "admin", "other_admin")})
        self.assertTrue(all(set(row)=={"id", "user_code", "display_name", "role", "relationship"} for row in rows))
        self.assertNotIn("PRIVATE-TELEPHONE", json.dumps(rows))
        self.assertNotIn("example.invalid", json.dumps(rows))
        self.send("POST", "/conversations", body=self.command(recipient_id=str(self.ids["professional"])), expected=404)
        members = self.send("GET", "/recipients", "owner")["items"]
        self.assertIn(str(self.ids["member"]), {row["id"] for row in members})
        self.assertNotIn(str(self.ids["private"]), {row["id"] for row in members})
        self.assertNotIn(str(self.ids["buyer"]), {row["id"] for row in members})
        for actor in ("suspended", "unverified"):
            self.send("GET", "/recipients", actor, expected=403)
        self.send("GET", "/policy", "unknown", expected=401)
        channels = self.send("GET", "/policy")["channels"]
        self.assertEqual({key for key, value in channels.items() if value}, {"in_app", "manual_whatsapp", "manual_call"})
        self.send("GET", "/recipients?q=%25", "owner")
        self.send("GET", "/recipients?page=0", expected=422)
        self.send("GET", "/recipients?page=1&page=2", expected=422)
        self.send("GET", "/policy?private=true", expected=422)

    def test_real_buyer_agent_reassignment_revokes_old_read_send_and_receipts(self):
        conversation = self.open()
        body, entry_id = self.message(conversation)
        agent_body, _ = self.message(conversation, "agent")
        page = self.send("GET", f"/conversations/{conversation}/entries", "agent")
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["items"][1]["id"], entry_id)
        self.change("buyer", assigned_realtor_id=self.ids["new_agent"])
        self.send("GET", "/commands/"+agent_body["submission_id"], "agent", expected=404)
        self.send("POST", f"/conversations/{conversation}/entries", "agent", agent_body, expected=404)
        for actor in ("agent", "new_agent"):
            self.send("GET", f"/conversations/{conversation}", actor, expected=404)
            self.send("GET", f"/conversations/{conversation}/entries", actor, expected=404)
            self.send("POST", f"/conversations/{conversation}/entries", actor,
                self.command(kind="message", body="No stale access"), expected=404)
            self.assertEqual(self.send("GET", "/unread", actor), {"conversations": 0, "entries": 0})
        retained = self.send("GET", f"/conversations/{conversation}/entries")
        self.assertEqual(retained["total"], 2)
        self.assertEqual(retained["read_only_reason"], "assignment_changed")
        self.send("POST", f"/conversations/{conversation}/entries", body=self.command(kind="message", body="Read only"), expected=409)
        fresh = self.open(recipient="new_agent")
        self.assertNotEqual(fresh, conversation)
        self.assertEqual(self.send("GET", f"/conversations/{fresh}/entries", "new_agent")["total"], 0)
        self.change("new_agent", email_verified=False)
        self.send("GET", f"/conversations/{fresh}/entries", "new_agent", expected=403)
        self.assertEqual(self.send("GET", f"/conversations/{fresh}")["read_only_reason"], "participant_unavailable")

    def test_social_default_is_explicitly_overridable_and_role_changes_revoke_buyer_pair(self):
        with Session(self.engine) as db:
            db.add(SocialNetworkProfile(user_id=self.ids["private"], display_name="Social public name",
                visibility="members", bio="Public member bio", version=1))
            db.commit()
        visible = self.send("GET", "/recipients", "owner")["items"]
        self.assertIn(str(self.ids["private"]), {row["id"] for row in visible})
        self.send("PUT", "/preferences", "private", self.command(expected_version=0, visibility="private"))
        hidden = self.send("GET", "/recipients", "owner")["items"]
        self.assertNotIn(str(self.ids["private"]), {row["id"] for row in hidden})
        conversation = self.open()
        self.message(conversation)
        self.change("agent", role="owner")
        self.send("GET", f"/conversations/{conversation}/entries", "agent", expected=404)
        self.assertEqual(self.send("GET", f"/conversations/{conversation}")["read_only_reason"], "assignment_changed")
        self.send("POST", "/conversations", body=self.command(recipient_id=str(self.ids["agent"])), expected=201)
        # Opening retained own history never makes a now-ineligible pair writable.
        self.send("POST", f"/conversations/{conversation}/entries", body=self.command(kind="message", body="No grant"), expected=409)

    def test_idempotency_is_actor_action_context_and_payload_bound_and_audited(self):
        legacy = self.legacy_snapshot()
        conversation = self.open()
        body, identifier = self.message(conversation)
        again = self.send("POST", f"/conversations/{conversation}/entries", body=body, expected=201)
        self.assertEqual(again["target_id"], identifier)
        self.assertEqual(self.send("GET", "/commands/"+body["submission_id"]), again)
        self.send("POST", f"/conversations/{conversation}/entries", body={**body, "body": "Different"}, expected=409)
        self.send("PUT", f"/conversations/{conversation}/read", body={"submission_id": body["submission_id"], "last_read_sequence": 1}, expected=409)
        self.send("GET", "/commands/"+body["submission_id"], "agent", expected=404)
        other = self.open(recipient="admin")
        self.send("POST", f"/conversations/{other}/entries", body=body, expected=409)
        agent_body = {**body, "body": "Other actor same UUID"}
        self.send("POST", f"/conversations/{conversation}/entries", "agent", agent_body, expected=201)
        with Session(self.engine) as db:
            self.assertEqual(db.query(Entry).filter_by(conversation_id=UUID(conversation)).count(), 2)
            self.assertEqual(db.query(Command).count(), db.query(Audit).count())
            audit = db.query(Audit).filter_by(actor_id=self.ids["buyer"], submission_id=UUID(body["submission_id"])).one()
            self.assertEqual((audit.target_id, audit.result_sequence, audit.actor_role), (UUID(identifier), 1, "buyer"))
        self.assertEqual(self.legacy_snapshot(), legacy)

    def test_nonbuyer_optin_versioning_blocks_and_retained_history(self):
        self.send("POST", "/conversations", "owner", self.command(recipient_id=str(self.ids["private"])), expected=404)
        value = self.send("PUT", "/preferences", "private", self.command(expected_version=0, visibility="members"))
        self.receipt(value, "visibility_saved")
        self.send("PUT", "/preferences", "private", self.command(expected_version=0, visibility="members"), expected=409)
        conversation = self.open("owner", "private")
        self.message(conversation, "owner")
        self.send("PUT", "/preferences", "private", self.command(expected_version=1, visibility="private"))
        self.message(conversation, "private")
        block = self.command(blocked=True)
        self.send("PUT", "/blocks/"+str(self.ids["owner"]), "private", block)
        self.send("PUT", "/blocks/"+str(self.ids["owner"]), "private", block)
        self.assertEqual(len(self.send("GET", "/blocks", "private")["items"]), 1)
        for actor in ("owner", "private"):
            self.assertEqual(self.send("GET", f"/conversations/{conversation}/entries", actor)["total"], 2)
            self.send("POST", f"/conversations/{conversation}/entries", actor,
                self.command(kind="message", body="Blocked"), expected=409)
        self.send("PUT", "/blocks/"+str(self.ids["owner"]), "private", self.command(blocked=False))
        self.message(conversation, "owner")
        with Session(self.engine) as db:
            db.add(NetworkBlock(blocker_id=self.ids["owner"], blocked_id=self.ids["private"]))
            db.commit()
        self.send("PUT", "/blocks/"+str(self.ids["private"]), "owner", self.command(blocked=False))
        self.assertEqual(self.send("GET", f"/conversations/{conversation}", "owner")["read_only_reason"], "blocked")

    def test_native_history_keysets_survive_new_arrivals_and_cursors_bind_resource(self):
        conversation = self.open()
        for _ in range(35):
            self.message(conversation)
        first = self.send("GET", f"/conversations/{conversation}/entries")
        self.assertEqual([row["sequence"] for row in first["items"]], list(range(35, 5, -1)))
        self.message(conversation)
        older = self.send("GET", f"/conversations/{conversation}/entries?cursor="+first["next_cursor"])
        self.assertEqual([row["sequence"] for row in older["items"]], [5, 4, 3, 2, 1])
        self.assertIsNone(older["next_cursor"])
        self.assertEqual(older["total"], 36)
        other = self.open(recipient="admin")
        self.send("GET", f"/conversations/{other}/entries?cursor="+first["next_cursor"], expected=422)
        for cursor in ("bad", first["next_cursor"]+"=", "x"*513):
            self.send("GET", f"/conversations/{conversation}/entries?cursor="+cursor, expected=422)
        source = service._source("native", conversation)
        # No arbitrary page=1000 or 30k ceiling exists; a large valid keyset is accepted.
        deep = service._cursor(source, {"sequence": 30001})
        self.assertEqual(self.send("GET", f"/conversations/{conversation}/entries?cursor="+deep)["total"], 36)
        boolean = service._cursor(source, {"sequence": True})
        self.send("GET", f"/conversations/{conversation}/entries?cursor="+boolean, expected=422)

    def test_unread_explicit_monotonic_cursor_and_self_entries(self):
        conversation = self.open()
        self.message(conversation)
        self.message(conversation, "agent")
        self.message(conversation, "agent")
        self.assertEqual(self.send("GET", "/unread"), {"conversations": 1, "entries": 2})
        self.send("GET", f"/conversations/{conversation}/entries")
        self.assertEqual(self.send("GET", "/unread")["entries"], 2)
        self.send("PUT", f"/conversations/{conversation}/read", body=self.command(last_read_sequence=2))
        self.assertEqual(self.send("GET", "/unread")["entries"], 1)
        self.send("PUT", f"/conversations/{conversation}/read", body=self.command(last_read_sequence=1))
        self.assertEqual(self.send("GET", "/unread")["entries"], 1)
        self.send("PUT", f"/conversations/{conversation}/read", body=self.command(last_read_sequence=4), expected=422)
        self.send("PUT", f"/conversations/{conversation}/read", "admin", self.command(last_read_sequence=3), expected=404)
        self.assertEqual(self.send("GET", "/unread", "admin"), {"conversations": 0, "entries": 0})

    def review(self, source, actor="admin"):
        return self.receipt(self.send("POST", "/admin/reviews", actor,
            self.command(source=source, reason="oversight"), expected=201), "review_opened")

    def test_admin_native_review_requires_own_current_audit_and_replies_as_admin(self):
        conversation = self.open()
        self.message(conversation)
        oversight = self.send("GET", "/conversations?view=oversight", "admin")["items"][0]
        self.assertIsNone(oversight["last_entry_preview"])
        self.assertEqual(oversight["read_only_reason"], "review_required")
        self.send("GET", f"/conversations/{conversation}/entries", "admin", expected=404)
        self.send("POST", f"/conversations/{conversation}/entries", "admin", self.command(kind="message", body="No review"), expected=404)
        review = self.review(service._source("native", conversation))
        page = self.send("GET", f"/admin/reviews/{review}/entries", "admin")
        self.assertFalse(page["read_only"])
        self.assertEqual(page["items"][0]["author"]["id"], str(self.ids["buyer"]))
        self.send("GET", f"/admin/reviews/{review}/entries", "other_admin", expected=404)
        body, identifier = self.message(conversation, "admin", review_id=review)
        entry = self.send("GET", f"/conversations/{conversation}/entries")["items"][0]
        self.assertEqual((entry["author"]["id"], entry["author"]["role"]), (str(self.ids["admin"]), "admin"))
        with Session(self.engine) as db:
            audit = db.query(Audit).filter_by(target_id=UUID(identifier), action="entry_created").one()
            self.assertEqual(audit.review_id, UUID(review))
        with patch.object(service, "now", return_value=datetime.now(timezone.utc)+timedelta(minutes=11)):
            self.send("GET", f"/admin/reviews/{review}/entries", "admin", expected=410)
            self.send("POST", f"/conversations/{conversation}/entries", "admin",
                self.command(kind="message", body="Expired", review_id=review), expected=410)
        self.change("admin", role="owner")
        self.send("GET", f"/admin/reviews/{review}/entries", "admin", expected=404)
        self.send("GET", "/commands/"+body["submission_id"], "admin", expected=404)

    def test_manual_channel_records_and_plaintext_bounds_never_claim_delivery(self):
        conversation = self.open()
        for kind in ("manual_whatsapp", "manual_call"):
            body = self.command(kind=kind, body="Manually recorded external contact",
                occurred_at=datetime.now(timezone.utc).isoformat(), reported_direction="outgoing",
                **({"call_outcome": "voicemail"} if kind=="manual_call" else {}))
            self.send("POST", f"/conversations/{conversation}/entries", body=body, expected=201)
        page = self.send("GET", f"/conversations/{conversation}/entries")
        self.assertEqual({row["delivery_state"] for row in page["items"]}, {"manual_record"})
        for row in page["items"]:
            self.assertEqual(row["author"]["id"], str(self.ids["buyer"]))
        for fields in ({"kind": "manual_call"}, {"kind": "manual_whatsapp"},
                {"occurred_at": datetime.now(timezone.utc).isoformat()}, {"body": "x\x00"},
                {"body": "x\x91"}, {"body": "\ud800"}, {"body": "é"*2001}, {"body": " \t\r\n "},
                {"kind": "message", "author_id": str(self.ids["admin"])},
                {"kind": "manual_call", "occurred_at": "2026-01-01", "reported_direction": "outgoing", "call_outcome": "connected"}):
            body = {**self.command(kind="message", body="Bounded"), **fields}
            self.send("POST", f"/conversations/{conversation}/entries", body=body, expected=422)
        self.send("POST", f"/conversations/{conversation}/entries", body=self.command(kind="manual_whatsapp", body="Future",
            occurred_at=(datetime.now(timezone.utc)+timedelta(minutes=6)).isoformat(), reported_direction="incoming"), expected=422)
        unicode_body = self.command(kind="message", body="é"*2000)
        self.send("POST", f"/conversations/{conversation}/entries", body=unicode_body, expected=201)

    def seed_legacy(self):
        stamp, listing_id, project_id, request_id, negotiation_id, recipient_id = datetime.now(timezone.utc), uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
        with Session(self.engine) as db:
            db.add(Listing(id=listing_id, title="Private listing title", type="villa", transaction="sale", price=1,
                location="PRIVATE-PRECISE-LOCATION", owner_id=self.ids["owner"], submitted_by=self.ids["owner"], status="active"))
            db.flush()
            db.add(NetworkProject(id=project_id, listing_id=listing_id, owner_id=self.ids["owner"],
                title="Private project title", summary="Private", property_class="house_villa", service_category_ids=["pool_service"], status="open", version=1))
            db.flush()
            for identifier, status, project in ((request_id, "awarded", project_id), (negotiation_id, "open", None)):
                db.add(ServiceRequest(id=identifier, listing_id=listing_id, custodian_id=self.ids["owner"], requester_id=self.ids["owner"],
                    category_id="pool_service", property_class="house_villa", title="Private service title", brief="Private brief",
                    area_label="Private area", requester_alias="Owner alias", recipient_user_codes=[8109], scope_sha256="a"*64,
                    status=status, version=1, project_id=project))
            db.flush()
            db.add(Recipient(id=recipient_id, request_id=negotiation_id, provider_id=self.ids["professional"], user_code=8109,
                member_alias="Provider alias", status="accepted", version=1))
            db.flush()
            for index in range(35):
                db.add(AwardMessage(id=UUID(int=index+1), request_id=request_id, sender_id=self.ids["owner"],
                    submission_id=uuid4(), sender_role="owner", body="Private awarded history "+str(index), created_at=stamp))
                db.add(NegotiationMessage(id=UUID(int=index+101), request_id=negotiation_id, recipient_id=recipient_id,
                    sender_id=self.ids["professional"], submission_id=uuid4(), sender_role="provider", body="Private negotiation "+str(index), created_at=stamp))
            db.commit()
        return service._source("service_award", request_id), service._source("service_negotiation", negotiation_id, recipient_id)

    def test_admin_both_legacy_sources_keyset_ties_read_only_and_no_old_grant_expansion(self):
        sources = self.seed_legacy()
        before = self.legacy_snapshot()
        metadata = self.send("GET", "/admin/legacy", "admin")
        self.assertEqual(metadata["total"], 2)
        self.assertNotIn("Private", json.dumps(metadata))
        for source in sources:
            review = self.review(source)
            first = self.send("GET", f"/admin/reviews/{review}/entries", "admin")
            self.assertEqual((first["total"], len(first["items"])), (35, 30))
            self.assertEqual(first["read_only_reason"], "legacy_read_only")
            self.assertTrue(all(row["delivery_state"]=="legacy_recorded" for row in first["items"]))
            fresh_review = self.review(source)
            older = self.send("GET", f"/admin/reviews/{fresh_review}/entries?cursor="+first["next_cursor"], "admin")
            self.assertEqual(len(older["items"]), 5)
            self.assertFalse({row["id"] for row in first["items"]}&{row["id"] for row in older["items"]})
            self.send("GET", f"/admin/reviews/{review}/entries", "owner", expected=404)
            response = self.client.post(f"/messaging/admin/reviews/{review}/entries", headers=self.headers["admin"],
                json=self.command(kind="message", body="Never"))
            self.assertEqual(response.status_code, 405, response.text)
            legacy_path = "/property-network/service-requests/"+source["id"]
            if source["recipient_id"]:
                legacy_path += "/recipients/"+source["recipient_id"]
            response = self.client.get(legacy_path+"/conversation", headers=self.headers["admin"])
            self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.legacy_snapshot(), before)

    def legacy_snapshot(self):
        with self.engine.connect() as db:
            names = [name for name in Base.metadata.tables if not name.startswith("inbox_")]
            return {name: db.execute(text(f'SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),\'[]\'::jsonb)::text FROM "{name}" t')).scalar()
                    for name in names}

    def test_simultaneous_sends_and_matching_retry_have_one_sequence_and_audit_each(self):
        conversation = self.open()
        barrier = threading.Barrier(2)
        payload = self.command(kind="message", body="Exactly one concurrent retry")

        def send(body, actor):
            barrier.wait(timeout=5)
            with TestClient(self.app) as client:
                return client.post(f"/messaging/conversations/{conversation}/entries", headers=self.headers[actor], json=body)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda actor: send(payload, actor), ("buyer", "buyer")))
        self.assertEqual([row.status_code for row in results], [201, 201])
        self.assertEqual(results[0].json(), results[1].json())
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(send, self.command(kind="message", body="Parallel "+actor), actor) for actor in ("buyer", "agent")]
            self.assertEqual([future.result().status_code for future in futures], [201, 201])
        page = self.send("GET", f"/conversations/{conversation}/entries")
        self.assertEqual([row["sequence"] for row in page["items"]], [3, 2, 1])
        with Session(self.engine) as db:
            self.assertEqual(db.query(Entry).count(), 3)
            self.assertEqual(db.query(Command).count(), db.query(Audit).count())

    def test_committed_reassignment_wins_over_a_waiting_old_agent_send(self):
        conversation = self.open()
        attempted = threading.Event()
        original = service._users

        def observed(db, identifiers, **kwargs):
            if kwargs.get("write") and self.ids["agent"] in identifiers:
                attempted.set()
            return original(db, identifiers, **kwargs)

        def send_after_lock():
            with TestClient(self.app) as client:
                return client.post(f"/messaging/conversations/{conversation}/entries", headers=self.headers["agent"],
                    json=self.command(kind="message", body="Old agent must not send after reassignment"))

        with Session(self.engine) as assignment, ThreadPoolExecutor(max_workers=1) as pool:
            buyer = assignment.query(User).filter_by(id=self.ids["buyer"]).with_for_update().one()
            buyer.assigned_realtor_id = self.ids["new_agent"]
            assignment.flush()
            with patch.object(service, "_users", side_effect=observed):
                future = pool.submit(send_after_lock)
                self.assertTrue(attempted.wait(5))
                assignment.commit()
                response = future.result(timeout=10)
            self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.send("GET", f"/conversations/{conversation}/entries")["total"], 0)

    def test_user_write_lock_allows_legacy_block_foreign_key_without_deadlock(self):
        conversation = self.open("owner", "member")
        low, high = sorted((self.ids["owner"], self.ids["member"]), key=str)
        # Reproduce the lock order at the potential legacy/new boundary: a
        # legacy blocker holds its own User while a new sender locks the lower
        # target first. FK KEY SHARE must remain compatible with that target.
        with Session(self.engine) as legacy, Session(self.engine) as sender:
            legacy.query(User).filter_by(id=high).with_for_update().one()
            service._users(sender, {low}, write=True)
            legacy.add(NetworkBlock(blocker_id=high, blocked_id=low))
            legacy.commit()
            sender.rollback()
        self.assertEqual(self.send("GET", f"/conversations/{conversation}", "owner")["read_only_reason"], "blocked")

    def test_cookie_mutation_origin_errors_are_private_and_do_not_echo_body(self):
        token = self.headers["buyer"]["Authorization"].split(" ", 1)[1]
        with TestClient(self.app) as client:
            client.cookies.set("ildr_token", token)
            for origin in (None, "https://untrusted.invalid"):
                response = client.post("/messaging/conversations", headers={"Origin": origin} if origin else {},
                    json=self.command(recipient_id=str(self.ids["agent"])))
                self.assertEqual(response.status_code, 403, response.text)
                self.assertEqual(response.headers["cache-control"], "no-store")
            response = client.post("/messaging/conversations", headers={"Origin": "http://testserver"},
                json=self.command(recipient_id=str(self.ids["agent"])))
            self.assertEqual(response.status_code, 201, response.text)
        response = self.client.put("/messaging/preferences", headers={**self.headers["buyer"], "Content-Type": "text/plain"}, content="PRIVATE-INVALID-BODY")
        self.assertEqual(response.status_code, 415)
        self.assertNotIn("PRIVATE-INVALID-BODY", response.text)

    def test_database_private_rls_model_parity_audit_atomicity_and_retention(self):
        conversation = self.open()
        self.message(conversation)
        with self.engine.connect() as db:
            flags = db.execute(text("SELECT relname,relrowsecurity FROM pg_class WHERE relnamespace=cast(:schema AS regnamespace) AND relname=ANY(:names)"),
                {"schema": self.schema, "names": list(self.revision.TABLES)}).all()
            self.assertEqual(len(flags), 7)
            self.assertTrue(all(row.relrowsecurity for row in flags))
            self.assertEqual(db.execute(text("SELECT count(*) FROM pg_policy WHERE polrelid IN (SELECT oid FROM pg_class WHERE relnamespace=cast(:schema AS regnamespace))")).scalar(), 0)
            for name in self.revision.TABLES:
                actual = inspect(db).get_columns(name, schema=self.schema)
                expected = Base.metadata.tables[name].columns
                self.assertEqual([col["name"] for col in actual], list(expected.keys()))
                for column in actual:
                    self.assertEqual(column["nullable"], expected[column["name"]].nullable)
                public_acl = db.execute(text("SELECT count(*) FROM pg_class c,LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a WHERE c.oid=cast(:name AS regclass) AND a.grantee=0"), {"name": name}).scalar()
                self.assertEqual(public_acl, 0)
            for role in ("anon", "authenticated", "service_role"):
                if db.execute(text("SELECT EXISTS(SELECT FROM pg_roles WHERE rolname=:role)"), {"role": role}).scalar():
                    for name in self.revision.TABLES:
                        self.assertFalse(db.execute(text("SELECT has_table_privilege(:role,:name,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"), {"role": role, "name": name}).scalar())
        for table in ("inbox_entries", "inbox_commands", "inbox_audit", "inbox_conversations"):
            with self.assertRaises(DBAPIError), self.engine.begin() as db:
                db.execute(text(f"DELETE FROM {table}"))
        with self.assertRaises(DBAPIError), self.engine.begin() as db:
            db.execute(text("UPDATE inbox_entries SET body='Changed'"))
        with self.assertRaises(DBAPIError), self.engine.begin() as db:
            db.execute(text("INSERT INTO inbox_commands(actor_id,submission_id,action,payload_sha256,target_id) VALUES(:actor,:submission,'visibility_saved',:digest,:actor)"),
                {"actor": self.ids["buyer"], "submission": uuid4(), "digest": "b"*64})
        for kind, body, occurred, direction, constraint in (
                ("manual_whatsapp", "Missing manual context", None, None, "ck_inbox_entry_kind"),
                ("message", "Unsafe\x91control", None, None, "ck_inbox_entry_body"),
                ("manual_whatsapp", "Future timestamp", datetime.now(timezone.utc)+timedelta(days=1), "outgoing", "ck_inbox_entry_time")):
            submission, identifier = uuid4(), uuid4()
            with self.assertRaises(DBAPIError) as caught, self.engine.begin() as db:
                db.execute(text("UPDATE inbox_conversations SET last_sequence=last_sequence+1,last_entry_at=now() WHERE id=:id"), {"id": UUID(conversation)})
                db.execute(text("INSERT INTO inbox_commands(actor_id,submission_id,action,payload_sha256,target_id,conversation_id) VALUES(:actor,:submission,'entry_created',:digest,:target,:conversation)"),
                    {"actor": self.ids["buyer"], "submission": submission, "digest": "b"*64, "target": identifier, "conversation": UUID(conversation)})
                db.execute(text("INSERT INTO inbox_entries(id,conversation_id,sequence,author_id,submission_id,author_role,kind,body,occurred_at,reported_direction) VALUES(:id,:conversation,2,:actor,:submission,'buyer',:kind,:body,:occurred,:direction)"),
                    {"id": identifier, "conversation": UUID(conversation), "actor": self.ids["buyer"], "submission": submission,
                     "kind": kind, "body": body, "occurred": occurred, "direction": direction})
            self.assertEqual(caught.exception.orig.diag.constraint_name, constraint)
        with self.assertRaises(DBAPIError), self.engine.begin() as db:
            self.revision.op = Operations(MigrationContext.configure(db))
            self.revision.downgrade()
        # A repeat upgrade retains populated histories and leaves all seven private.
        with self.engine.begin() as db:
            self.revision.op = Operations(MigrationContext.configure(db))
            self.revision.upgrade()
        self.assertEqual(self.send("GET", f"/conversations/{conversation}/entries")["total"], 1)


if __name__ == "__main__":
    unittest.main()
