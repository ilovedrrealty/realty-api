"""Private award conversations against a disposable loopback PostgreSQL."""
import base64
import importlib.util
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import test_service_visit as visit_fixture

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from models.listing import Listing
from models.service_conversation import NetworkServiceConversationMessage as Message
from models.user import User
from utils import private_network_media as storage


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAgAAAAGCAIAAABxZ0isAAAAEUlEQVR42mN4kq+CFTEMpAQAvzJGURcKCxAAAAAASUVORK5CYII=")


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class PrivateAwardedServiceConversation(unittest.TestCase):
    _visit = visit_fixture.PrivateAwardedServiceWork
    _workflow = _visit._workflow
    cleanup_owned_schema = classmethod(_workflow.cleanup_owned_schema.__func__)
    setUp = _workflow.setUp
    patched = _workflow.patched
    stop_patches = _workflow.stop_patches
    send = _workflow.send
    ok = _workflow.ok
    opt = _workflow.opt
    private = _workflow.private
    request_body = _workflow.request_body
    request = _workflow.request
    received = _workflow.received
    accepted = _workflow.accepted
    terms = _workflow.terms
    quote = _workflow.quote
    award_body = _workflow.award_body
    parallel = _workflow.parallel
    assert_private = _visit.assert_private
    assert_refused = _visit.assert_refused

    @classmethod
    def setUpClass(cls):
        cls._visit.setUpClass.__func__(cls)
        path = Path(__file__).resolve().parents[1] / "alembic/versions/c6f3a2e8d519_private_awarded_service_conversation.py"
        spec = importlib.util.spec_from_file_location("conversation_fixture_migration", path)
        cls.conversation_revision = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.conversation_revision)
        with cls.engine.begin() as db:
            cls.conversation_revision.op = Operations(MigrationContext.configure(db))
            cls.conversation_revision.upgrade()

    def awarded(self, request=None):
        if request is None:
            request, _ = self.request()
        self.quote(request)
        self.quote(request, "pro2")
        root = f"/service-requests/{request['id']}"
        award = self.ok(self.send("POST", root + "/award", body=self.award_body(request)))
        return root + "/conversation", award["project"]

    def body(self, message="Synthetic award conversation"):
        return {"submission_id": str(uuid4()), "body": message}

    def message(self, response, status=201):
        self.assert_private(response)
        value = self.ok(response, status)
        self.assertEqual(set(value), {"id", "sender", "body", "created_at"})
        self.assertIsNotNone(datetime.fromisoformat(value["created_at"]).tzinfo)
        self.assertEqual(str(UUID(value["id"])), value["id"])
        return value

    def page(self, path, role="owner", number=1):
        response = self.send("GET", f"{path}?page={number}", role)
        self.assert_private(response)
        value = self.ok(response)
        self.assertEqual(set(value), {"request_id", "items", "total", "page", "page_size"})
        self.assertEqual((value["request_id"], value["page"], value["page_size"]), (path.split("/")[2], number, 30))
        return value

    def count_messages(self):
        with Session(self.engine) as db:
            return db.query(Message).count()

    def test_exact_participants_lose_transcript_on_revocation_and_custody_change(self):
        request, _ = self.request()
        self.assert_refused(self.send("GET", f"/service-requests/{request['id']}/conversation"), 404)
        path, project = self.awarded(request)
        self.assertEqual(self.page(path)["items"], [])
        self.assertEqual(self.page(path, "pro1")["items"], [])
        project_path = f"/projects/{project['id']}"
        self.ok(self.send("POST", project_path + "/members", body={
            "expected_version": project["version"], "user_code": self.codes["pro2"], "role": "professional",
        }))
        payload = self.body()
        receipt = f"{path}/submissions/{payload['submission_id']}"
        for role, status in (("outsider", 404), ("pro2", 404), ("admin", 404), ("manager", 404), ("unknown", 401)):
            with self.subTest(role=role):
                self.assert_refused(self.send("GET", path, role), status)
                self.assert_refused(self.send("GET", receipt, role), status)
                self.assert_refused(self.send("POST", path, role, payload), status)
        owner_message = self.message(self.send("POST", path, body=payload))
        self.assertEqual(self.page(path, "pro1")["items"], [owner_message])
        current = self.ok(self.send("GET", project_path))
        self.ok(self.send("POST", f"{project_path}/members/{self.codes['pro1']}/revoke",
                          body={"expected_version": current["version"]}))
        for role in ("owner", "pro1"):
            self.assert_refused(self.send("GET", path, role), 404)
            self.assert_refused(self.send("GET", receipt, role), 404)
            self.assert_refused(self.send("POST", path, role, payload), 404)

        private_request, _ = self.request(self.private())
        archived_path, private_project = self.awarded(private_request)
        private_message = self.message(self.send("POST", archived_path, "pro1", self.body()))
        self.assertEqual(self.page(archived_path)["items"], [private_message])
        self.ok(self.send("PUT", f"/projects/{private_project['id']}", body={
            "expected_version": private_project["version"], "title": private_project["title"],
            "summary": private_project["summary"], "status": "archived",
        }))
        for role in ("owner", "pro1"):
            self.assert_refused(self.send("GET", archived_path, role), 404)
            self.assert_refused(self.send("POST", archived_path, role, self.body()), 404)

        current_path, _ = self.awarded()
        self.message(self.send("POST", current_path, "pro1", self.body()))
        with Session(self.engine) as db:
            db.get(User, self.ids["outsider"]).role = "owner"
            db.get(Listing, self.listing).owner_id = self.ids["outsider"]
            db.commit()
        for role in ("owner", "pro1", "outsider", "admin"):
            self.assert_refused(self.send("GET", current_path, role), 404)
            self.assert_refused(self.send("POST", current_path, role, self.body()), 404)
        self.assertEqual(self.count_messages(), 3)

    def test_message_shape_and_plain_text_validation(self):
        path, _ = self.awarded()
        for value in ("", " \t\r\n ", "\u2003", "hello\x00", "hello\x1f", "hello\x7f", "hello\x91", "é" * 2001, 42):
            with self.subTest(value_type=type(value).__name__, length=len(value) if isinstance(value, str) else None):
                self.assert_refused(self.send("POST", path, body=self.body(value)), 422)
        self.assert_refused(self.send("POST", path, body={**self.body(), "sender_id": str(self.ids["owner"])}), 422)
        self.assert_refused(self.send("GET", path + "?page=0"), 422)
        self.assert_refused(self.send("GET", path + "?page=1001"), 422)
        self.assertEqual(self.count_messages(), 0)
        raw = "  Hola 🛠️\r\n  <b>literal & plain</b>\t "
        item = self.message(self.send("POST", path, body=self.body(raw)))
        self.assertEqual((item["sender"], item["body"]), ("owner", raw.strip()))
        maximum = "é" * 2000
        item = self.message(self.send("POST", path, "pro1", self.body(maximum)))
        self.assertEqual((item["sender"], item["body"]), ("provider", maximum))
        unicode_edges = "\ufeffHola\ufeff"
        self.assertEqual(self.message(self.send("POST", path, body=self.body(unicode_edges)))["body"], unicode_edges)

    def test_idempotency_receipts_and_concurrent_photo_upload(self):
        path, project = self.awarded()
        payload = self.body()
        receipt = f"{path}/submissions/{payload['submission_id']}"
        responses = self.parallel([lambda: self.send("POST", path, body=payload)] * 2)
        first, second = (self.message(response) for response in responses)
        self.assertEqual(first, second)
        self.assertEqual(self.message(self.send("GET", receipt), 200), first)
        self.assert_refused(self.send("GET", receipt, "pro1"), 404)
        provider = self.message(self.send("POST", path, "pro1", payload))
        self.assertNotEqual(first["id"], provider["id"])
        self.assertEqual(self.message(self.send("GET", receipt, "pro1"), 200), provider)
        self.assert_refused(self.send("POST", path, body={**payload, "body": "Changed message"}), 409)
        other, _ = self.awarded()
        self.assert_refused(self.send("POST", other, body=payload), 409)
        self.assert_refused(self.send("GET", f"{other}/submissions/{payload['submission_id']}"), 404)
        self.assertEqual(self.count_messages(), 2)

        self.patched(storage, "store_image", return_value=None)
        photo, conversation = self.parallel([
            lambda: self.client.post("/property-network/media", headers={
                "x-test-role": "pro1", "Authorization": "Bearer synthetic-only",
            }, data={"project_id": project["id"]}, files={"file": ("synthetic.png", PNG, "image/png")}),
            lambda: self.send("POST", path, body=self.body("Concurrent synthetic conversation")),
        ])
        self.assertEqual(self.ok(photo, 201)["content_type"], "image/webp")
        self.message(conversation)
        self.assertEqual(self.count_messages(), 3)

    def test_stable_newest_first_pagination_with_equal_timestamps(self):
        path, _ = self.awarded()
        other, _ = self.awarded()
        self.message(self.send("POST", other, body=self.body("Other awarded conversation")))
        request_id = UUID(path.split("/")[2])
        instant = datetime(2026, 11, 12, 15, tzinfo=timezone.utc)
        with Session(self.engine) as db:
            for number in range(1, 33):
                db.add(Message(id=UUID(int=number), request_id=request_id, sender_id=self.ids["owner"],
                               submission_id=uuid4(), sender_role="owner", body=f"Synthetic message {number}", created_at=instant))
            db.commit()
        first = self.page(path, "pro1")
        second = self.page(path, "pro1", 2)
        self.assertEqual((first["total"], second["total"], len(first["items"]), len(second["items"])), (32, 32, 30, 2))
        self.assertEqual([item["id"] for item in first["items"] + second["items"]], [str(UUID(int=n)) for n in range(32, 0, -1)])
        self.assertEqual(self.page(path, "pro1", 3)["items"], [])

    def test_migration_blocks_direct_access_invalid_senders_and_record_removal(self):
        revision = self.conversation_revision
        role_permissions = text("""
            SELECT r.rolname, has_table_privilege(r.oid, to_regclass('network_service_conversation_messages'), 'SELECT'),
                has_function_privilege(r.oid, to_regprocedure('network_guard_service_conversation_message()'), 'EXECUTE')
            FROM pg_roles r WHERE r.rolname IN ('anon','authenticated','service_role') ORDER BY r.rolname
        """)
        # Role DDL and all extra grants roll back together, even when a check fails.
        with self.engine.connect() as db, db.begin() as rollback:
            for role in ("anon", "authenticated", "service_role"):
                db.execute(text(f"""DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
                        CREATE ROLE {role} NOLOGIN;
                    END IF;
                END $$"""))
            db.execute(text("GRANT SELECT ON network_service_conversation_messages TO anon, authenticated, service_role"))
            db.execute(text("GRANT EXECUTE ON FUNCTION network_guard_service_conversation_message() TO anon, authenticated, service_role"))
            self.assertEqual([tuple(row) for row in db.execute(role_permissions)], [
                ("anon", True, True), ("authenticated", True, True), ("service_role", True, True),
            ])
            revision.op = Operations(MigrationContext.configure(db))
            revision.upgrade()
            self.assertEqual([tuple(row) for row in db.execute(role_permissions)], [
                ("anon", False, False), ("authenticated", False, False), ("service_role", False, False),
            ])
            rollback.rollback()

        with self.engine.begin() as db:
            revision.op = Operations(MigrationContext.configure(db))
            revision.downgrade()
            self.assertIsNone(db.execute(text("SELECT to_regclass('network_service_conversation_messages')")).scalar_one())
            revision.upgrade()
            table = db.execute(text("""
                SELECT c.relrowsecurity, NOT EXISTS (
                    SELECT 1 FROM aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a WHERE a.grantee=0
                ), NOT EXISTS (
                    SELECT 1 FROM pg_roles r WHERE r.rolname IN ('anon','authenticated','service_role')
                    AND has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                ) FROM pg_class c WHERE c.oid=to_regclass('network_service_conversation_messages')
            """)).one()
            self.assertEqual(tuple(table), (True, True, True))
            invoker = db.execute(text("""
                SELECT NOT p.prosecdef, NOT EXISTS (
                    SELECT 1 FROM aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a WHERE a.grantee=0
                ) FROM pg_proc p WHERE p.oid=to_regprocedure('network_guard_service_conversation_message()')
            """)).one()
            self.assertEqual(tuple(invoker), (True, True))

        path, _ = self.awarded()
        with self.assertRaises(DBAPIError) as invalid_sender, Session(self.engine) as db:
            db.add(Message(id=uuid4(), request_id=UUID(path.split("/")[2]), sender_id=self.ids["pro2"],
                           submission_id=uuid4(), sender_role="provider", body="Unauthorized synthetic sender"))
            db.commit()
        self.assertEqual(invalid_sender.exception.orig.pgcode, "23514")
        self.message(self.send("POST", path, body=self.body()))
        for statement in ("UPDATE network_service_conversation_messages SET body = 'altered'", "DELETE FROM network_service_conversation_messages"):
            with self.assertRaises(DBAPIError) as retained, self.engine.begin() as db:
                db.execute(text(statement))
            self.assertEqual(retained.exception.orig.pgcode, "23000")
        with self.assertRaises(DBAPIError) as denied_downgrade, self.engine.begin() as db:
            revision.op = Operations(MigrationContext.configure(db))
            revision.downgrade()
        self.assertEqual(denied_downgrade.exception.orig.pgcode, "55000")
        self.assertEqual(self.count_messages(), 1)


if __name__ == "__main__":
    unittest.main()
