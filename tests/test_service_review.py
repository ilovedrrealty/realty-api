"""Private completion feedback through real routes and disposable PostgreSQL."""
import importlib.util
import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import test_service_negotiation as negotiation_fixture

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from models.listing import Listing
from models.service_review import NetworkServiceReview as Review
from models.user import User


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class PrivateCompletedServiceReview(unittest.TestCase):
    _negotiation = negotiation_fixture.PrivateServiceNegotiationConversation
    _conversation = _negotiation._conversation
    _visit = _conversation._visit
    _workflow = _visit._workflow
    cleanup_owned_schema = classmethod(_workflow.cleanup_owned_schema.__func__)
    setUp = _visit.setUp
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
    awarded = _visit.awarded
    work_path = _visit.work_path
    confirm = _visit.confirm
    proposal = _visit.proposal
    command = _visit.command
    change = _visit.change
    work = _visit.work
    assert_private = _visit.assert_private
    assert_refused = _visit.assert_refused

    @classmethod
    def setUpClass(cls):
        cls._negotiation.setUpClass.__func__(cls)
        path = Path(__file__).resolve().parents[1] / "alembic/versions/a61d9e4b7f20_private_service_reviews.py"
        spec = importlib.util.spec_from_file_location("service_review_fixture_migration", path)
        cls.review_revision = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.review_revision)
        with cls.engine.begin() as db:
            cls.review_revision.op = Operations(MigrationContext.configure(db))
            cls.review_revision.upgrade()

    def body(self, **fields):
        return {"submission_id": str(uuid4()), "stars": 5, **fields}

    def review_path(self, work_path):
        return work_path.removesuffix("/work") + "/review"

    def request_completion(self, path):
        confirmed = self.confirm(path)
        self.clock += timedelta(hours=2, minutes=30)
        return self.change(path, self.command("request_completion", confirmed["version"],
            note="Synthetic progress completion statement", completed_on=self.clock.date().isoformat()), "pro1")

    def approve(self, path, requested):
        return self.change(path, self.command("approve_completion", requested["version"], share_in_history=True))

    def completed(self, request=None):
        path, project = self.awarded(request)
        self.approve(path, self.request_completion(path))
        return self.review_path(path), project

    def detail(self, path, actor="owner"):
        response = self.send("GET", path, actor)
        self.assert_private(response)
        data = self.ok(response)
        self.assertEqual(set(data), {"request_id", "review", "can_submit"})
        self.assertEqual(data["request_id"], path.split("/")[2])
        return data

    def review(self, response, status=201):
        self.assert_private(response)
        data = self.ok(response, status)
        self.assertEqual(set(data), {"id", "stars", "comment", "created_at"})
        self.assertEqual(str(UUID(data["id"])), data["id"])
        self.assertIsNotNone(datetime.fromisoformat(data["created_at"]).tzinfo)
        return data

    def denied(self, path, actor, body, status=404):
        self.assert_refused(self.send("GET", path, actor), status)
        self.assert_refused(self.send("GET", f"{path}/submissions/{body['submission_id']}", actor), status)
        self.assert_refused(self.send("POST", path, actor, body), status)

    def test_requires_owner_approved_completion_and_exact_readers(self):
        request, _ = self.request(role="manager")
        work_path = self.work_path(request)
        path, body = self.review_path(work_path), self.body()
        self.denied(path, "owner", body)
        _, project = self.awarded(request)
        self.denied(path, "owner", body)
        requested = self.request_completion(work_path)
        self.denied(path, "owner", body)
        self.denied(path, "pro1", body)
        self.approve(work_path, requested)
        self.ok(self.send("POST", f"/projects/{project['id']}/members", body={
            "expected_version": project["version"], "user_code": self.codes["pro2"], "role": "professional",
        }))
        for actor, allowed in (("owner", True), ("pro1", False)):
            self.assertEqual(self.detail(path, actor), {"request_id": request["id"], "review": None, "can_submit": allowed})
        for actor in ("admin", "manager", "pro2", "outsider"):
            self.denied(path, actor, body)
        self.denied(path, "unknown", body, 401)
        self.assert_refused(self.send("POST", path, "pro1", body), 403)
        row = self.review(self.send("POST", path, body=body))
        self.assertEqual((row["stars"], row["comment"]), (5, None))
        for actor in ("owner", "pro1"):
            self.assertEqual(self.detail(path, actor), {"request_id": request["id"], "review": row, "can_submit": False})

    def test_revocation_archive_and_custody_close_access(self):
        path, project = self.completed()
        body = self.body(comment="Synthetic retained review before access change")
        self.review(self.send("POST", path, body=body))
        with Session(self.engine) as db:
            db.get(User, self.ids["owner"]).role = "buyer"
            db.commit()
        for actor in ("owner", "pro1"):
            self.denied(path, actor, body)
        with Session(self.engine) as db:
            db.get(User, self.ids["owner"]).role = "owner"
            db.commit()
        self.ok(self.send("POST", f"/projects/{project['id']}/members/{self.codes['pro1']}/revoke", body={
            "expected_version": project["version"],
        }))
        for actor in ("owner", "pro1"):
            self.denied(path, actor, body)

        request, _ = self.request(self.private())
        archived, project = self.completed(request)
        archive_body = self.body()
        self.review(self.send("POST", archived, body=archive_body))
        self.ok(self.send("PUT", f"/projects/{project['id']}", body={"expected_version": project["version"],
            "title": project["title"], "summary": project["summary"], "status": "archived"}))
        for actor in ("owner", "pro1"):
            self.denied(archived, actor, archive_body)

        transferred, _ = self.completed()
        transfer_body = self.body()
        self.review(self.send("POST", transferred, body=transfer_body))
        with Session(self.engine) as db:
            db.get(User, self.ids["outsider"]).role = "owner"
            db.get(Listing, self.listing).owner_id = self.ids["outsider"]
            db.commit()
            self.assertEqual(db.query(Review).count(), 3)
        for actor in ("owner", "pro1", "outsider"):
            self.denied(transferred, actor, transfer_body)

    def test_strict_private_projection_and_no_work_history_leak(self):
        path, project = self.completed()
        for fields in ({"stars": 0}, {"stars": 6}, {"stars": True}, {"stars": 4.0}, {"stars": "4"},
                       {"comment": 4}, {"comment": "a" * 2001}, {"comment": "bad\x00"},
                       {"comment": "bad\x85"}, {"reviewer_id": str(uuid4())}, {"public": True}):
            self.assert_refused(self.send("POST", path, body=self.body(**fields)), 422)
        root = path.removesuffix("/review")
        projections = (root, root + "/work", root + "/conversation", f"/projects/{project['id']}/updates",
                       f"/properties/{self.listing}/history", "/service-providers?category_id=pool_service&property_class=house_villa")
        before = [self.ok(self.send("GET", route)) for route in projections]
        payload = self.body(stars=4, comment="  <b>Synthetic private review</b>\nLínea dos  ")
        row = self.review(self.send("POST", path, body=payload))
        self.assertEqual((row["stars"], row["comment"]), (4, "<b>Synthetic private review</b>\nLínea dos"))
        self.assertEqual([self.ok(self.send("GET", route)) for route in projections], before)
        self.assertEqual(self.detail(path, "pro1")["review"], row)

    def test_owner_receipt_and_concurrent_submission_are_idempotent(self):
        path, _ = self.completed()
        body = self.body(stars=2, comment=" \t ")
        calls = [lambda: self.send("POST", path, body=body), lambda: self.send("POST", path, body=body)]
        left, right = [self.review(response) for response in self.parallel(calls)]
        self.assertEqual(left, right)
        self.assertIsNone(left["comment"])
        receipt = f"{path}/submissions/{body['submission_id']}"
        self.assertEqual(self.review(self.send("GET", receipt), 200), left)
        self.assert_refused(self.send("GET", receipt, "pro1"), 404)
        self.assert_refused(self.send("GET", receipt, "admin"), 404)
        self.assert_refused(self.send("POST", path, body={**body, "stars": 3}), 409)
        self.assert_refused(self.send("POST", path, body=self.body(stars=2)), 409)
        second, _ = self.completed()
        self.assert_refused(self.send("POST", second, body=body), 409)
        self.assert_refused(self.send("GET", f"{second}/submissions/{body['submission_id']}"), 404)
        with Session(self.engine) as db:
            self.assertEqual(db.query(Review).count(), 1)

    def test_migration_restricts_raw_writes_direct_roles_and_retention(self):
        revision = self.review_revision
        with self.engine.connect() as db, db.begin() as rollback:
            for role in ("anon", "authenticated", "service_role"):
                db.execute(text(f"""DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN CREATE ROLE {role} NOLOGIN; END IF;
                END $$"""))
            db.execute(text("GRANT ALL ON network_service_reviews TO anon, authenticated, service_role"))
            db.execute(text("GRANT EXECUTE ON FUNCTION network_guard_service_review() TO anon, authenticated, service_role"))
            acl = text("""SELECT rolname,
                has_table_privilege(oid, to_regclass('network_service_reviews'), 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE'),
                has_function_privilege(oid, to_regprocedure('network_guard_service_review()'), 'EXECUTE')
                FROM pg_roles WHERE rolname IN ('anon','authenticated','service_role') ORDER BY rolname""")
            roles = ("anon", "authenticated", "service_role")
            self.assertEqual([tuple(row) for row in db.execute(acl)], [(role, True, True) for role in roles])
            revision.op = Operations(MigrationContext.configure(db))
            revision.upgrade()
            self.assertEqual([tuple(row) for row in db.execute(acl)], [(role, False, False) for role in roles])
            rollback.rollback()
        with self.engine.begin() as db:
            revision.op = Operations(MigrationContext.configure(db))
            revision.downgrade()
            self.assertIsNone(db.execute(text("SELECT to_regclass('network_service_reviews')")).scalar_one())
            revision.upgrade()
            self.assertTrue(db.execute(text("SELECT relrowsecurity FROM pg_class WHERE oid=to_regclass('network_service_reviews')")).scalar_one())

        work_path, _ = self.awarded()
        requested = self.request_completion(work_path)
        request_id = UUID(work_path.split("/")[2])
        path = self.review_path(work_path)
        def reject_insert(reviewer, provider):
            with self.assertRaises(DBAPIError) as rejected, Session(self.engine) as db:
                db.add(Review(id=uuid4(), request_id=request_id, reviewer_id=self.ids[reviewer], provider_id=self.ids[provider],
                              submission_id=uuid4(), stars=5))
                db.commit()
            self.assertEqual(rejected.exception.orig.pgcode, "23514")

        reject_insert("owner", "pro1")
        self.approve(work_path, requested)
        reject_insert("manager", "pro1")
        reject_insert("owner", "pro2")
        row = self.review(self.send("POST", path, body=self.body()))
        for statement in ("UPDATE network_service_reviews SET stars=1", "DELETE FROM network_service_reviews"):
            with self.assertRaises(DBAPIError) as retained, self.engine.begin() as db:
                db.execute(text(statement))
            self.assertEqual(retained.exception.orig.pgcode, "23000")
        with self.assertRaises(DBAPIError) as populated, self.engine.begin() as db:
            revision.op = Operations(MigrationContext.configure(db))
            revision.downgrade()
        self.assertEqual(populated.exception.orig.pgcode, "55000")
        self.assertEqual(self.detail(path)["review"], row)


if __name__ == "__main__":
    unittest.main()
