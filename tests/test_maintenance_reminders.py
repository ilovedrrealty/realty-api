"""Owner maintenance reminders through private routes and disposable PostgreSQL."""
import importlib.util
import os
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import test_service_visit as visit_fixture
import test_service_workflow as workflow_fixture

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from models.listing import Listing
from models.maintenance_reminders import (NetworkMaintenanceAction as Action, NetworkMaintenanceOccurrence as Occurrence,
    NetworkMaintenancePreference as Preference, NetworkMaintenanceSchedule as Schedule)
from models.user import User
from services import maintenance_reminders


DEFAULT = {"enabled": False, "lead_days": 7, "version": 0}
FIELDS = {"id", "title", "repeat", "anchor_on", "due_on", "snoozed_until", "status", "version", "attention"}


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class PrivateMaintenanceReminders(unittest.TestCase):
    _workflow = workflow_fixture.PrivateServiceWorkflow
    _visit = visit_fixture.PrivateAwardedServiceWork
    cleanup_owned_schema = classmethod(_workflow.cleanup_owned_schema.__func__)
    patched = _workflow.patched
    stop_patches = _workflow.stop_patches
    send = _workflow.send
    ok = _workflow.ok
    private = _workflow.private
    parallel = _workflow.parallel
    assert_private = _visit.assert_private
    assert_refused = _visit.assert_refused

    @classmethod
    def setUpClass(cls):
        cls._workflow.setUpClass.__func__(cls)
        path = Path(__file__).resolve().parents[1] / "alembic/versions/e2b8c4a6d903_private_maintenance_reminders.py"
        spec = importlib.util.spec_from_file_location("maintenance_reminders_fixture_migration", path)
        cls.revision = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.revision)
        with cls.engine.begin() as db:
            cls.revision.op = Operations(MigrationContext.configure(db))
            cls.revision.upgrade()

    def setUp(self):
        self._workflow.setUp(self)
        self.clock = datetime(2026, 11, 12, 13, tzinfo=timezone.utc)
        self.patched(maintenance_reminders.network, "now", side_effect=lambda: self.clock)
        self.listing_path = self.path("listing", self.listing)

    def path(self, kind, source_id):
        return f"/maintenance-reminders/{kind}/{source_id}"

    def read(self, path, actor="owner", page=1):
        response = self.send("GET", f"{path}?page={page}", actor)
        self.assert_private(response)
        data = self.ok(response)
        self.assertEqual(set(data), {"source", "as_of", "time_zone", "preference", "items", "total", "page", "page_size"})
        self.assertEqual(data["source"], {"kind": path.split("/")[2], "id": path.split("/")[3]})
        self.assertEqual((data["time_zone"], data["page"], data["page_size"]), ("America/Santo_Domingo", page, 30))
        return data

    def body(self, first_due_on="2026-11-12", repeat="none", title="Synthetic maintenance"):
        return {"submission_id": str(uuid4()), "title": title, "repeat": repeat, "first_due_on": first_due_on}

    def preference(self, path, version=0, enabled=True, lead_days=7):
        body = {"submission_id": str(uuid4()), "expected_version": version, "enabled": enabled, "lead_days": lead_days}
        response = self.send("PUT", path + "/preferences", body=body)
        self.assert_private(response)
        return self.ok(response), body

    def item(self, response, status=200):
        self.assert_private(response)
        data = self.ok(response, status)
        self.assertEqual(set(data), FIELDS)
        return data

    def create(self, path, **fields):
        body = self.body(**fields)
        return self.item(self.send("POST", path, body=body), 201), body

    def action(self, operation, version, **fields):
        return {"submission_id": str(uuid4()), "expected_version": version, "operation": operation, **fields}

    def action_path(self, path, item):
        return f"{path}/{item['id']}/actions"

    def receipt_path(self, path, body):
        return f"{path}/submissions/{body['submission_id']}"

    def counts(self):
        with Session(self.engine) as db:
            return tuple(db.query(model).count() for model in (Preference, Schedule, Occurrence, Action))

    def test_exact_current_source_custody_and_default_off(self):
        path = self.listing_path
        empty = self.read(path)
        self.assertEqual((empty["preference"], empty["items"], self.counts()), (DEFAULT, [], (0, 0, 0, 0)))
        item, body = self.create(path)
        self.assertEqual((item["attention"], self.read(path)["preference"]), ("none", DEFAULT))
        for actor in ("admin", "manager", "pro1", "outsider"):
            self.assert_refused(self.send("GET", path, actor), 404)
            self.assert_refused(self.send("POST", path, actor, self.body()), 404)
            self.assert_refused(self.send("GET", self.receipt_path(path, body), actor), 404)
            self.assert_refused(self.send("POST", self.action_path(path, item), actor, self.action("complete", 1)), 404)
        self.assert_refused(self.send("GET", path, "unknown"), 401)
        self.assert_refused(self.send("GET", self.path("private", self.listing)), 404)
        self.assert_refused(self.send("GET", self.path("unsupported", self.listing)), 422)

        private = self.private()
        registry_path = self.path("private", private["id"])
        self.assertEqual(self.read(registry_path)["items"], [])
        registry_item, _ = self.create(registry_path, title="Synthetic private registry work")
        self.assertEqual(self.read(registry_path)["items"], [registry_item])
        fields = {key: private[key] for key in ("title", "location", "address", "notes", "relationship", "property_class")}
        self.ok(self.send("PUT", f"/registry/{private['id']}", body={**fields, "expected_version": private["version"], "status": "archived"}))
        self.assert_refused(self.send("GET", registry_path), 404)
        with Session(self.engine) as db:
            db.get(User, self.ids["outsider"]).role = "owner"
            db.get(Listing, self.listing).owner_id = self.ids["outsider"]
            db.commit()
        self.assert_refused(self.send("GET", path), 404)
        self.assert_refused(self.send("GET", self.receipt_path(path, body)), 404)
        self.assert_refused(self.send("GET", self.receipt_path(path, body), "outsider"), 404)
        self.assertEqual((self.read(path, "outsider")["items"], self.counts()), ([], (2, 2, 2, 2)))

    def test_preferences_and_receipts_are_versioned_and_source_scoped(self):
        path = self.listing_path
        current, body = self.preference(path, lead_days=3)
        self.assertEqual(current, {"enabled": True, "lead_days": 3, "version": 1})
        self.assertEqual(self.ok(self.send("PUT", path + "/preferences", body=body)), current)
        self.assert_refused(self.send("PUT", path + "/preferences", body={**body, "lead_days": 4}), 409)
        self.assert_refused(self.send("PUT", path + "/preferences", body={**body, "submission_id": str(uuid4())}), 409)
        for invalid in ({"lead_days": 31}, {"lead_days": True}, {"enabled": 1}, {"expected_version": False}):
            self.assert_refused(self.send("PUT", path + "/preferences", body={**body, **invalid}), 422)
        disabled, _ = self.preference(path, version=1, enabled=False, lead_days=0)
        receipt = self.send("GET", self.receipt_path(path, body))
        self.assert_private(receipt)
        self.assertEqual(self.ok(receipt), {"operation": "preferences", "result": disabled})
        self.assertEqual(self.ok(self.send("PUT", path + "/preferences", body=body)), disabled)
        private = self.path("private", self.private()["id"])
        self.assert_refused(self.send("GET", self.receipt_path(private, body)), 404)
        self.assert_refused(self.send("PUT", private + "/preferences", body=body), 409)
        self.assert_refused(self.send("POST", path, body={**self.body(), "submission_id": body["submission_id"]}), 409)
        self.assertEqual(self.read(private)["preference"], DEFAULT)
        self.assertEqual(self.counts(), (1, 0, 0, 2))

    def test_dominican_due_window_snooze_pause_resume_and_stop(self):
        path = self.listing_path
        self.clock = datetime(2026, 11, 13, 3, 59, tzinfo=timezone.utc)
        self.preference(path, lead_days=3)
        due, _ = self.create(path)
        self.assertEqual((self.read(path)["as_of"], due["attention"]), ("2026-11-12", "due"))
        for day, attention in (("2026-11-11", "overdue"), ("2026-11-15", "upcoming"), ("2026-11-16", "scheduled")):
            self.assertEqual(self.create(path, first_due_on=day)[0]["attention"], attention)
        for invalid in (1234, "2026-11-12T00:00:00Z", "2026-11-31"):
            self.assert_refused(self.send("POST", path, body=self.body(first_due_on=invalid)), 422)
        operation_path = self.action_path(path, due)
        for invalid in (self.action("snooze", 1), self.action("snooze", 1, snoozed_until="2026-11-12"),
                        self.action("pause", 1, snoozed_until=None), self.action("pause", 1, snoozed_until="2026-11-13")):
            self.assert_refused(self.send("POST", operation_path, body=invalid), 422)
        snooze = self.action("snooze", 1, snoozed_until="2026-11-13")
        snoozed = self.item(self.send("POST", operation_path, body=snooze))
        self.assertEqual((snoozed["due_on"], snoozed["snoozed_until"], snoozed["attention"]),
                         ("2026-11-12", "2026-11-13", "snoozed"))
        self.clock += timedelta(minutes=1)
        current = self.ok(self.send("GET", self.receipt_path(path, snooze)))["result"]
        self.assertEqual((self.read(path)["as_of"], current["attention"]), ("2026-11-13", "overdue"))
        paused = self.item(self.send("POST", operation_path, body=self.action("pause", 2)))
        self.assertEqual((paused["status"], paused["attention"], paused["snoozed_until"]), ("paused", "none", "2026-11-13"))
        self.assert_refused(self.send("POST", operation_path, body=self.action("complete", 3)), 409)
        resumed = self.item(self.send("POST", operation_path, body=self.action("resume", 3)))
        self.assertEqual((resumed["status"], resumed["attention"]), ("active", "overdue"))
        stop = self.action("stop", 4)
        stopped = self.item(self.send("POST", operation_path, body=stop))
        self.assertEqual((stopped["status"], stopped["attention"], stopped["due_on"], stopped["snoozed_until"]),
                         ("stopped", "none", due["due_on"], None))
        self.assertEqual(self.item(self.send("POST", operation_path, body=stop)), stopped)
        self.assert_refused(self.send("POST", operation_path, body=self.action("resume", 5)), 409)

    def test_recurrence_is_anchored_skips_missed_dates_and_commits_once(self):
        path = self.listing_path
        self.clock = datetime(2026, 3, 20, 13, tzinfo=timezone.utc)
        self.preference(path, lead_days=0)
        monthly, created = self.create(path, repeat="monthly", first_due_on="2026-01-31")
        operation_path = self.action_path(path, monthly)
        complete = self.action("complete", 1)
        calls = [lambda: self.send("POST", operation_path, body=complete) for _ in range(2)]
        first, duplicate = [self.item(response) for response in self.parallel(calls)]
        self.assertEqual(first, duplicate)
        self.assertEqual((first["anchor_on"], first["due_on"], first["version"], first["attention"]),
                         ("2026-01-31", "2026-03-31", 2, "scheduled"))
        self.assert_refused(self.send("POST", operation_path, body=self.action("complete", 1)), 409)
        self.assertEqual(self.ok(self.send("GET", self.receipt_path(path, created))), {"operation": "create", "result": first})
        self.assertEqual(self.ok(self.send("GET", self.receipt_path(path, complete))), {"operation": "complete", "result": first})
        with Session(self.engine) as db:
            rows = db.query(Occurrence).filter_by(schedule_id=UUID(first["id"])).order_by(Occurrence.due_on).all()
            self.assertEqual([(r.due_on.isoformat(), r.status) for r in rows], [("2026-01-31", "completed"), ("2026-03-31", "open")])

        for cadence, anchor, expected in (("weekly", "2026-03-01", "2026-03-22"),
                                          ("quarterly", "2025-11-30", "2026-05-30"),
                                          ("yearly", "2024-02-29", "2027-02-28"),
                                          ("none", "2026-03-20", "2026-03-20")):
            item, _ = self.create(path, repeat=cadence, first_due_on=anchor)
            result = self.item(self.send("POST", self.action_path(path, item), body=self.action("complete", 1)))
            self.assertEqual(result["due_on"], expected)
            self.assertEqual(result["status"], "completed" if cadence == "none" else "active")

        overflow, _ = self.create(path, repeat="yearly", first_due_on="9999-12-31")
        invalid = self.action("complete", 1)
        before = self.counts()
        self.assert_refused(self.send("POST", self.action_path(path, overflow), body=invalid), 422)
        self.assert_refused(self.send("GET", self.receipt_path(path, invalid)), 404)
        self.assertEqual(self.counts(), before)

    def test_migration_keeps_private_storage_and_one_open_occurrence(self):
        with self.engine.connect() as db, db.begin() as rollback:
            for role in ("anon", "authenticated", "service_role"):
                db.execute(text(f"""DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN CREATE ROLE {role} NOLOGIN; END IF;
                END $$"""))
            for table in self.revision.TABLES:
                db.execute(text(f"GRANT ALL ON {table} TO anon, authenticated, service_role"))
            self.revision.op = Operations(MigrationContext.configure(db))
            self.revision.upgrade()
            for table in self.revision.TABLES:
                self.assertEqual([tuple(row) for row in db.execute(text(f"""SELECT rolname,
                    has_table_privilege(oid, to_regclass('{table}'), 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    FROM pg_roles WHERE rolname IN ('anon','authenticated','service_role') ORDER BY rolname"""))],
                    [("anon", False), ("authenticated", False), ("service_role", False)])
            rollback.rollback()
        with self.engine.begin() as db:
            self.revision.op = Operations(MigrationContext.configure(db))
            self.revision.downgrade()
            for table in self.revision.TABLES:
                self.assertIsNone(db.execute(text("SELECT to_regclass(:name)"), {"name": table}).scalar_one())
            self.revision.upgrade()
            for table in self.revision.TABLES:
                self.assertTrue(db.execute(text("SELECT relrowsecurity FROM pg_class WHERE oid=to_regclass(:name)"),
                                           {"name": table}).scalar_one())

        schedule, _ = self.create(self.listing_path)
        with self.assertRaises(DBAPIError) as denied, Session(self.engine) as db:
            db.add(Occurrence(schedule_id=UUID(schedule["id"]), due_on=date(2026, 11, 13), status="open"))
            db.commit()
        self.assertEqual(denied.exception.orig.pgcode, "23505")
        self.assertEqual(self.read(self.listing_path)["items"], [schedule])


if __name__ == "__main__":
    unittest.main()
