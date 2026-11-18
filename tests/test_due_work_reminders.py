"""Opted-in due-work reminders through real routes on disposable PostgreSQL."""
import importlib.util
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import test_service_visit as visit_fixture

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.orm import Session

from models.due_work_reminders import NetworkDueWorkAction as Action, NetworkDueWorkDisposition as Disposition, NetworkDueWorkPreference as Preference
from models.listing import Listing
from models.property_network import NetworkMilestone, NetworkProjectMember
from models.service_visit import NetworkServiceWork
from models.user import User


DEFAULT = {"milestones_enabled": False, "visits_enabled": False, "lead_days": 7, "version": 0}
FIELDS = {"kind", "id", "occurrence_version", "title", "due_on", "starts_at", "duration_minutes", "state",
          "snoozed_until", "version", "attention"}


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class PrivateDueWorkReminders(unittest.TestCase):
    _visit = visit_fixture.PrivateAwardedServiceWork
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
    awarded = _visit.awarded
    work_path = _visit.work_path
    confirm = _visit.confirm
    proposal = _visit.proposal
    command = _visit.command
    change = _visit.change
    work = _visit.work
    assert_private = _visit.assert_private
    assert_refused = _visit.assert_refused
    parallel = _workflow.parallel

    @classmethod
    def setUpClass(cls):
        cls._visit.setUpClass.__func__(cls)
        path = Path(__file__).resolve().parents[1] / "alembic/versions/a6e4c2b8d517_private_due_work_reminders.py"
        spec = importlib.util.spec_from_file_location("due_work_fixture_migration", path)
        cls.due_revision = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.due_revision)
        with cls.engine.begin() as db:
            cls.due_revision.op = Operations(MigrationContext.configure(db))
            cls.due_revision.upgrade()

    def path(self, project):
        return f"/projects/{project['id']}/due-work-reminders"

    def day(self):
        return self.clock.astimezone(visit_fixture.DR).date().isoformat()

    def listed(self, path, actor="owner"):
        response = self.send("GET", path, actor)
        self.assert_private(response)
        value = self.ok(response)
        self.assertEqual(set(value), {"project_id", "as_of", "time_zone", "preference", "items", "total", "page", "page_size"})
        self.assertEqual((value["project_id"], value["time_zone"], value["page"], value["page_size"]),
                         (path.split("/")[2], "America/Santo_Domingo", 1, 30))
        self.assertTrue(all(set(item) == FIELDS for item in value["items"]))
        return value

    def prefs(self, path, actor="owner", version=0, milestones=True, visits=True, lead_days=7):
        body = {"submission_id": str(uuid4()), "expected_version": version, "milestones_enabled": milestones,
                "visits_enabled": visits, "lead_days": lead_days}
        response = self.send("PUT", path + "/preferences", actor, body)
        self.assert_private(response)
        return self.ok(response), body

    def add_milestone(self, project, due=None):
        return self.ok(self.send("POST", f"/projects/{project['id']}/milestones", body={
            "title": "Synthetic dated milestone", "due_date": due or self.day(),
        }), 201)

    def edit_milestone(self, row, **updates):
        body = {key: row[key] for key in ("title", "due_date", "status")}
        return self.ok(self.send("PUT", f"/milestones/{row['id']}", body={**body, **updates, "expected_version": row["version"]}))

    def member(self, project, role, *, assign=None):
        route = f"/projects/{project['id']}"
        current = self.ok(self.send("GET", route))
        body = {"expected_version": current["version"]}
        if assign:
            return self.ok(self.send("POST", route + "/members", body={**body, "role": assign, "user_code": self.codes[role]}))
        return self.ok(self.send("POST", f"{route}/members/{self.codes[role]}/revoke", body=body))

    def action(self, path, item, operation, **fields):
        route = f"{path}/{item['kind']}/{item['id']}/actions"
        body = {"submission_id": str(uuid4()), "expected_version": item["version"], "occurrence_version": item["occurrence_version"],
                "operation": operation, **fields}
        return route, body

    def receipt(self, path, body, actor="owner"):
        return self.send("GET", f"{path}/submissions/{body['submission_id']}", actor)

    def counts(self):
        with Session(self.engine) as db:
            return tuple(db.query(model).count() for model in (Preference, Disposition, Action, NetworkMilestone, NetworkServiceWork))

    def test_current_owner_and_exact_professional_opt_in_are_independent(self):
        request, _ = self.request(self.private())
        work_path, project = self.awarded(request)
        path = self.path(project)
        self.add_milestone(project)
        self.confirm(work_path)
        self.member(project, "pro2", assign="professional")
        self.member(project, "manager", assign="manager")
        before = self.counts()
        for actor in ("owner", "pro1", "pro2"):
            page = self.listed(path, actor)
            self.assertEqual((page["preference"], page["items"]), (DEFAULT, []))
        self.assertEqual(self.counts(), before)
        for actor in ("admin", "manager", "outsider"):
            self.assert_refused(self.send("GET", path, actor), 404)
        self.prefs(path)
        self.assertEqual({item["kind"] for item in self.listed(path)["items"]}, {"milestone", "visit"})
        self.assertEqual(self.listed(path, "pro1")["items"], [])
        self.prefs(path, "pro1", milestones=False)
        self.assertEqual([item["kind"] for item in self.listed(path, "pro1")["items"]], ["visit"])
        self.prefs(path, "pro2")
        self.assertEqual([item["kind"] for item in self.listed(path, "pro2")["items"]], ["milestone"])

    def test_milestone_occurrence_changes_only_for_date_or_reopening(self):
        _, project = self.awarded()
        path = self.path(project)
        self.prefs(path, visits=False)
        original = self.add_milestone(project)
        first = self.listed(path)["items"][0]
        self.assertEqual((first["occurrence_version"], first["due_on"], first["starts_at"], first["duration_minutes"]), (1, self.day(), None, None))
        route, body = self.action(path, first, "stop")
        self.assertEqual(self.ok(self.send("POST", route, body=body))["state"], "stopped")
        changed = self.edit_milestone(original, title="Synthetic renamed", status="in_progress")
        preserved = self.listed(path)["items"][0]
        self.assertEqual((preserved["occurrence_version"], preserved["state"], preserved["title"]), (1, "stopped", "Synthetic renamed"))
        tomorrow = (self.clock.astimezone(visit_fixture.DR).date() + timedelta(days=1)).isoformat()
        moved = self.edit_milestone(changed, due_date=tomorrow)
        active = self.listed(path)["items"][0]
        self.assertEqual((active["occurrence_version"], active["state"], active["version"]), (moved["version"], "active", 0))
        self.assert_refused(self.receipt(path, body), 404)
        terminal = self.edit_milestone(moved, status="completed")
        self.assertEqual(self.listed(path)["items"], [])
        reopened = self.edit_milestone(terminal, status="open")
        self.assertEqual(self.listed(path)["items"][0]["occurrence_version"], reopened["version"])

    def test_snooze_dismiss_restore_stop_and_receipt_are_actor_scoped(self):
        self.clock = datetime(2026, 11, 13, 3, 59, tzinfo=timezone.utc)
        _, project = self.awarded()
        path = self.path(project)
        self.add_milestone(project)
        enabled, preference = self.prefs(path, lead_days=0)
        self.prefs(path, "pro1", lead_days=0)
        first = self.listed(path)["items"][0]
        self.assertEqual((first["attention"], first["due_on"]), ("due", "2026-11-12"))
        route, snooze = self.action(path, first, "snooze", snoozed_until="2026-11-13")
        calls = [lambda: self.send("POST", route, body=snooze) for _ in range(2)]
        left, right = [self.ok(response) for response in self.parallel(calls)]
        self.assertEqual(left, right)
        self.assertEqual((left["state"], left["attention"], left["version"]), ("snoozed", "none", 1))
        self.assertEqual(self.listed(path, "pro1")["items"][0], first)
        self.assert_refused(self.receipt(path, snooze, "pro1"), 404)
        self.clock += timedelta(minutes=1)
        current = self.ok(self.receipt(path, snooze))["result"]
        self.assertEqual((current["state"], current["snoozed_until"], current["attention"], current["version"]), ("active", None, "overdue", 1))
        for operation, expected in (("dismiss", "dismissed"), ("restore", "active"), ("stop", "stopped")):
            _, body = self.action(path, current, operation)
            current = self.ok(self.send("POST", route, body=body))
            self.assertEqual(current["state"], expected)
        self.assert_refused(self.send("POST", route, body=self.action(path, current, "restore")[1]), 409)
        disabled, _ = self.prefs(path, version=1, milestones=False, visits=False)
        self.assertEqual(self.listed(path)["items"], [])
        self.assertEqual(self.ok(self.receipt(path, preference)), {"operation": "preferences", "result": disabled})
        self.assertEqual(enabled["version"], 1)
        self.assert_refused(self.receipt(path, snooze), 404)

    def test_confirmed_visit_reschedule_cancel_and_completion_use_exact_occurrences(self):
        work_path, project = self.awarded()
        path = self.path(project)
        self.prefs(path, milestones=False)
        original = self.proposal(0)
        proposed = self.change(work_path, original)
        self.assertEqual(self.listed(path)["items"], [])
        confirmed = self.change(work_path, self.command("accept_visit", proposed["version"]), "pro1")
        first = self.listed(path)["items"][0]
        self.assertEqual((first["kind"], first["id"], first["occurrence_version"]), ("visit", work_path.split("/")[2], confirmed["version"]))
        later = self.proposal(confirmed["version"], start=self.clock + timedelta(hours=4), duration=75)
        pending = self.change(work_path, later, "pro1")
        self.assertEqual(self.listed(path)["items"], [first])
        declined = self.change(work_path, self.command("decline_visit", pending["version"]))
        self.assertEqual(self.listed(path)["items"], [first])
        pending = self.change(work_path, {**later, "submission_id": str(uuid4()), "expected_version": declined["version"]}, "pro1")
        accepted = self.change(work_path, self.command("accept_visit", pending["version"]))
        second = self.listed(path)["items"][0]
        self.assertEqual((second["occurrence_version"], second["duration_minutes"]), (accepted["version"], 75))
        self.assertNotEqual(second["starts_at"], first["starts_at"])
        route, stop = self.action(path, second, "stop")
        self.assertEqual(self.ok(self.send("POST", route, body=stop))["state"], "stopped")
        cancelled = self.change(work_path, self.command("cancel_visit", accepted["version"]))
        self.assertEqual(self.listed(path)["items"], [])
        self.assert_refused(self.receipt(path, stop), 404)
        pending = self.change(work_path, {**later, "submission_id": str(uuid4()), "expected_version": cancelled["version"]}, "pro1")
        accepted = self.change(work_path, self.command("accept_visit", pending["version"]))
        third = self.listed(path)["items"][0]
        self.assertEqual((third["starts_at"], third["occurrence_version"], third["state"], third["version"]),
                         (second["starts_at"], accepted["version"], "active", 0))
        self.clock += timedelta(hours=5)
        requested = self.change(work_path, self.command("request_completion", accepted["version"], note="Synthetic finished visit",
                                                       completed_on=self.day()), "pro1")
        self.assertEqual(self.listed(path)["items"], [])
        self.change(work_path, self.command("approve_completion", requested["version"], share_in_history=True))
        self.assertEqual(self.listed(path)["items"], [])

    def test_revoked_and_changed_grants_require_fresh_opt_in_and_custody(self):
        work_path, project = self.awarded()
        path = self.path(project)
        self.confirm(work_path)
        self.prefs(path)
        _, previous = self.prefs(path, "pro1")
        self.assertEqual(len(self.listed(path, "pro1")["items"]), 1)
        self.member(project, "pro1")
        self.assert_refused(self.send("GET", path, "pro1"), 404)
        self.assert_refused(self.receipt(path, previous, "pro1"), 404)
        self.assertEqual(self.listed(path)["items"], [])
        self.member(project, "pro1", assign="professional")
        self.assertEqual(self.listed(path, "pro1")["preference"], DEFAULT)
        self.assert_refused(self.receipt(path, previous, "pro1"), 404)
        self.prefs(path, "pro1")
        self.member(project, "pro1", assign="manager")
        self.assert_refused(self.send("GET", path, "pro1"), 404)
        self.member(project, "pro1", assign="professional")
        self.assertEqual(self.listed(path, "pro1")["preference"], DEFAULT)
        with Session(self.engine) as db:
            db.get(User, self.ids["outsider"]).role = "owner"
            db.get(Listing, self.listing).owner_id = self.ids["outsider"]
            db.commit()
        for actor in ("owner", "pro1", "outsider"):
            self.assert_refused(self.send("GET", path, actor), 404)

    def test_migration_keeps_legacy_slots_and_private_storage(self):
        work_path, project = self.awarded()
        confirmed = self.confirm(work_path)
        milestone = self.add_milestone(project)
        revision = self.due_revision
        with self.engine.begin() as db:
            revision.op = Operations(MigrationContext.configure(db))
            revision.downgrade()
            revision.upgrade()
            for table in revision.TABLES:
                private = db.execute(text("""SELECT c.relrowsecurity, NOT EXISTS (
                    SELECT 1 FROM aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a WHERE a.grantee=0
                ) FROM pg_class c WHERE c.oid=to_regclass(:name)"""), {"name": table}).one()
                self.assertEqual(tuple(private), (True, True))
        with Session(self.engine) as db:
            work = db.get(NetworkServiceWork, UUID(work_path.split("/")[2]))
            self.assertEqual((work.version, work.confirmed_reminder_version), (confirmed["version"], None))
            self.assertEqual(db.get(NetworkMilestone, UUID(milestone["id"])).reminder_occurrence_version, 1)
            member = db.query(NetworkProjectMember).filter_by(project_id=UUID(project["id"]), user_id=self.ids["pro1"]).one()
            self.assertEqual(member.reminder_grant_version, 1)
        path = self.path(project)
        self.prefs(path)
        self.assertEqual({item["kind"]: item["occurrence_version"] for item in self.listed(path)["items"]}, {"milestone": 1, "visit": 1})


if __name__ == "__main__":
    unittest.main()
