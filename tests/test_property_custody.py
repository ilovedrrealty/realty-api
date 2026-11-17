"""Private registry custody against native migrations and real application routes."""
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

from models.property_custody import NetworkPropertyCustodyAction as Receipt, NetworkPropertyCustodyTransfer as Transfer
from models.property_network import NetworkProject as Project, NetworkProperty as Property
from models.service_workflow import NetworkServiceRequest as RequestRow
from services import property_custody as custody


BASE = "/registry-custody"
LABEL = "Handoff of the private service property"
NOTES = "PRIVATE-NOTES"
FIELD_KEYS = {"title", "location", "address", "property_class", "relationship"}


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class PrivatePropertyCustody(unittest.TestCase):
    _workflow = workflow_fixture.PrivateServiceWorkflow
    _visit = visit_fixture.PrivateAwardedServiceWork
    cleanup_owned_schema = classmethod(_workflow.cleanup_owned_schema.__func__)
    patched = _workflow.patched
    stop_patches = _workflow.stop_patches
    send = _workflow.send
    ok = _workflow.ok
    private = _workflow.private
    parallel = _workflow.parallel
    opt = _workflow.opt
    request_body = _workflow.request_body
    request = _workflow.request
    received = _workflow.received
    accepted = _workflow.accepted
    terms = _workflow.terms
    quote = _workflow.quote
    award_body = _workflow.award_body
    assert_private = _visit.assert_private
    assert_refused = _visit.assert_refused

    @classmethod
    def setUpClass(cls):
        cls._visit.setUpClass.__func__(cls)
        for filename in ("e64a91f327bc_private_property_history_shares.py", "f4b27d6e913c_private_registry_custody.py"):
            path = Path(__file__).resolve().parents[1] / "alembic/versions" / filename
            spec = importlib.util.spec_from_file_location("custody_fixture_" + filename[:-3], path)
            revision = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(revision)
            with cls.engine.begin() as db:
                revision.op = Operations(MigrationContext.configure(db))
                revision.upgrade()
        cls.custody_revision = revision

    def setUp(self):
        self._workflow.setUp(self)
        self.clock = custody.network.now()
        self.patched(custody.network, "now", side_effect=lambda: self.clock)

    def call(self, method, path=BASE, actor="owner", body=None, status=200):
        response = self.send(method, path, actor, body)
        self.assert_private(response)
        if status >= 400:
            self.assert_refused(response, status)
        return self.ok(response, status)

    def proposal(self, property, recipient="pro1", **changes):
        return {"submission_id": str(uuid4()), "property_id": property["id"], "expected_property_version": property["version"],
                "recipient_user_code": self.codes[recipient], "label": LABEL, "transfer_acknowledged": True, **changes}

    def offer(self, property, actor="owner", recipient="pro1", **changes):
        body = self.proposal(property, recipient, **changes)
        return self.call("POST", actor=actor, body=body, status=201), body

    def decision(self, operation):
        return {"submission_id": str(uuid4()), "expected_version": 1, "operation": operation,
                **({"transfer_acknowledged": True} if operation == "accept" else {})}

    def act(self, transfer, actor="pro1", operation="accept", status=200, body=None):
        return self.call("POST", f"{BASE}/{transfer['id']}/actions", actor, body or self.decision(operation), status)

    def options(self, property, actor="owner", status=200):
        return self.call("GET", f"{BASE}/options/{property['id']}", actor, status=status)

    def edit(self, property, actor="owner", **changes):
        body = {key: property[key] for key in FIELD_KEYS | {"status", "notes"}}
        return self.call("PUT", f"/registry/{property['id']}", actor, {**body, "expected_version": property["version"], **changes})

    def project(self, property, actor="owner"):
        return self.call("POST", "/projects", actor, {"property_id": property["id"], "submission_id": str(uuid4()),
            "title": "Synthetic custody project", "property_class": property["property_class"], "service_category_ids": ["pool_service"]}, 201)

    def close_project(self, project, state="completed"):
        return self.call("PUT", f"/projects/{project['id']}", body={"expected_version": project["version"],
            "title": project["title"], "summary": project["summary"], "status": state})

    def test_transfer_preserves_exact_fields_notes_and_ownership_boundary(self):
        created = {"submission_id": str(uuid4()), "title": "Private transfer property", "location": "Private general area",
            "address": "Private street", "notes": NOTES, "property_class": "house_villa", "relationship": "manager",
            "authority_acknowledged": True}
        original = self.call("POST", "/registry", body=created, status=201)
        options = self.options(original)
        acknowledged = {key: original[key] for key in FIELD_KEYS}
        self.assertEqual(options["fields"], acknowledged)
        self.assertNotIn(NOTES, str(options))
        for actor in ("admin", "pro1", "outsider"):
            self.options(original, actor, 404)
        transfer, proposal = self.offer(original)
        pending = self.call("GET", f"{BASE}/{transfer['id']}", "pro1")
        self.assertEqual((pending["status"], pending["fields"], pending["property_id"], pending["actions"]),
                         ("pending", None, None, ["accept", "decline"]))
        self.assertEqual(self.call("GET", BASE + "?view=received", "pro1")["items"][0], pending)
        self.call("GET", f"{BASE}/{transfer['id']}", "outsider", status=404)
        self.call("GET", f"{BASE}/{transfer['id']}", "admin", status=404)
        self.call("GET", f"/registry/{original['id']}", "pro1", status=404)
        self.act(transfer, "owner", status=409)
        accepted = self.act(transfer)
        self.assertEqual(accepted, {"id": transfer["id"], "operation": "accept", "status": "accepted", "version": 2})
        now = self.call("GET", f"/registry/{original['id']}", "pro1")
        self.assertEqual((now["version"], now["notes"], {key: now[key] for key in FIELD_KEYS}), (original["version"] + 1, "", acknowledged))
        self.assertEqual(self.call("GET", f"{BASE}/{transfer['id']}", "pro1")["fields"], acknowledged)
        self.assertIsNone(self.call("GET", BASE + "?view=received", "pro1")["items"][0]["fields"])
        self.call("GET", f"/registry/{original['id']}", status=404)
        self.call("GET", f"/registry/submissions/{created['submission_id']}", status=404)
        self.call("POST", "/registry", body=created, status=404)
        self.assertEqual(self.call("GET", f"{BASE}/submissions/{proposal['submission_id']}"), transfer)
        self.call("GET", f"{BASE}/submissions/{proposal['submission_id']}", "pro1", status=404)
        self.assertEqual(self.project(now, "pro1")["my_role"], "owner")
        with Session(self.engine) as db:
            row = db.get(Property, UUID(now["id"]))
            saved = db.get(Transfer, UUID(transfer["id"]))
            self.assertEqual((row.custodian_id, row.current_custodian_id, row.custody_version, saved.prior_notes),
                (self.ids["owner"], self.ids["pro1"], 2, NOTES))

    def test_live_work_blocks_proposal_and_stale_property_blocks_acceptance(self):
        original = self.private()
        project = self.project(original)
        self.assertEqual(self.options(original)["blockers"], {"active_projects": 1, "open_requests": 0, "unfinished_awards": 0})
        self.call("POST", body=self.proposal(original), status=409)
        self.close_project(project)
        request, _ = self.request(original)
        self.assertEqual(self.options(original)["blockers"]["open_requests"], 1)
        self.call("POST", body=self.proposal(original), status=409)
        self.call("POST", f"/service-requests/{request['id']}/cancel", body={"submission_id": str(uuid4()), "expected_request_version": 1})
        transfer, _ = self.offer(original)
        edited = self.edit(original, address="New unacknowledged street", notes="New private notes")
        self.act(transfer, status=409)
        self.act(transfer, "owner", "cancel")
        linked, _ = self.request(edited)
        self.quote(linked)
        award = self.call("POST", f"/service-requests/{linked['id']}/award", body=self.award_body(linked))
        self.close_project(award["project"])
        self.assertEqual(self.options(edited)["blockers"], {"active_projects": 0, "open_requests": 0, "unfinished_awards": 1})
        self.call("POST", body=self.proposal(edited), status=409)

    def test_cancel_decline_expiry_and_idempotent_actor_receipts(self):
        property = self.private()
        for invalid in ({"transfer_acknowledged": False}, {"recipient_user_code": True}, {"label": ""}):
            self.call("POST", body=self.proposal(property, **invalid), status=422)
        for role in ("owner", "admin", "suspended", "unverified"):
            self.call("POST", body=self.proposal(property, role), status=404)
        first, body = self.offer(property)
        self.assertEqual(self.call("POST", body=body, status=201), first)
        self.call("POST", body={**body, "label": "Another label"}, status=409)
        self.call("POST", body=self.proposal(property, "pro2"), status=409)
        self.act(first, body={**self.decision("accept"), "transfer_acknowledged": False}, status=422)
        decision = self.decision("decline")
        declined = self.act(first, body=decision)
        self.assertEqual(self.act(first, body=decision), declined)
        self.assertEqual(self.call("GET", f"{BASE}/submissions/{decision['submission_id']}", "pro1"), declined)
        self.call("GET", f"{BASE}/submissions/{decision['submission_id']}", "owner", status=404)
        second, _ = self.offer(property, recipient="pro2")
        self.clock += timedelta(days=7)
        expired = self.call("GET", f"{BASE}/{second['id']}", "pro2")
        self.assertEqual((expired["status"], expired["version"], expired["actions"], expired["fields"]), ("expired", 1, [], None))
        self.act(second, "pro2", status=409)
        third, _ = self.offer(property)
        self.assertEqual(self.act(third, "owner", "cancel")["status"], "cancelled")
        with Session(self.engine) as db:
            self.assertEqual(db.get(Transfer, UUID(second["id"])).status, "expired")

    def test_original_project_and_service_rows_stay_closed_across_return(self):
        property = self.private()
        project = self.close_project(self.project(property))
        request, _ = self.request(property)
        self.call("POST", f"/service-requests/{request['id']}/cancel", body={"submission_id": str(uuid4()), "expected_request_version": 1})
        self.call("PUT", f"/maintenance-reminders/private/{property['id']}/preferences", body={
            "submission_id": str(uuid4()), "expected_version": 0, "enabled": True, "lead_days": 7})
        transfer, _ = self.offer(property)
        self.act(transfer)
        original_project = self.call("GET", f"/projects/{project['id']}")
        self.assertFalse(original_project["can_update"] or original_project["can_assign"] or original_project["can_manage"])
        self.call("GET", f"/projects/{project['id']}", "pro1", status=404)
        next_property = self.call("GET", f"/registry/{property['id']}", "pro1")
        self.edit(next_property, "pro1", title="Successor private property title", notes="Successor only notes")
        retained = self.call("GET", f"/projects/{project['id']}")
        self.assertEqual(retained["property_title"], property["title"])
        next_property = self.call("GET", f"/registry/{property['id']}", "pro1")
        returned, _ = self.offer(next_property, actor="pro1", recipient="owner")
        self.act(returned, "owner")
        self.assertIsNone(self.call("GET", f"{BASE}/{transfer['id']}", "pro1")["fields"])
        reminders = self.call("GET", f"/maintenance-reminders/private/{property['id']}")
        self.assertEqual(reminders["preference"], {"enabled": False, "lead_days": 7, "version": 0})
        with Session(self.engine) as db:
            old_req = db.get(RequestRow, UUID(request["id"]))
            old_project = db.get(Project, UUID(project["id"]))
            self.assertEqual((old_req.status, old_req.version, old_req.custody_closed), ("cancelled", 2, True))
            self.assertEqual((old_project.closed_custody_title, old_project.version), (property["title"], project["version"]))
            self.assertEqual(db.get(Property, UUID(property["id"])).custody_version, 3)

    def test_racing_consent_and_native_retention_guards(self):
        property_id = uuid4()
        with self.engine.begin() as db:
            self.custody_revision.op = Operations(MigrationContext.configure(db))
            self.custody_revision.downgrade()
            insert_legacy = text("""INSERT INTO network_properties (id,custodian_id,submission_id,title,location,address,
                property_class,relationship,notes,status,version,creation_sha256,authority_acknowledged)
                VALUES (:id,:owner,:key,'Legacy private property','Area','','house_villa','owner','','active',1,:digest,true)""")
            inputs = {"id": property_id, "owner": self.ids["owner"], "key": uuid4(), "digest": "a" * 64}
            db.execute(insert_legacy, inputs)
            self.custody_revision.upgrade()
            inserted = uuid4()
            db.execute(insert_legacy, {**inputs, "id": inserted, "key": uuid4()})
            restored = db.execute(text("""SELECT id,current_custodian_id,custody_version
                FROM network_properties WHERE id IN (:upgraded,:inserted)"""),
                {"upgraded": property_id, "inserted": inserted}).all()
            self.assertEqual({tuple(row) for row in restored}, {(property_id, self.ids["owner"], 1), (inserted, self.ids["owner"], 1)})
        property = self.call("GET", f"/registry/{property_id}")
        transfer, _ = self.offer(property)
        with self.assertRaises(DBAPIError) as error, self.engine.begin() as db:
            db.execute(text("""UPDATE network_property_custody_transfers SET status='accepted',version=2,finished_at=:instant
                WHERE id=:id"""), {"instant": self.clock, "id": UUID(transfer["id"])})
        self.assertEqual(error.exception.orig.pgcode, "55000")
        path = f"{BASE}/{transfer['id']}/actions"
        accept, cancel = self.decision("accept"), self.decision("cancel")
        results = self.parallel([lambda: self.send("POST", path, "pro1", accept), lambda: self.send("POST", path, "owner", cancel)])
        self.assertEqual(sorted(row.status_code for row in results), [200, 409])
        self.assertIn(next(row.json()["status"] for row in results if row.status_code == 200), {"accepted", "cancelled"})
        with Session(self.engine) as db:
            self.assertEqual((db.query(Transfer).count(), db.query(Receipt).count()), (1, 2))
            acl = db.execute(text("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=current_schema() AND c.relname IN ('network_property_custody_transfers','network_property_custody_actions')
                AND c.relrowsecurity AND NOT EXISTS (SELECT FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a WHERE a.grantee=0)""")).scalar_one()
            self.assertEqual(acl, 2)
        for statement in ("DELETE FROM network_property_custody_actions", "UPDATE network_properties SET custody_version=custody_version+1"):
            with self.assertRaises(DBAPIError) as error, self.engine.begin() as db:
                db.execute(text(statement))
            self.assertEqual(error.exception.orig.pgcode, "55000")
        with self.assertRaises(DBAPIError) as error, self.engine.begin() as db:
            self.custody_revision.op = Operations(MigrationContext.configure(db))
            self.custody_revision.downgrade()
        self.assertEqual(error.exception.orig.pgcode, "55000")
