"""Awarded service visits through real private routes and a disposable Postgres."""
import importlib.util
import os
import unittest
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import test_service_workflow as workflow_fixture

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from models.property_network import NetworkProjectUpdate
from models.service_visit import NetworkServiceWork, NetworkServiceWorkAction
from services import service_visit
from services.property_management import today_dr


PROVIDER_NOTE = "Synthetic completion summary shared with project participants"
PRIVATE_REVIEW_NOTE = "Synthetic private review note for the awarded provider only"
WORK_FIELDS = {
    "request_id", "project_id", "version", "status", "confirmed_visit", "proposed_visit",
    "completion_note", "completed_on", "completion_review_note", "completion_update_id", "actions",
}
DR = timezone(timedelta(hours=-4))


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class PrivateAwardedServiceWork(unittest.TestCase):
    # Reuse the source-checked fixture setup and seed helpers without inheriting
    # the other suite's test cases or sharing its generated schema.
    _workflow = workflow_fixture.PrivateServiceWorkflow
    cleanup_owned_schema = classmethod(_workflow.cleanup_owned_schema.__func__)
    patched = _workflow.patched
    stop_patches = _workflow.stop_patches
    send = _workflow.send
    ok = _workflow.ok
    opt = _workflow.opt
    request_body = _workflow.request_body
    request = _workflow.request
    received = _workflow.received
    accepted = _workflow.accepted
    terms = _workflow.terms
    quote = _workflow.quote
    award_body = _workflow.award_body
    parallel = _workflow.parallel

    @classmethod
    def setUpClass(cls):
        cls._workflow.setUpClass.__func__(cls)
        migration = Path(__file__).resolve().parents[1] / "alembic/versions/b7e2c4d6f901_private_awarded_service_work.py"
        spec = importlib.util.spec_from_file_location("service_visit_fixture_migration", migration)
        cls.revision = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.revision)
        with cls.engine.begin() as connection:
            cls.revision.op = Operations(MigrationContext.configure(connection))
            cls.revision.upgrade()

    def setUp(self):
        self._workflow.setUp(self)
        local_morning = datetime.combine(today_dr(), time(9), DR)
        self.clock = local_morning.astimezone(timezone.utc)
        self.patched(service_visit.network, "now", side_effect=lambda: self.clock)

    def awarded(self, request=None):
        if request is None:
            request, _ = self.request()
        self.quote(request)
        self.quote(request, "pro2", amount="9876.54")
        result = self.ok(self.send("POST", f"/service-requests/{request['id']}/award", body=self.award_body(request)))
        return self.work_path(request), result["project"]

    def work_path(self, request):
        return f"/service-requests/{request['id']}/work"

    def command(self, operation, version, **fields):
        return {"submission_id": str(uuid4()), "expected_version": version, "operation": operation, **fields}

    def proposal(self, version, *, start=None, duration=60):
        return self.command("propose_visit", version, starts_at=(start or self.clock + timedelta(hours=2)).isoformat(),
                            duration_minutes=duration)

    def assert_private(self, response):
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        self.assertEqual(response.headers.get("Pragma"), "no-cache")
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")

    def work(self, response, status=200):
        self.assert_private(response)
        row = self.ok(response, status)
        self.assertEqual(set(row), WORK_FIELDS)
        return row

    def get_work(self, path, role="owner"):
        return self.work(self.send("GET", path, role))

    def change(self, path, command, role="owner"):
        return self.work(self.send("POST", path, role, command))

    def assert_refused(self, response, status):
        self.assert_private(response)
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(set(response.json()), {"detail"})

    def rows(self):
        with Session(self.engine) as db:
            return tuple(db.query(model).count() for model in (
                NetworkServiceWork, NetworkServiceWorkAction, NetworkProjectUpdate,
            ))

    def assert_visit(self, visit, command, proposed_by=None):
        fields = {"starts_at", "duration_minutes"}
        if proposed_by is not None:
            fields.add("proposed_by")
        self.assertEqual(set(visit), fields)
        self.assertEqual(datetime.fromisoformat(visit["starts_at"]), datetime.fromisoformat(command["starts_at"]))
        self.assertEqual(visit["duration_minutes"], command["duration_minutes"])
        if proposed_by is not None:
            self.assertEqual(visit["proposed_by"], proposed_by)

    def confirm(self, path, *, proposal=None):
        proposed = self.change(path, proposal or self.proposal(0))
        return self.change(path, self.command("accept_visit", proposed["version"]), "pro1")

    def test_lazy_access_exact_winner_and_receipt_revocation(self):
        request, _ = self.request()
        self.assert_refused(self.send("GET", self.work_path(request)), 404)
        path, project = self.awarded(request)
        initial = self.get_work(path)
        self.assertEqual((initial["request_id"], initial["project_id"], initial["version"], initial["status"]),
                         (request["id"], project["id"], 0, "unscheduled"))
        self.assertEqual([initial[key] for key in ("confirmed_visit", "proposed_visit", "completion_note", "completed_on",
                         "completion_review_note", "completion_update_id")], [None] * 6)
        self.assertIn("propose_visit", self.get_work(path, "pro1")["actions"])
        self.assertEqual(self.get_work(path, "admin")["actions"], [])
        self.assertEqual(self.rows(), (0, 0, 0))

        project_path = f"/projects/{project['id']}"
        self.ok(self.send("POST", f"{project_path}/members", body={
            "expected_version": project["version"], "user_code": self.codes["pro2"], "role": "professional",
        }))
        proposal = self.proposal(0)
        self.assert_refused(self.send("POST", path, "admin", proposal), 409)
        for role in ("outsider", "pro2"):
            with self.subTest(role=role):
                self.assert_refused(self.send("GET", path, role), 404)
                self.assert_refused(self.send("POST", path, role, proposal), 404)
        self.assertEqual(self.rows(), (0, 0, 0))

        proposed = self.change(path, proposal, "pro1")
        receipt = f"{path}/submissions/{proposal['submission_id']}"
        self.assertEqual(self.work(self.send("GET", receipt, "pro1")), proposed)
        self.assert_refused(self.send("GET", receipt, "owner"), 404)
        self.assert_refused(self.send("GET", receipt, "outsider"), 404)
        project = self.ok(self.send("GET", project_path))
        self.ok(self.send("POST", f"{project_path}/members/{self.codes['pro1']}/revoke",
                          body={"expected_version": project["version"]}))
        self.assert_refused(self.send("GET", path, "pro1"), 404)
        self.assert_refused(self.send("GET", receipt, "pro1"), 404)
        self.assert_refused(self.send("POST", path, "pro1", proposal), 404)
        retained = self.get_work(path)
        self.assertEqual((retained["version"], retained["status"], retained["actions"]),
                         (proposed["version"], "proposed", []))
        self.assertEqual(self.rows(), (1, 1, 0))

    def test_validation_other_side_reschedule_cancel_and_current_replay(self):
        path, _ = self.awarded()
        proposal = self.proposal(0)
        invalid = (
            {"starts_at": "2026-11-12T11:00:00"}, {"duration_minutes": 14}, {"duration_minutes": 481},
            {"duration_minutes": True}, {"expected_version": True}, {"note": "a" * 2001},
            {"provider_id": str(uuid4())}, {"operation": "invented"},
        )
        for fields in invalid:
            with self.subTest(fields=tuple(fields)):
                self.assert_refused(self.send("POST", path, body={**proposal, **fields}), 422)
        self.assertEqual(self.rows(), (0, 0, 0))

        proposed = self.change(path, proposal)
        self.assertEqual((proposed["version"], proposed["status"], proposed["confirmed_visit"]), (1, "proposed", None))
        self.assert_visit(proposed["proposed_visit"], proposal, "owner")
        for operation in ("accept_visit", "decline_visit"):
            self.assert_refused(self.send("POST", path, body=self.command(operation, 1)), 409)
        self.assert_refused(self.send("POST", path, "pro1", self.command("accept_visit", 0)), 409)
        confirmed = self.change(path, self.command("accept_visit", 1), "pro1")
        self.assertEqual((confirmed["version"], confirmed["status"], confirmed["proposed_visit"]), (2, "confirmed", None))
        self.assert_visit(confirmed["confirmed_visit"], proposal)
        self.assertEqual(self.change(path, proposal), self.get_work(path))
        self.assertEqual(self.work(self.send("GET", f"{path}/submissions/{proposal['submission_id']}")), self.get_work(path))
        self.assert_refused(self.send("POST", path, body={**proposal, "duration_minutes": 90}), 409)

        reschedule = self.proposal(2, start=self.clock + timedelta(hours=4), duration=90)
        pending = self.change(path, reschedule, "pro1")
        self.assertEqual((pending["version"], pending["status"]), (3, "proposed"))
        self.assertEqual(pending["confirmed_visit"], confirmed["confirmed_visit"])
        self.assert_visit(pending["proposed_visit"], reschedule, "provider")
        declined = self.change(path, self.command("decline_visit", 3))
        self.assertEqual((declined["version"], declined["status"], declined["proposed_visit"]), (4, "confirmed", None))
        self.assertEqual(declined["confirmed_visit"], confirmed["confirmed_visit"])
        pending = self.change(path, self.proposal(4, start=self.clock + timedelta(hours=4), duration=90), "pro1")
        moved = self.change(path, self.command("accept_visit", pending["version"]))
        self.assert_visit(moved["confirmed_visit"], reschedule)
        cancelled = self.change(path, self.command("cancel_visit", moved["version"]), "pro1")
        self.assertEqual((cancelled["version"], cancelled["status"], cancelled["confirmed_visit"], cancelled["proposed_visit"]),
                         (7, "cancelled", None, None))
        self.assertEqual(self.rows(), (1, 7, 0))

    def test_distinct_requests_cannot_confirm_overlapping_provider_visits(self):
        first, _ = self.awarded()
        second, _ = self.awarded()
        start = self.clock + timedelta(hours=2)
        original = self.proposal(0, start=start)
        self.change(first, original)
        overlapping = self.proposal(0, start=start + timedelta(minutes=30))
        self.change(second, overlapping)
        responses = self.parallel([
            lambda: self.send("POST", first, "pro1", self.command("accept_visit", 1)),
            lambda: self.send("POST", second, "pro1", self.command("accept_visit", 1)),
        ])
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        for path, proposal, response in zip((first, second), (original, overlapping), responses):
            row = self.get_work(path)
            if response.status_code == 200:
                self.assertEqual((row["status"], row["version"]), ("confirmed", 2))
                self.assert_visit(row["confirmed_visit"], proposal)
            else:
                self.assert_refused(response, 409)
                self.assertEqual((row["status"], row["version"], row["confirmed_visit"]), ("proposed", 1, None))
        self.assertEqual(self.rows(), (2, 3, 0))
        self.assert_refused(self.send("GET", f"{second}/submissions/{original['submission_id']}"), 404)

    def test_migration_fresh_and_precreated_tables_keep_private_retained_records(self):
        def assert_private_objects(db):
            tables = db.execute(text("""
                SELECT c.relname, c.relrowsecurity, NOT EXISTS (
                    SELECT 1 FROM aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a WHERE a.grantee = 0
                ), NOT EXISTS (
                    SELECT 1 FROM pg_roles r WHERE r.rolname IN ('anon', 'authenticated')
                      AND has_table_privilege(r.oid, c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                )
                FROM pg_class c
                WHERE c.oid IN (to_regclass('network_service_work'), to_regclass('network_service_work_actions'))
                ORDER BY c.relname
            """)).all()
            self.assertEqual([tuple(row) for row in tables], [
                ("network_service_work", True, True, True), ("network_service_work_actions", True, True, True),
            ])
            guard = db.execute(text("""
                SELECT NOT p.prosecdef, NOT EXISTS (
                    SELECT 1 FROM aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a WHERE a.grantee = 0
                ) FROM pg_proc p WHERE p.oid = to_regprocedure('network_guard_awarded_service_work()')
            """)).one()
            self.assertEqual(tuple(guard), (True, True))

        with self.engine.begin() as db:
            assert_private_objects(db)  # The ORM bootstrap had already made both tables.
            self.revision.op = Operations(MigrationContext.configure(db))
            self.revision.downgrade()
            self.assertEqual(tuple(db.execute(text("""
                SELECT to_regclass('network_service_work'), to_regclass('network_service_work_actions'),
                       to_regprocedure('network_guard_awarded_service_work()')
            """)).one()), (None, None, None))
            self.revision.upgrade()  # Upgrade now creates both tables itself.
            assert_private_objects(db)

        path, _ = self.awarded()
        self.change(path, self.proposal(0))
        with self.assertRaises(DBAPIError) as denied_downgrade, self.engine.begin() as db:
            self.revision.op = Operations(MigrationContext.configure(db))
            self.revision.downgrade()
        self.assertEqual(denied_downgrade.exception.orig.pgcode, "55000")
        with self.assertRaises(DBAPIError) as denied_delete, self.engine.begin() as db:
            db.execute(text("DELETE FROM network_service_work_actions"))
        self.assertEqual(denied_delete.exception.orig.pgcode, "23000")
        self.assertEqual(self.rows(), (1, 1, 0))

    def test_completion_review_rolls_back_with_history_and_replay_creates_one_private_entry(self):
        path, project = self.awarded()
        timeline = f"/projects/{project['id']}/updates"
        history = f"/properties/{self.listing}/history"
        confirmed = self.confirm(path)
        day = self.clock.astimezone(DR).date().isoformat()
        completion = self.command("request_completion", confirmed["version"], note=PROVIDER_NOTE, completed_on=day)
        self.assert_refused(self.send("POST", path, "pro1", completion), 409)

        self.clock += timedelta(hours=2, minutes=30)
        self.assert_refused(self.send("POST", path, body=completion), 409)
        requested = self.change(path, completion, "pro1")
        self.assertEqual((requested["status"], requested["completion_note"], requested["completed_on"],
                          requested["completion_update_id"]), ("completion_requested", PROVIDER_NOTE, day, None))
        self.assertEqual(self.ok(self.send("GET", timeline))["items"], [])
        self.assertEqual(self.ok(self.send("GET", history))["items"], [])

        review = self.command("request_changes", requested["version"], note=PRIVATE_REVIEW_NOTE)
        self.assert_refused(self.send("POST", path, "pro1", review), 409)
        rework = self.change(path, review)
        self.assertEqual((rework["status"], rework["completion_review_note"], rework["completion_update_id"]),
                         ("confirmed", PRIVATE_REVIEW_NOTE, None))
        requested = self.change(path, self.command("request_completion", rework["version"], note=PROVIDER_NOTE,
                                                   completed_on=day), "pro1")
        owner_review = self.get_work(path)
        approve = self.command("approve_completion", requested["version"], share_in_history=True)
        self.assert_refused(self.send("POST", path, body={**approve, "share_in_history": False}), 422)
        self.assert_refused(self.send("POST", path, "pro1", approve), 409)

        # A real DB error during the timeline insert must roll back the approval
        # and its receipt, allowing the exact same submission once repaired.
        with self.engine.begin() as db:
            db.execute(text("ALTER TABLE network_project_updates ADD CONSTRAINT synthetic_visit_approval_failure CHECK (false) NOT VALID"))
        try:
            self.assert_refused(self.send("POST", path, body=approve), 503)
            self.assertEqual(self.get_work(path), owner_review)
            self.assert_refused(self.send("GET", f"{path}/submissions/{approve['submission_id']}"), 404)
            self.assertEqual(self.ok(self.send("GET", timeline))["items"], [])
        finally:
            with self.engine.begin() as db:
                db.execute(text("ALTER TABLE network_project_updates DROP CONSTRAINT synthetic_visit_approval_failure"))

        completed = self.change(path, approve)
        update_id = completed["completion_update_id"]
        self.assertEqual((completed["version"], completed["status"]), (requested["version"] + 1, "completed"))
        self.assertEqual(str(UUID(update_id)), update_id)
        self.assertEqual(self.change(path, approve), completed)
        self.assertEqual(self.work(self.send("GET", f"{path}/submissions/{approve['submission_id']}")), completed)
        self.assertEqual(self.rows(), (1, 6, 1))
        with Session(self.engine) as db:
            entry = db.get(NetworkProjectUpdate, UUID(update_id))
            self.assertEqual(entry.author_id, self.ids["owner"])
            self.assertNotEqual(str(entry.submission_id), approve["submission_id"])

        for role in ("owner", "pro1"):
            for url in (timeline, history):
                with self.subTest(role=role, route=url):
                    response = self.send("GET", url, role)
                    self.assert_private(response)
                    rows = self.ok(response)["items"]
                    self.assertEqual(len(rows), 1)
                    self.assertEqual((rows[0]["id"], rows[0]["kind"], rows[0]["body"], rows[0]["occurred_on"], rows[0]["media_ids"]),
                                     (update_id, "progress", PROVIDER_NOTE, day, []))
                    for private in (PRIVATE_REVIEW_NOTE, "9876.54", approve["submission_id"]):
                        self.assertNotIn(private, response.text)
        self.assert_refused(self.send("GET", timeline, "pro2"), 404)
        self.assert_refused(self.send("GET", history, "outsider"), 404)


if __name__ == "__main__":
    unittest.main()
