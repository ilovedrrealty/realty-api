"""Pre-award conversation isolation and retention on disposable PostgreSQL."""
import importlib.util
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import test_service_conversation as conversation_fixture

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from models.listing import Listing
from models.service_conversation import NetworkServiceConversationMessage as AwardMessage
from models.service_negotiation import NetworkServiceNegotiationMessage as Message
from models.user import User


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class PrivateServiceNegotiationConversation(unittest.TestCase):
    _conversation = conversation_fixture.PrivateAwardedServiceConversation
    _visit = _conversation._visit
    _workflow = _conversation._workflow
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
    body = _conversation.body
    message = _conversation.message

    @classmethod
    def setUpClass(cls):
        cls._conversation.setUpClass.__func__(cls)
        path = Path(__file__).resolve().parents[1] / "alembic/versions/e4a1c7b9d305_private_service_negotiation_conversation.py"
        spec = importlib.util.spec_from_file_location("negotiation_fixture_migration", path)
        cls.negotiation_revision = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.negotiation_revision)
        with cls.engine.begin() as db:
            cls.negotiation_revision.op = Operations(MigrationContext.configure(db))
            cls.negotiation_revision.upgrade()

    def request_path(self, request):
        return f"/service-requests/{request['id']}"

    def thread_path(self, request, recipient):
        return self.request_path(request) + f"/recipients/{recipient['id']}/conversation"

    def open_threads(self, request=None):
        if request is None:
            request, _ = self.request()
        recipients = [self.accepted(request, actor)["recipient"] for actor in ("pro1", "pro2")]
        return request, recipients

    def page(self, path, role="owner", number=1):
        response = self.send("GET", f"{path}?page={number}", role)
        self.assert_private(response)
        data = self.ok(response)
        self.assertEqual(set(data), {"request_id", "recipient_id", "items", "total", "page", "page_size"})
        self.assertEqual((data["request_id"], data["recipient_id"], data["page"], data["page_size"]),
                         (path.split("/")[2], path.split("/")[4], number, 30))
        return data

    def closed(self, path, role, body):
        self.assert_refused(self.send("GET", path, role), 404)
        self.assert_refused(self.send("GET", path + "/access", role), 404)
        self.assert_refused(self.send("GET", f"{path}/submissions/{body['submission_id']}", role), 404)
        self.assert_refused(self.send("POST", path, role, body), 404)

    def test_only_current_custodian_and_exact_eligible_accepted_provider(self):
        request, _ = self.request(role="manager")
        pending = self.received(request)["recipient"]
        self.closed(self.thread_path(request, pending), "owner", self.body())
        request, (first, second) = self.open_threads(request)
        path, other = self.thread_path(request, first), self.thread_path(request, second)
        body = self.body("Only the actual custodian and first provider")
        for actor in ("owner", "pro1"):
            access = self.send("GET", path + "/access", actor)
            self.assert_private(access)
            self.assertEqual(self.ok(access), {"request_id": request["id"], "recipient_id": first["id"],
                                               "can_open_conversation": True})
        for actor in ("admin", "manager", "pro2", "outsider"):
            self.closed(path, actor, body)
        self.assert_refused(self.send("GET", path, "unknown"), 401)
        private = self.message(self.send("POST", path, body=body))
        self.assertEqual(self.page(path, "pro1")["items"], [private])
        self.assertEqual(self.page(other, "pro2")["items"], [])

        self.opt("pro1", service_opt_in=False)
        for actor in ("owner", "pro1"):
            self.closed(path, actor, body)
        self.assertEqual(self.page(other, "pro2")["items"], [])
        self.opt("pro1", category_ids=["plumbing"])
        for actor in ("owner", "pro1"):
            self.closed(path, actor, body)
        self.opt("pro1")
        with Session(self.engine) as db:
            db.get(User, self.ids["owner"]).role = "buyer"
            db.commit()
        self.closed(path, "pro1", body)  # Explicit listing owner ID alone is insufficient after role loss.
        self.closed(path, "owner", body)

    def test_terminal_and_source_changes_close_without_transcript_transfer(self):
        marker = self.body("Private negotiation only, never project progress")
        for operation in ("decline", "revoke", "cancel", "award"):
            with self.subTest(operation=operation):
                request, (first, second) = self.open_threads()
                root = self.request_path(request)
                path = self.thread_path(request, first)
                body = {**marker, "submission_id": str(uuid4())}
                self.message(self.send("POST", path, body=body))
                if operation == "decline":
                    self.ok(self.send("POST", f"{root}/recipients/{first['id']}/decline", "pro1", {
                        "submission_id": str(uuid4()), "expected_recipient_version": first["version"],
                    }))
                elif operation in ("revoke", "cancel"):
                    version = self.ok(self.send("GET", root))["version"]
                    command = {"submission_id": str(uuid4()), "expected_request_version": version}
                    if operation == "revoke":
                        command["expected_recipient_version"] = first["version"]
                    endpoint = f"{root}/recipients/{first['id']}/revoke" if operation == "revoke" else root + "/cancel"
                    self.ok(self.send("POST", endpoint, body=command))
                else:
                    self.quote(request)
                    award = self.ok(self.send("POST", root + "/award", body=self.award_body(request)))
                    for actor in ("owner", "pro1"):
                        self.assertEqual(self.ok(self.send("GET", root + "/conversation", actor))["items"], [])
                        self.assertEqual(self.ok(self.send("GET", f"/projects/{award['project']['id']}/updates", actor))["items"], [])
                for actor in ("owner", "pro1"):
                    self.closed(path, actor, body)
                if operation in ("decline", "revoke"):
                    self.assertEqual(self.page(self.thread_path(request, second), "pro2")["items"], [])
                else:
                    self.closed(self.thread_path(request, second), "pro2", body)

        private = self.private()
        request, _ = self.request(private)
        request, (first, _) = self.open_threads(request)
        path = self.thread_path(request, first)
        body = self.body()
        self.message(self.send("POST", path, body=body))
        fields = {key: private[key] for key in ("title", "location", "address", "notes", "relationship")}
        registry_path = f"/registry/{private['id']}"
        archived = self.ok(self.send("PUT", registry_path, body={**fields, "expected_version": private["version"],
                                                                "property_class": private["property_class"], "status": "archived"}))
        for actor in ("owner", "pro1"):
            self.closed(path, actor, body)
        self.ok(self.send("PUT", registry_path, body={**fields, "expected_version": archived["version"],
                                                     "property_class": "office", "status": "active"}))
        for actor in ("owner", "pro1"):
            self.closed(path, actor, body)

        request, (first, _) = self.open_threads()
        path = self.thread_path(request, first)
        body = self.body()
        self.message(self.send("POST", path, "pro1", body))
        with Session(self.engine) as db:
            db.get(User, self.ids["outsider"]).role = "owner"
            db.get(Listing, self.listing).owner_id = self.ids["outsider"]
            db.commit()
        for actor in ("owner", "pro1", "outsider"):
            self.closed(path, actor, body)
        with Session(self.engine) as db:
            self.assertEqual(db.query(Message).count(), 6)
            self.assertEqual(db.query(AwardMessage).count(), 0)

    def test_idempotent_submission_is_actor_thread_and_phase_scoped(self):
        request, (first, second) = self.open_threads()
        path, other = self.thread_path(request, first), self.thread_path(request, second)
        payload = self.body()
        receipt = f"{path}/submissions/{payload['submission_id']}"
        first_post, replay = (self.message(response) for response in self.parallel([lambda: self.send("POST", path, body=payload)] * 2))
        self.assertEqual(first_post, replay)
        self.assertEqual(self.message(self.send("GET", receipt), 200), first_post)
        self.assert_refused(self.send("GET", receipt, "pro1"), 404)
        self.assert_refused(self.send("GET", f"{other}/submissions/{payload['submission_id']}"), 404)
        self.assert_refused(self.send("POST", other, body=payload), 409)
        self.assert_refused(self.send("POST", path, body={**payload, "body": "Changed"}), 409)
        provider = self.message(self.send("POST", path, "pro1", payload))
        self.assertNotEqual(first_post["id"], provider["id"])
        self.assertEqual(self.message(self.send("GET", receipt, "pro1"), 200), provider)

        self.quote(request)
        root = self.request_path(request)
        self.ok(self.send("POST", root + "/award", body=self.award_body(request)))
        awarded_path = root + "/conversation"
        self.assert_refused(self.send("POST", awarded_path, body=payload), 409)
        self.assert_refused(self.send("GET", f"{awarded_path}/submissions/{payload['submission_id']}"), 404)
        with self.assertRaises(DBAPIError) as pre_to_award, Session(self.engine) as db:
            db.add(AwardMessage(id=uuid4(), request_id=UUID(request["id"]), sender_id=self.ids["owner"],
                                submission_id=UUID(payload["submission_id"]), sender_role="owner", body=payload["body"]))
            db.commit()
        self.assertEqual(pre_to_award.exception.orig.pgcode, "23505")
        awarded_body = self.body("Independent awarded message")
        self.message(self.send("POST", awarded_path, body=awarded_body))
        next_request, (next_recipient, _) = self.open_threads()
        next_path = self.thread_path(next_request, next_recipient)
        self.assert_refused(self.send("POST", next_path, body=awarded_body), 409)
        self.assert_refused(self.send("GET", f"{next_path}/submissions/{awarded_body['submission_id']}"), 404)
        with self.assertRaises(DBAPIError) as award_to_pre, Session(self.engine) as db:
            db.add(Message(id=uuid4(), request_id=UUID(next_request["id"]), recipient_id=UUID(next_recipient["id"]),
                           sender_id=self.ids["owner"], submission_id=UUID(awarded_body["submission_id"]),
                           sender_role="owner", body=awarded_body["body"]))
            db.commit()
        self.assertEqual(award_to_pre.exception.orig.pgcode, "23505")

    def test_plain_text_and_deterministic_newest_pagination(self):
        request, (first, second) = self.open_threads()
        path = self.thread_path(request, first)
        for value in ("", " \t\r\n ", "contains\x00", "contains\x7f", "é" * 2001):
            self.assert_refused(self.send("POST", path, body=self.body(value)), 422)
        self.assert_refused(self.send("POST", path, body={**self.body(), "recipient_id": second["id"]}), 422)
        self.assert_refused(self.send("GET", path + "?page=0"), 422)
        other = self.thread_path(request, second)
        valid = "  Hola 🛠️\r\n <b>literal & plain</b>  "
        posted = self.message(self.send("POST", other, "pro2", self.body(valid)))
        self.assertEqual((posted["sender"], posted["body"]), ("provider", valid.strip()))
        self.assertEqual(self.page(other, "pro2")["items"], [posted])

        instant = datetime(2026, 11, 12, 15, tzinfo=timezone.utc)
        with Session(self.engine) as db:
            for number in range(1, 33):
                db.add(Message(id=UUID(int=number), request_id=UUID(request["id"]), recipient_id=UUID(first["id"]),
                               sender_id=self.ids["owner"], submission_id=uuid4(), sender_role="owner",
                               body=f"Synthetic message {number}", created_at=instant))
            db.commit()
        first_page, next_page = self.page(path, "pro1"), self.page(path, "pro1", 2)
        self.assertEqual((first_page["total"], next_page["total"], len(first_page["items"]), len(next_page["items"])), (32, 32, 30, 2))
        self.assertEqual([item["id"] for item in first_page["items"] + next_page["items"]], [str(UUID(int=n)) for n in range(32, 0, -1)])
        self.assertEqual(self.page(path, "pro1", 3)["items"], [])

    def test_migration_preserves_private_immutable_recipient_records(self):
        revision = self.negotiation_revision
        with self.engine.connect() as db, db.begin() as rollback:
            for role in ("anon", "authenticated", "service_role"):
                db.execute(text(f"""DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN CREATE ROLE {role} NOLOGIN; END IF;
                END $$"""))
            db.execute(text("GRANT SELECT ON network_service_negotiation_messages TO anon, authenticated, service_role"))
            db.execute(text("GRANT EXECUTE ON FUNCTION network_guard_service_negotiation_message() TO anon, authenticated, service_role"))
            direct = text("""SELECT rolname,
                has_table_privilege(oid, to_regclass('network_service_negotiation_messages'), 'SELECT'),
                has_function_privilege(oid, to_regprocedure('network_guard_service_negotiation_message()'), 'EXECUTE')
                FROM pg_roles WHERE rolname IN ('anon','authenticated','service_role') ORDER BY rolname""")
            self.assertEqual([tuple(row) for row in db.execute(direct)], [
                ("anon", True, True), ("authenticated", True, True), ("service_role", True, True),
            ])
            revision.op = Operations(MigrationContext.configure(db))
            revision.upgrade()
            self.assertEqual([tuple(row) for row in db.execute(direct)], [
                ("anon", False, False), ("authenticated", False, False), ("service_role", False, False),
            ])
            rollback.rollback()

        with self.engine.begin() as db:
            revision.op = Operations(MigrationContext.configure(db))
            revision.downgrade()
            self.assertIsNone(db.execute(text("SELECT to_regclass('network_service_negotiation_messages')")).scalar_one())
            revision.upgrade()
            self.assertTrue(db.execute(text("""SELECT relrowsecurity FROM pg_class
                WHERE oid=to_regclass('network_service_negotiation_messages')""")).scalar_one())

        request, (first, _) = self.open_threads()
        other, (wrong, _) = self.open_threads()
        path = self.thread_path(request, first)
        self.closed(self.thread_path(other, first), "owner", self.body())
        for recipient, sender in ((wrong["id"], self.ids["pro1"]), (first["id"], self.ids["pro2"])):
            with self.assertRaises(DBAPIError) as rejected, Session(self.engine) as db:
                db.add(Message(id=uuid4(), request_id=UUID(request["id"]), recipient_id=UUID(recipient),
                               sender_id=sender, submission_id=uuid4(), sender_role="provider", body="Synthetic invalid scope"))
                db.commit()
            self.assertEqual(rejected.exception.orig.pgcode, "23514")

        payload = self.body()
        self.message(self.send("POST", path, body=payload))
        for statement in ("UPDATE network_service_negotiation_messages SET body='altered'", "DELETE FROM network_service_negotiation_messages"):
            with self.assertRaises(DBAPIError) as retained, self.engine.begin() as db:
                db.execute(text(statement))
            self.assertEqual(retained.exception.orig.pgcode, "23000")
        with self.assertRaises(DBAPIError) as populated, self.engine.begin() as db:
            revision.op = Operations(MigrationContext.configure(db))
            revision.downgrade()
        self.assertEqual(populated.exception.orig.pgcode, "55000")
        self.assertEqual(self.page(path)["total"], 1)


if __name__ == "__main__":
    unittest.main()
