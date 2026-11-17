"""Private referral decisions in the existing disposable PostgreSQL service fixture."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
import importlib.util
import os
from pathlib import Path
import unittest
from urllib.parse import urlencode
from uuid import UUID, uuid4

import test_service_workflow as workflow_fixture

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import Session

from models.listing import Listing
from models.professional_profile import ProfessionalProfile
from models.service_referral import NetworkServiceReferral, NetworkServiceReferralEvent
from models.service_workflow import NetworkServicePreference, NetworkServiceRecipient, NetworkServiceRequest
from models.user import User
from routes import service_referral
from schemas.service_referral import ReferralAction, ReferralCreate
from services import service_workflow as workflow


TITLE = "REFERRAL_PRIVATE_TITLE"
BRIEF = "REFERRAL_PRIVATE_BRIEF"
AREA = "REFERRAL_PRIVATE_AREA"
REQUESTER = "REFERRAL_PRIVATE_REQUESTER"
REFERRAL_FIELDS = {
    "id", "version", "status", "category_id", "property_class", "area_id", "language", "relationship",
    "created_at", "updated_at", "provider_alias", "can_offer", "can_accept", "can_decline", "can_revoke", "request_id",
}
PAGE_FIELDS = {"items", "total", "page", "page_size"}


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class PrivateServiceReferrals(unittest.TestCase):
    _workflow = workflow_fixture.PrivateServiceWorkflow
    cleanup_owned_schema = classmethod(_workflow.cleanup_owned_schema.__func__)
    setUp = _workflow.setUp
    patched = _workflow.patched
    stop_patches = _workflow.stop_patches
    send = _workflow.send
    ok = _workflow.ok
    opt = _workflow.opt
    request = _workflow.request
    request_body = _workflow.request_body
    received = _workflow.received
    accepted = _workflow.accepted
    parallel = _workflow.parallel
    publish = _workflow._publish_match_profile

    @classmethod
    def setUpClass(cls):
        cls._workflow.setUpClass.__func__(cls)
        path = Path(__file__).resolve().parents[1] / "alembic/versions/e12ac7d49f03_private_service_referrals.py"
        spec = importlib.util.spec_from_file_location("service_referrals_fixture_migration", path)
        revision = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(revision)
        with cls.engine.begin() as db:
            revision.op = Operations(MigrationContext.configure(db))
            revision.upgrade()
        cls.client.app.include_router(service_referral.router)

    def checked(self, response, status=200):
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        self.assertEqual(response.headers.get("Pragma"), "no-cache")
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        for hidden in (TITLE, BRIEF, AREA, REQUESTER, "PRIVATE-ACCOUNT-", "EXACT-LOCATION-MARKER",
                       "PROFILE_PRIVATE_NAME", "PROFILE_CONTACT", "DRAFT_UNREVIEWED_NAME", "+18095550100",
                       "source_title", "scope_sha256", "@example"):
            self.assertNotIn(hidden, response.text)
        result = self.ok(response, status)
        if status >= 400:
            self.assertEqual(set(result), {"detail"})
        return result

    def opened(self):
        row, _ = self.request(role="manager", title=TITLE, brief=BRIEF, area_label=AREA, requester_alias=REQUESTER)
        return row

    def matching_profile(self, key="outsider", **publish_fields):
        self.opt(key)
        return self.publish(key, **publish_fields)

    def extra_provider(self, key, code):
        self.ids[key], self.codes[key] = uuid4(), code
        with Session(self.engine) as db:
            db.add(User(id=self.ids[key], user_code=code, email=f"{key}@example.invalid", display_name="PRIVATE-ACCOUNT-extra",
                        role="buyer", status="active", email_verified=True))
            db.commit()
        self.matching_profile(key)

    def referrals(self, row, actor="manager", expected=200, page=1):
        result = self.checked(self.send("GET", f"/service-requests/{row['id']}/referrals?page={page}", actor), expected)
        if expected == 200:
            self.assertEqual(set(result), PAGE_FIELDS | {"can_suggest", "can_offer"})
            self.assertEqual((result["page"], result["page_size"]), (page, 30))
        return result

    def candidates(self, row, actor="manager", expected=200, **changes):
        query = urlencode({"area_id": "cabarete", "language": "es", "page": 1, **changes})
        return self.checked(self.send("GET", f"/service-requests/{row['id']}/referral-providers?{query}", actor), expected)

    def inbox(self, actor="outsider"):
        result = self.checked(self.send("GET", "/service-referrals?view=received&page=1", actor))
        self.assertEqual(set(result), PAGE_FIELDS)
        for item in result["items"]:
            self.assertEqual(set(item), REFERRAL_FIELDS)
            self.assertIsNone(item["provider_alias"])
        return result

    def referral_body(self, provider="outsider", *, sharing=True):
        return {"submission_id": str(uuid4()), "provider_user_code": self.codes[provider], "area_id": "cabarete",
                "language": "es", "relationship": "worked_together", "share_confirmed": sharing}

    def create(self, row, actor, body, expected=201):
        response = self.send("POST", f"/service-requests/{row['id']}/referrals", actor, body)
        result = self.checked(response, expected)
        if expected == 201:
            self.assertEqual(set(result), REFERRAL_FIELDS)
        return result

    def transition(self, row, actor, action, version, *, expected=200, body=None):
        body = body or {"submission_id": str(uuid4()), "expected_version": version, "share_confirmed": action == "offer"}
        result = self.checked(self.send("POST", f"/service-referrals/{row['id']}/{action}", actor, body), expected)
        if expected == 200:
            self.assertEqual(set(result), REFERRAL_FIELDS)
        return result

    def receipt(self, body, actor, expected=200):
        return self.checked(self.send("GET", f"/service-referral-submissions/{body['submission_id']}", actor), expected)

    def recipient_count(self, request):
        with Session(self.engine) as db:
            return db.query(NetworkServiceRecipient).filter_by(request_id=UUID(request["id"])).count()

    def test_private_proposal_does_not_reveal_request_until_accepted_into_normal_workflow(self):
        request = self.opened()
        self.accepted(request, "pro1")
        self.matching_profile()
        self.assertTrue(self.referrals(request, "pro1")["can_suggest"])
        self.assertFalse(self.referrals(request, "pro1")["can_offer"])
        self.assertTrue(self.referrals(request)["can_offer"])
        self.referrals(request, "pro2", 404)
        self.referrals(request, "outsider", 404)
        self.referrals(request, "unknown", 401)
        self.assertEqual(self.candidates(request, "pro1")["items"], [
            {"user_code": self.codes["outsider"], "member_alias": "Deliberate alias outsider", "category_ids": ["pool_service"]},
        ])
        self.assertEqual(self.candidates(request, language="en")["items"], [])
        self.candidates(request, "outsider", 404)
        with Session(self.engine) as db:
            before = db.get(NetworkServiceRequest, UUID(request["id"]))
            original_scope = (before.scope_sha256, list(before.recipient_user_codes), before.version)

        self.create(request, "pro1", self.referral_body(sharing=True), 422)
        nomination = self.referral_body(sharing=False)
        proposed = self.create(request, "pro1", nomination)
        self.assertEqual((proposed["status"], proposed["version"]), ("proposed", 1))
        self.assertEqual(self.referrals(request, "pro1")["items"], [proposed])
        self.assertEqual(self.inbox()["items"], [])
        self.receipt(nomination, "outsider", 404)
        self.transition(proposed, "outsider", "accept", 1, expected=404)
        self.transition(proposed, "pro1", "offer", 1, expected=404)
        self.transition(proposed, "manager", "offer", 1, expected=422,
                        body={"submission_id": str(uuid4()), "expected_version": 1, "share_confirmed": False})

        offer_body = {"submission_id": str(uuid4()), "expected_version": 1, "share_confirmed": True}
        self.transition(proposed, "manager", "offer", 1, body=offer_body)
        [offer] = self.inbox()["items"]
        self.assertEqual((offer["status"], offer["version"], offer["request_id"]), ("offered", 2, None))
        self.assertTrue(offer["can_accept"])
        self.assertTrue(offer["can_decline"])
        self.assertEqual(self.recipient_count(request), 2)
        self.checked(self.send("GET", f"/service-requests/{request['id']}?view=received", "outsider"), 404)
        self.transition(offer, "manager", "accept", 2, expected=404)

        accept_body = {"submission_id": str(uuid4()), "expected_version": 2, "share_confirmed": False}
        accepted = self.transition(offer, "outsider", "accept", 2, body=accept_body)
        self.assertEqual((accepted["status"], accepted["version"], accepted["request_id"]), ("accepted", 3, request["id"]))
        self.assertIsNone(accepted["provider_alias"])
        self.assertEqual(self.receipt(accept_body, "outsider"), accepted)
        normal = self.received(request, "outsider")
        self.assertEqual((normal["title"], normal["brief"], normal["area_label"]), (TITLE, BRIEF, AREA))
        self.assertEqual(normal["recipient"]["status"], "accepted")
        self.assertTrue(normal["capabilities"]["can_quote"])
        self.assertNotIn("source", normal)
        with Session(self.engine) as db:
            saved = db.get(NetworkServiceRequest, UUID(request["id"]))
            self.assertEqual((saved.scope_sha256, saved.recipient_user_codes, saved.version),
                             (*original_scope[:2], original_scope[2] + 1))
            referral = db.get(NetworkServiceReferral, UUID(offer["id"]))
            recipient = db.get(NetworkServiceRecipient, referral.recipient_id)
            self.assertEqual((recipient.id, recipient.request_id, recipient.provider_id, recipient.version),
                             (UUID(normal["recipient"]["id"]), saved.id, self.ids["outsider"], 1))
            events = db.query(NetworkServiceReferralEvent).filter_by(referral_id=referral.id).order_by(
                NetworkServiceReferralEvent.result_version).all()
            actions = (("create", "pro1", saved.id, ReferralCreate.model_validate(nomination)),
                       ("offer", "manager", referral.id, ReferralAction.model_validate(offer_body)),
                       ("accept", "outsider", referral.id, ReferralAction.model_validate(accept_body)))
            self.assertEqual(
                [(event.operation, event.actor_id, event.result_version, event.input_sha256) for event in events],
                [(operation, self.ids[actor], version, workflow.fingerprint("referral_" + operation, target, body))
                 for version, (operation, actor, target, body) in enumerate(actions, 1)],
            )

    def test_stale_commands_replays_and_revoke_preserve_actor_scope(self):
        request = self.opened()
        self.matching_profile()
        self.create(request, "manager", self.referral_body(sharing=False), 422)
        body = self.referral_body()
        initial = self.create(request, "manager", body)
        self.assertEqual(self.create(request, "manager", body), initial)
        self.assertEqual(self.receipt(body, "manager"), initial)
        self.receipt(body, "owner", 404)
        self.create(request, "manager", {**body, "relationship": "know_their_work"}, 409)
        self.create(request, "manager", self.referral_body(), 409)
        self.transition(initial, "manager", "revoke", 2, expected=409)
        self.transition(initial, "outsider", "decline", 1, expected=422,
                        body={"submission_id": str(uuid4()), "expected_version": 1, "share_confirmed": True})
        revoke = {"submission_id": str(uuid4()), "expected_version": 1, "share_confirmed": False}
        current = self.transition(initial, "manager", "revoke", 1, body=revoke)
        self.assertEqual((current["status"], current["version"]), ("revoked", 2))
        self.assertEqual(self.transition(initial, "manager", "revoke", 1, body=revoke), current)
        self.assertEqual(self.receipt(body, "manager"), current)
        self.assertEqual(self.create(request, "manager", body), current)
        self.assertEqual(self.inbox()["items"], [])
        self.transition(initial, "outsider", "accept", 1, expected=404)
        self.assertEqual(self.recipient_count(request), 2)
        with Session(self.engine) as db:
            self.assertEqual(db.query(NetworkServiceReferralEvent).count(), 2)

    def test_concurrent_offers_respect_capacity_and_decline_reopens_slot(self):
        request = self.opened()
        self.matching_profile()
        for index, key in enumerate(("spare_one", "spare_two", "spare_three"), 7100):
            self.extra_provider(key, index)
        self.create(request, "manager", self.referral_body("outsider"))
        self.create(request, "manager", self.referral_body("spare_one"))
        requests = (self.referral_body("spare_two"), self.referral_body("spare_three"))
        responses = self.parallel([
            lambda body=body: self.send("POST", f"/service-requests/{request['id']}/referrals", "manager", body)
            for body in requests
        ])
        self.assertCountEqual([response.status_code for response in responses], [201, 409])
        for response in responses:
            self.checked(response, response.status_code)
        self.assertFalse(self.referrals(request)["can_offer"])
        self.assertEqual(self.referrals(request)["total"], 3)
        self.assertEqual(self.candidates(request)["items"], [])
        self.assertEqual(self.recipient_count(request), 2)

        winner_index = next(index for index, response in enumerate(responses) if response.status_code == 201)
        winner = ("spare_two", "spare_three")[winner_index]
        offered = responses[winner_index].json()
        declined = self.transition(offered, winner, "decline", 1)
        self.assertEqual((declined["status"], declined["request_id"]), ("declined", None))
        self.assertTrue(self.referrals(request)["can_offer"])
        self.assertEqual(self.create(request, "manager", requests[1 - winner_index])["status"], "offered")
        self.assertEqual(self.recipient_count(request), 2)

    def test_locked_opt_in_and_current_published_match_are_required_for_acceptance(self):
        request = self.opened()
        profile_id = self.matching_profile(status="hidden")
        self.assertEqual(self.candidates(request)["items"], [])
        self.create(request, "manager", self.referral_body(), 409)
        with Session(self.engine) as db:
            db.get(ProfessionalProfile, profile_id).listing_status = "published"
            db.commit()
        offered = self.create(request, "manager", self.referral_body())
        self.assertTrue(self.inbox()["items"][0]["can_accept"])
        command = {"submission_id": str(uuid4()), "expected_version": 1, "share_confirmed": False}

        def accept():
            return self.send("POST", f"/service-referrals/{offered['id']}/accept", "outsider", command)

        with ThreadPoolExecutor(max_workers=1) as worker, Session(self.engine) as db:
            preference = db.query(NetworkServicePreference).filter_by(user_id=self.ids["outsider"]).with_for_update().one()
            pending = worker.submit(accept)
            with self.assertRaises(TimeoutError):
                pending.result(timeout=0.2)
            preference.service_opt_in = False
            db.commit()
            self.checked(pending.result(timeout=12), 409)
        self.receipt(command, "outsider", 404)
        self.assertEqual(self.recipient_count(request), 2)
        self.assertFalse(self.inbox()["items"][0]["can_accept"])

        self.opt("outsider")
        with Session(self.engine) as db:
            db.get(ProfessionalProfile, profile_id).listing_status = "suspended"
            db.commit()
        self.transition(offered, "outsider", "accept", 1, expected=409, body=command)
        self.assertFalse(self.inbox()["items"][0]["can_accept"])
        with Session(self.engine) as db:
            db.get(ProfessionalProfile, profile_id).listing_status = "published"
            db.commit()
        self.assertEqual(self.transition(offered, "outsider", "accept", 1, body=command)["status"], "accepted")
        self.assertEqual(self.recipient_count(request), 3)

    def test_sponsor_manager_and_custody_changes_recheck_existing_offers_and_receipts(self):
        request = self.opened()
        self.accepted(request, "pro1")
        self.matching_profile()
        nomination = self.referral_body(sharing=False)
        proposed = self.create(request, "pro1", nomination)
        offer_body = {"submission_id": str(uuid4()), "expected_version": 1, "share_confirmed": True}
        offered = self.transition(proposed, "manager", "offer", 1, body=offer_body)
        self.assertTrue(self.inbox()["items"][0]["can_accept"])
        self.opt("pro1", service_opt_in=False)
        self.receipt(nomination, "pro1", 404)
        self.assertFalse(self.inbox()["items"][0]["can_accept"])
        self.transition(offered, "outsider", "accept", 2, expected=409)

        self.opt("pro1")
        self.assertTrue(self.inbox()["items"][0]["can_accept"])
        with Session(self.engine) as db:
            db.get(Listing, self.listing).assigned_realtor_id = self.ids["new_manager"]
            db.commit()
        self.receipt(offer_body, "manager", 404)
        self.referrals(request, "manager", 404)
        self.assertFalse(self.inbox()["items"][0]["can_accept"])
        self.transition(offered, "outsider", "accept", 2, expected=409)
        self.assertEqual(self.referrals(request, "owner")["total"], 1)

        with Session(self.engine) as db:
            db.get(Listing, self.listing).owner_id = self.ids["new_manager"]
            db.commit()
        self.referrals(request, "owner", 404)
        self.receipt(nomination, "pro1", 404)
        self.assertEqual(self.inbox()["items"], [])
        self.transition(offered, "outsider", "accept", 2, expected=404)
        self.assertEqual(self.recipient_count(request), 2)
