"""Current service operations through private routes and disposable PostgreSQL."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import test_service_visit as visit_fixture

from sqlalchemy.orm import Session

from models.listing import Listing
from models.user import User
from services import service_workflow as workflow


FIELDS = {"request_id", "title", "stage", "desired_start_on", "quote_valid_until", "starts_at", "actions", "source"}


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class PrivateServiceOperations(unittest.TestCase):
    _visit = visit_fixture.PrivateAwardedServiceWork
    _workflow = _visit._workflow
    cleanup_owned_schema = classmethod(_workflow.cleanup_owned_schema.__func__)
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
    counts = _workflow.counts
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
        cls._visit.setUpClass.__func__(cls)

    def setUp(self):
        self._visit.setUp(self)
        self.clock = datetime(2026, 11, 12, 13, tzinfo=timezone.utc)
        self.patched(workflow, "today_dr", side_effect=self.day)

    def day(self):
        return self.clock.astimezone(visit_fixture.DR).date()

    def path(self, view="managed", page=1, suffix=""):
        return f"/service-operations?view={view}&page={page}{suffix}"

    def queue(self, view="managed", actor="owner", page=1, suffix=""):
        response = self.send("GET", self.path(view, page, suffix), actor)
        self.assert_private(response)
        result = self.ok(response)
        self.assertEqual(set(result), {"view", "as_of", "time_zone", "items", "total", "page", "page_size"})
        self.assertEqual((result["view"], result["as_of"], result["time_zone"], result["page"], result["page_size"]),
                         (view, self.day().isoformat(), "America/Santo_Domingo", page, 30))
        self.assertTrue(all(set(item) == FIELDS for item in result["items"]))
        self.assertTrue(all(item["source"] is None for item in result["items"]) if view == "received" else
                        all(set(item["source"]) == {"kind", "id", "title"} for item in result["items"]))
        return result

    def card(self, view="managed", actor="owner"):
        page = self.queue(view, actor)
        self.assertEqual((page["total"], len(page["items"])), (1, 1))
        return page["items"][0]

    def counter(self, request, provider="pro1", actor="owner", **changes):
        managed = self.ok(self.send("GET", f"/service-requests/{request['id']}?view=managed", actor))
        recipient = next(item for item in managed["recipients"] if item["user_code"] == self.codes[provider])
        offer = recipient["current_proposal"]
        return self.ok(self.send("POST", f"/service-requests/{request['id']}/recipients/{recipient['id']}/counter", actor, {
            "submission_id": str(uuid4()), "expected_request_version": managed["version"],
            "expected_recipient_version": recipient["version"], "current_proposal_id": offer["id"],
            "expected_proposal_version": offer["version"], "terms": self.terms(**changes),
        }))

    def revoke(self, request, provider):
        managed = self.ok(self.send("GET", f"/service-requests/{request['id']}?view=managed"))
        recipient = next(item for item in managed["recipients"] if item["user_code"] == self.codes[provider])
        self.ok(self.send("POST", f"/service-requests/{request['id']}/recipients/{recipient['id']}/revoke", body={
            "submission_id": str(uuid4()), "expected_request_version": managed["version"],
            "expected_recipient_version": recipient["version"],
        }))

    def change_member(self, project, provider, *, role=None):
        path = f"/projects/{project['id']}"
        current = self.ok(self.send("GET", path))
        version = {"expected_version": current["version"]}
        if role:
            return self.ok(self.send("POST", path + "/members", body={**version, "role": role, "user_code": self.codes[provider]}))
        return self.ok(self.send("POST", f"{path}/members/{self.codes[provider]}/revoke", body=version))

    def test_managed_quote_precedence_expiry_and_source_filters(self):
        request, _ = self.request(desired_start_on="2026-11-13")
        first = self.card()
        self.assertEqual((first["stage"], first["desired_start_on"], first["quote_valid_until"], first["starts_at"]),
                         ("awaiting_replies", "2026-11-13", None, None))
        self.assertEqual(first["source"], {"kind": "listing", "id": str(self.listing), "title": "Private-owner listing"})
        self.quote(request, valid_until="2026-11-13")
        self.quote(request, "pro2")
        self.counter(request, "pro2", valid_until="2026-11-15")
        ready = self.card()
        self.assertEqual((ready["stage"], ready["quote_valid_until"]), ("quote_ready", "2026-11-13"))
        self.assertEqual(set(ready["actions"]), {"compare_quotes", "award", "counter", "withdraw_offer", "cancel_request"})
        self.clock = datetime(2026, 11, 14, 3, 59, tzinfo=timezone.utc)
        self.assertEqual(self.card()["stage"], "quote_ready")
        self.clock += timedelta(minutes=1)
        pending = self.card()
        self.assertEqual((pending["stage"], pending["quote_valid_until"]), ("counter_pending", "2026-11-15"))
        self.assertNotIn("award", pending["actions"])
        self.clock += timedelta(days=2)
        expired = self.card()
        self.assertEqual((expired["stage"], expired["quote_valid_until"]), ("offer_expired", "2026-11-15"))
        self.assertIn("counter", expired["actions"])
        self.revoke(request, "pro1")
        self.revoke(request, "pro2")
        attention = self.card()
        self.assertEqual((attention["stage"], attention["actions"]), ("request_needs_attention", ["compare_quotes", "cancel_request"]))
        self.assertEqual(self.queue(suffix=f"&source_kind=listing&source_id={self.listing}")["items"], [attention])
        self.assertEqual(self.queue(suffix=f"&source_kind=private&source_id={self.listing}")["total"], 0)
        self.assert_refused(self.send("GET", self.path(suffix="&source_kind=listing")), 422)
        self.assert_refused(self.send("GET", self.path("received", suffix=f"&source_kind=listing&source_id={self.listing}"), "pro1"), 422)

    def test_received_invitation_quote_counter_and_closure_are_private(self):
        request, _ = self.request(role="manager", brief="Exact private test brief", area_label="Specific private area")
        self.assertEqual(self.card(actor="manager")["request_id"], request["id"])
        self.assertEqual(self.queue(actor="new_manager")["total"], 0)
        invitation = self.card("received", "pro1")
        self.assertEqual((invitation["stage"], invitation["actions"]), ("invitation", ["accept_invitation", "decline_invitation"]))
        self.opt("pro1", service_opt_in=False)
        unavailable = self.card("received", "pro1")
        self.assertEqual((unavailable["stage"], unavailable["actions"]), ("invitation_unavailable", ["decline_invitation"]))
        self.opt("pro1")
        self.accepted(request)
        self.assertEqual(self.card("received", "pro1")["stage"], "ready_to_quote")
        self.quote(request)
        self.assertEqual(self.card("received", "pro1")["stage"], "quote_submitted")
        self.counter(request, actor="manager")
        self.assertIn("withdraw_offer", self.card(actor="manager")["actions"])
        self.assertNotIn("withdraw_offer", self.card()["actions"])
        incoming = self.card("received", "pro1")
        self.assertEqual(incoming["stage"], "counter_ready")
        self.assertTrue({"accept_counter", "decline_counter"}.issubset(incoming["actions"]))
        self.opt("pro1", service_opt_in=False)
        unavailable = self.card("received", "pro1")
        self.assertEqual((unavailable["stage"], unavailable["actions"]), ("invitation_unavailable", ["decline_invitation", "decline_counter"]))
        self.opt("pro1")
        offered = self.received(request)
        proposal = offered["current_proposal"]
        self.ok(self.send("POST", f"/service-proposals/{proposal['id']}/accept-counter", "pro1", {
            "submission_id": str(uuid4()), "expected_recipient_version": offered["recipient"]["version"],
            "expected_proposal_version": proposal["version"],
        }))
        self.assertEqual(self.card("received", "pro1")["stage"], "counter_accepted")
        self.assertEqual(self.card()["stage"], "quote_ready")
        before = self.counts()
        second_response = self.send("GET", self.path("received"), "pro2")
        self.assertEqual(self.ok(second_response)["items"][0]["stage"], "invitation")
        for private in ("Exact private test brief", "Specific private area", str(self.listing), proposal["id"], proposal["amount"]):
            self.assertNotIn(private, second_response.text)
        self.assertEqual(self.counts(), before)
        with Session(self.engine) as db:
            db.get(Listing, self.listing).assigned_realtor_id = self.ids["new_manager"]
            db.commit()
        self.assertEqual(self.queue(actor="manager")["total"], 0)
        self.assertEqual(self.queue(actor="new_manager")["total"], 0)
        self.assert_refused(self.send("GET", self.path(), "admin"), 404)
        self.assert_refused(self.send("GET", self.path("received"), "unknown"), 401)

    def test_awarded_visit_and_completion_actions_match_current_work(self):
        request, _ = self.request(role="manager")
        work_path, _ = self.awarded(request)
        self.assertEqual(self.queue(actor="manager")["total"], 0)
        self.assertEqual(self.queue("received", "pro2")["total"], 0)
        self.assertEqual((self.card()["stage"], self.card()["actions"]), ("schedule_needed", ["propose_visit"]))
        first = self.change(work_path, self.proposal(0))
        self.assertEqual(self.card()["stage"], "visit_proposed")
        recipient = self.card("received", "pro1")
        self.assertEqual((recipient["stage"], recipient["starts_at"]), ("visit_response", None))
        self.assertEqual(set(recipient["actions"]), {"accept_visit", "decline_visit", "cancel_visit"})
        confirmed = self.change(work_path, self.command("accept_visit", first["version"]), "pro1")
        current = self.card()
        self.assertEqual((current["stage"], current["starts_at"]), ("visit_confirmed", confirmed["confirmed_visit"]["starts_at"]))
        proposed = self.change(work_path, self.proposal(confirmed["version"], start=self.clock + timedelta(hours=3)), "pro1")
        reschedule = self.card()
        self.assertEqual((reschedule["stage"], reschedule["starts_at"]), ("visit_response", current["starts_at"]))
        confirmed = self.change(work_path, self.command("decline_visit", proposed["version"]))
        self.clock += timedelta(hours=3)
        self.assertIn("request_completion", self.card("received", "pro1")["actions"])
        self.assertEqual(self.card("received", "pro1")["stage"], "visit_completion_due")
        requested = self.change(work_path, self.command("request_completion", confirmed["version"],
            note="Synthetic completion note must not appear", completed_on=self.day().isoformat()), "pro1")
        self.assertEqual((self.card()["stage"], set(self.card()["actions"])), ("completion_review", {"approve_completion", "request_changes"}))
        self.assertEqual((self.card("received", "pro1")["stage"], self.card("received", "pro1")["actions"]), ("completion_review", []))
        self.assertNotIn("Synthetic completion note must not appear", self.send("GET", self.path()).text)
        self.change(work_path, self.command("approve_completion", requested["version"], share_in_history=True))
        self.assertEqual((self.queue()["total"], self.queue("received", "pro1")["total"]), (0, 0))

    def test_revocation_and_custody_deny_before_counts(self):
        work_path, project = self.awarded()
        self.confirm(work_path)
        self.assertEqual((self.queue()["total"], self.queue("received", "pro1")["total"]), (1, 1))
        self.change_member(project, "pro2", role="professional")
        self.change_member(project, "pro1")
        self.assertEqual((self.queue()["total"], self.queue("received", "pro1")["total"], self.queue("received", "pro2")["total"]), (0, 0, 0))
        self.change_member(project, "pro1", role="professional")
        self.assertEqual(self.queue("received", "pro1")["total"], 1)
        with Session(self.engine) as db:
            db.get(User, self.ids["outsider"]).role = "owner"
            db.get(Listing, self.listing).owner_id = self.ids["outsider"]
            db.commit()
        self.assertEqual((self.queue()["total"], self.queue(actor="outsider")["total"], self.queue("received", "pro1")["total"]), (0, 0, 0))

    def test_stable_pagination_and_empty_page_counts(self):
        self.opt("pro1")
        self.opt("pro2")
        private = self.private()
        expected = []
        for number in range(31):
            row = self.ok(self.send("POST", "/service-requests", body=self.request_body(private, title=f"Synthetic queue request {number}")), 201)
            expected.append(row["id"])
        self.assertEqual(self.queue(suffix=f"&source_kind=private&source_id={private['id']}")["total"], 31)
        before = self.counts()
        for view, actor in (("managed", "owner"), ("received", "pro1")):
            first, second, empty = (self.queue(view, actor, page=page) for page in (1, 2, 3))
            self.assertEqual((first["total"], second["total"], empty["total"], len(first["items"]), len(second["items"]), empty["items"]),
                             (31, 31, 31, 30, 1, []))
            self.assertEqual([item["request_id"] for item in first["items"] + second["items"]], sorted(expected))
        self.assertEqual(self.queue("received", "outsider", page=3)["total"], 0)
        self.assertEqual(self.counts(), before)


if __name__ == "__main__":
    unittest.main()
