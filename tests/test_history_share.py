"""Private property history consent through real routes on disposable PostgreSQL."""
import importlib.util
import os
import unittest
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import test_service_visit as visit_fixture
import test_service_workflow as workflow_fixture

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from models.history_share import NetworkHistoryShare as Share, NetworkHistoryShareAction as Receipt
from models.listing import Listing
from models.property_network import NetworkProjectUpdate as Update
from services import history_share
from services.property_management import today_dr


BASE = "/history-shares"
SUMMARY = "Synthetic owner-approved pool maintenance summary"
RAW_BODY = "PRIVATE-PROJECT-NOTES-NEVER-SHARED"
SHARE_FIELDS = {"id", "label", "view", "status", "expires_at", "version", "item_count", "items", "actions"}


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class PrivatePropertyHistoryShares(unittest.TestCase):
    _workflow = workflow_fixture.PrivateServiceWorkflow
    cleanup_owned_schema = classmethod(_workflow.cleanup_owned_schema.__func__)
    patched = _workflow.patched
    stop_patches = _workflow.stop_patches
    send = _workflow.send
    ok = _workflow.ok
    parallel = _workflow.parallel
    assert_private = visit_fixture.PrivateAwardedServiceWork.assert_private
    assert_refused = visit_fixture.PrivateAwardedServiceWork.assert_refused

    @classmethod
    def setUpClass(cls):
        cls._workflow.setUpClass.__func__(cls)
        path = Path(__file__).resolve().parents[1] / "alembic/versions/e64a91f327bc_private_property_history_shares.py"
        spec = importlib.util.spec_from_file_location("history_share_fixture_migration", path)
        cls.revision = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.revision)
        with cls.engine.begin() as db:
            cls.revision.op = Operations(MigrationContext.configure(db))
            cls.revision.upgrade()

    def setUp(self):
        self._workflow.setUp(self)
        self.clock = history_share.network.now()
        self.patched(history_share.network, "now", side_effect=lambda: self.clock)

    def call(self, method, path=BASE, actor="owner", body=None, status=200):
        response = self.send(method, path, actor, body)
        self.assert_private(response)
        if status >= 400:
            self.assert_refused(response, status)
            self.assertNotIn(RAW_BODY, response.text)
            return response.json()
        return self.ok(response, status)

    def source(self):
        return self.call("POST", "/registry", body={"submission_id": str(uuid4()), "title": "Synthetic private property",
            "location": "PRIVATE-EXACT-LOCATION", "address": "PRIVATE-STREET", "property_class": "house_villa",
            "relationship": "owner", "notes": "PRIVATE-REGISTRY-NOTES", "authority_acknowledged": True}, status=201)

    def project(self, kind, source_id, title):
        field = "property_id" if kind == "private" else "listing_id"
        return self.call("POST", "/projects", body={field: str(source_id), "submission_id": str(uuid4()),
            "title": title, "summary": "PRIVATE-PROJECT-SUMMARY", "property_class": "house_villa",
            "service_category_ids": ["pool_service"]}, status=201)

    def post(self, project, body=RAW_BODY):
        return self.call("POST", f"/projects/{project['id']}/updates", body={
            "submission_id": str(uuid4()), "body": body, "occurred_on": today_dr().isoformat()}, status=201)

    def set_project_status(self, project, state):
        return self.call("PUT", f"/projects/{project['id']}", body={"expected_version": project["version"],
            "title": project["title"], "summary": project["summary"], "status": state})

    def assign(self, project, actor, role="professional"):
        self.call("POST", f"/projects/{project['id']}/members", body={"expected_version": project["version"],
            "user_code": self.codes[actor], "role": role})
        return self.call("GET", f"/projects/{project['id']}")

    def seed(self, kind="listing"):
        source_id = str(self.listing) if kind == "listing" else self.source()["id"]
        old_project = self.project(kind, source_id, "Synthetic completed past service")
        update = self.post(old_project)
        self.set_project_status(old_project, "completed")
        anchor = self.assign(self.project(kind, source_id, "Synthetic active assignment"), "pro1")
        return source_id, old_project, update, anchor

    def body(self, kind, source_id, update, anchor, **changes):
        return {"submission_id": str(uuid4()), "source_kind": kind, "source_id": source_id,
            "anchor_project_id": anchor["id"], "recipient_user_code": self.codes["pro1"],
            "label": "Selected pool service history", "expires_in_days": 7,
            "items": [{"update_id": update["id"], "summary": SUMMARY}], **changes}

    def create(self, body):
        return self.call("POST", body=body, status=201)

    def action(self, operation, version):
        return {"submission_id": str(uuid4()), "expected_version": version, "operation": operation}

    def change(self, share, body, actor, status=200):
        return self.call("POST", f"{BASE}/{share['id']}/actions", actor, body, status)

    def detail(self, share, actor="owner", status=200):
        result = self.call("GET", f"{BASE}/{share['id']}", actor, status=status)
        if status == 200:
            self.assertEqual(set(result), SHARE_FIELDS)
        return result

    def listed(self, actor, view="received"):
        return self.call("GET", f"{BASE}?view={view}", actor)

    def test_listing_owner_selects_and_only_assigned_recipient_accepts(self):
        source_id, old_project, update, anchor = self.seed()
        anchor = self.assign(anchor, "manager", "manager")
        anchor = self.assign(anchor, "pro2", "viewer")
        picker = f"{BASE}/options/listing/{source_id}?anchor_project_id={anchor['id']}"
        options = self.call("GET", picker)
        self.assertEqual(options["professionals"], [{"user_code": self.codes["pro1"], "display_name": "PRIVATE-ACCOUNT-pro1"}])
        self.assertEqual(options["items"], [{"update_id": update["id"], "project_title": old_project["title"],
            "occurred_on": today_dr().isoformat(), "service_category_ids": ["pool_service"]}])
        for actor in ("pro1", "pro2", "manager", "admin", "outsider"):
            self.call("GET", picker, actor, status=404)
        body = self.body("listing", source_id, update, anchor)
        share = self.create(body)
        self.assertEqual(share, {"id": share["id"], "operation": "create", "status": "pending", "version": 1})
        self.assertIsNone(self.listed("pro1")["items"][0]["items"])
        self.assertEqual(self.detail(share, "pro1")["actions"], ["accept", "decline"])
        self.assertIsNone(self.detail(share, "pro1")["items"])
        for actor in ("manager", "pro2", "admin", "outsider"):
            self.detail(share, actor, 404)
            self.change(share, self.action("accept", 1), actor, 404)
        self.change(share, self.action("accept", 1), "owner", 409)
        self.change(share, self.action("accept", 1), "pro1")
        detail = self.detail(share, "pro1")
        self.assertEqual(detail["items"], [{"occurred_on": today_dr().isoformat(),
            "service_category_ids": ["pool_service"], "summary": SUMMARY}])
        self.assertNotIn(RAW_BODY, str(detail))
        self.call("GET", f"/projects/{old_project['id']}/updates", "pro1", status=404)
        self.call("GET", f"/updates/{update['id']}/comments", "pro1", status=404)
        with Session(self.engine) as db:
            self.assertEqual((db.query(Update).count(), db.get(Update, UUID(update["id"])).body), (1, RAW_BODY))

    def test_registry_rejects_cross_scope_incomplete_and_ineligible_choices(self):
        source_id, _, update, anchor = self.seed("private")
        body = self.body("private", source_id, update, anchor)
        _, _, listing_update, listing_anchor = self.seed()
        fresh_update = self.post(anchor)
        picker = f"{BASE}/options/private/{source_id}?anchor_project_id={anchor['id']}"
        self.assertEqual(self.call("GET", picker)["items"][0]["update_id"], update["id"])
        for change in ({"anchor_project_id": listing_anchor["id"]}, {"recipient_user_code": self.codes["outsider"]},
                       {"recipient_user_code": self.codes["owner"]}, {"recipient_user_code": self.codes["admin"]},
                       {"items": [{"update_id": listing_update["id"], "summary": SUMMARY}]},
                       {"items": [{"update_id": fresh_update["id"], "summary": SUMMARY}]}):
            with self.subTest(change=change):
                self.call("POST", body={**body, **change}, status=404)
        for change in ({"expires_in_days": True}, {"expires_in_days": 2}, {"media_ids": []},
                       {"items": body["items"] * 2}, {"items": [{**body["items"][0], "summary": "bad\x00text"}]}):
            self.call("POST", body={**body, **change}, status=422)
        self.assertEqual(self.listed("owner", "sent")["total"], 0)
        share = self.create(body)
        self.assertEqual(self.change(share, self.action("decline", 1), "pro1")["status"], "declined")
        self.detail(share, "pro1", 404)

    def test_actor_owned_receipts_expiry_and_explicit_revoke(self):
        source_id, _, update, anchor = self.seed()
        body = self.body("listing", source_id, update, anchor, expires_in_days=1)
        share = self.create(body)
        self.assertEqual(self.create(body), share)
        self.call("POST", body={**body, "label": "Changed share"}, status=409)
        self.call("GET", f"{BASE}/submissions/{body['submission_id']}", "pro1", status=404)
        acceptance = self.action("accept", 1)
        accepted = self.change(share, acceptance, "pro1")
        self.assertEqual(self.change(share, acceptance, "pro1"), accepted)
        self.change(share, {**acceptance, "operation": "decline"}, "pro1", 409)
        self.change(share, self.action("revoke", 1), "owner", 409)
        self.change(share, self.action("revoke", 2), "owner")
        self.detail(share, "pro1", 404)
        self.assertEqual(self.call("GET", f"{BASE}/submissions/{acceptance['submission_id']}", "pro1"), accepted)
        self.assertEqual(self.listed("pro1")["items"], [])
        expires = self.create({**body, "submission_id": str(uuid4())})
        self.clock += timedelta(days=1)
        self.assertEqual(self.detail(expires)["status"], "expired")
        self.detail(expires, "pro1", 404)
        self.change(expires, self.action("accept", 1), "pro1", 404)

    def test_revoked_assignment_and_closed_anchor_never_restore_old_access(self):
        source_id, _, update, anchor = self.seed()
        body = self.body("listing", source_id, update, anchor)
        share = self.create(body)
        self.change(share, self.action("accept", 1), "pro1")
        self.call("POST", f"/projects/{anchor['id']}/members/{self.codes['pro1']}/revoke",
                  body={"expected_version": anchor["version"]})
        self.detail(share, "pro1", 404)
        anchor = self.assign(self.call("GET", f"/projects/{anchor['id']}"), "pro1")
        self.detail(share, "pro1", 404)
        replacement = self.create({**body, "submission_id": str(uuid4())})
        self.change(replacement, self.action("accept", 1), "pro1")
        anchor = self.set_project_status(anchor, "completed")
        self.set_project_status(anchor, "open")
        self.detail(replacement, "pro1", 404)

        after_anchor = self.create({**body, "submission_id": str(uuid4())})
        self.change(after_anchor, self.action("accept", 1), "pro1")
        for state in ("archived", "active"):
            with Session(self.engine) as db:
                db.get(Listing, UUID(source_id)).status = state
                db.commit()
        self.detail(after_anchor, "pro1", 404)

    def test_one_concurrent_transition_and_retained_protected_storage(self):
        source_id, _, update, anchor = self.seed()
        share = self.create(self.body("listing", source_id, update, anchor))
        path = f"{BASE}/{share['id']}/actions"
        first = self.action("accept", 1)
        second = self.action("decline", 1)
        results = self.parallel([lambda: self.send("POST", path, "pro1", first),
                                 lambda: self.send("POST", path, "pro1", second)])
        self.assertEqual(sorted(response.status_code for response in results), [200, 404 if any(
            response.status_code == 200 and response.json()["status"] == "declined" for response in results) else 409])
        with Session(self.engine) as db:
            self.assertEqual((db.query(Share).count(), db.query(Receipt).count()), (1, 2))
            self.assertEqual(db.execute(text("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=current_schema() AND c.relname IN ('network_history_shares','network_history_share_actions')
                AND c.relrowsecurity AND NOT EXISTS (SELECT FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a WHERE a.grantee=0)""")).scalar_one(), 2)
        for sql in ("UPDATE network_history_shares SET label='Changed'", "DELETE FROM network_history_share_actions"):
            with self.assertRaises(DBAPIError) as error, self.engine.begin() as db:
                db.execute(text(sql))
            self.assertEqual(error.exception.orig.pgcode, "55000")
        with self.assertRaises(DBAPIError) as error, self.engine.begin() as db:
            self.revision.op = Operations(MigrationContext.configure(db))
            self.revision.downgrade()
        self.assertEqual(error.exception.orig.pgcode, "55000")
