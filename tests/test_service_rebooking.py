"""Read-only completed-service rebooking through real routes and disposable PostgreSQL."""
import os
import unittest

import test_service_review as review_fixture

from sqlalchemy.orm import Session

from models.listing import Listing
from models.property_network import NetworkAudit, NetworkProject, NetworkProjectUpdate
from models.service_review import NetworkServiceReview
from models.service_visit import NetworkServiceWork, NetworkServiceWorkAction
from models.service_workflow import NetworkServiceAction, NetworkServiceProposal, NetworkServiceRecipient, NetworkServiceRequest
from models.user import User


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class PrivateCompletedServiceRebooking(unittest.TestCase):
    _review = review_fixture.PrivateCompletedServiceReview
    _negotiation = _review._negotiation
    _conversation = _review._conversation
    _visit = _review._visit
    _workflow = _review._workflow
    cleanup_owned_schema = classmethod(_review.cleanup_owned_schema.__func__)
    setUp = _review.setUp
    patched = _review.patched
    stop_patches = _review.stop_patches
    send = _review.send
    ok = _review.ok
    opt = _review.opt
    private = _review.private
    request_body = _review.request_body
    request = _review.request
    received = _review.received
    accepted = _review.accepted
    terms = _review.terms
    quote = _review.quote
    award_body = _review.award_body
    awarded = _review.awarded
    work_path = _review.work_path
    confirm = _review.confirm
    proposal = _review.proposal
    command = _review.command
    change = _review.change
    work = _review.work
    request_completion = _review.request_completion
    approve = _review.approve
    completed = _review.completed
    review_path = _review.review_path
    assert_private = _review.assert_private
    assert_refused = _review.assert_refused

    @classmethod
    def setUpClass(cls):
        cls._review.setUpClass.__func__(cls)

    def context(self, path):
        response = self.send("GET", path)
        self.assert_private(response)
        return self.ok(response)

    def context_path(self, path):
        return path.rsplit("/", 1)[0] + "/rebook-context"

    def edit_registry(self, row, **updates):
        fields = {key: row[key] for key in ("title", "location", "address", "notes", "relationship", "property_class", "status")}
        return self.ok(self.send("PUT", f"/registry/{row['id']}", body={**fields, **updates, "expected_version": row["version"]}))

    def recorded_counts(self):
        with Session(self.engine) as db:
            return tuple(db.query(model).count() for model in (
                NetworkServiceRequest, NetworkServiceRecipient, NetworkServiceProposal, NetworkServiceAction,
                NetworkProject, NetworkProjectUpdate, NetworkServiceWork, NetworkServiceWorkAction,
                NetworkServiceReview, NetworkAudit,
            ))

    def test_current_listing_and_private_sources_use_current_provider_details(self):
        listing_path, _ = self.completed()
        private = self.private()
        request, _ = self.request(private)
        registry_path, _ = self.completed(request)
        self.edit_registry(private, title="Current private registry title")
        self.opt("pro1", member_alias="Current provider alias")
        current_code = self.codes["pro1"] + 1000
        with Session(self.engine) as db:
            db.get(Listing, self.listing).title = "Current listing title"
            db.get(User, self.ids["pro1"]).user_code = current_code
            db.commit()

        before = self.recorded_counts()
        for path, kind, identity, title in (
            (listing_path, "listing", str(self.listing), "Current listing title"),
            (registry_path, "private", private["id"], "Current private registry title"),
        ):
            self.assertEqual(self.context(self.context_path(path)), {
                "request_id": path.split("/")[2],
                "source": {"kind": kind, "id": identity, "title": title, "property_class": "house_villa"},
                "category_id": "pool_service", "provider": {"user_code": current_code, "member_alias": "Current provider alias"},
            })
        self.assertEqual(self.recorded_counts(), before)

    def test_only_current_custodian_of_owner_approved_award_can_rebook(self):
        request, _ = self.request(role="manager")
        work_path = self.work_path(request)
        path = self.context_path(work_path)
        self.assert_refused(self.send("GET", path), 404)
        self.awarded(request)
        self.assert_refused(self.send("GET", path), 404)
        requested = self.request_completion(work_path)
        self.assert_refused(self.send("GET", path), 404)
        self.approve(work_path, requested)
        self.assertEqual(self.context(path)["request_id"], request["id"])
        for actor in ("admin", "manager", "pro1", "pro2", "outsider"):
            self.assert_refused(self.send("GET", path, actor), 404)
        self.assert_refused(self.send("GET", path, "unknown"), 401)

    def test_opt_out_revocation_and_source_changes_close_context_without_writes(self):
        listing_path, project = self.completed()
        path = self.context_path(listing_path)
        self.context(path)
        for preference in ({"service_opt_in": False}, {"category_ids": ["plumbing"]}, {"property_class_ids": ["office"]}):
            self.opt("pro1", **preference)
            before = self.recorded_counts()
            self.assert_refused(self.send("GET", path), 404)
            self.assertEqual(self.recorded_counts(), before)
        self.opt("pro1")
        self.context(path)
        project_path = f"/projects/{project['id']}"
        self.ok(self.send("POST", f"{project_path}/members/{self.codes['pro1']}/revoke",
                          body={"expected_version": project["version"]}))
        before = self.recorded_counts()
        self.assert_refused(self.send("GET", path), 404)
        self.assertEqual(self.recorded_counts(), before)
        updated = self.ok(self.send("GET", project_path))
        self.ok(self.send("POST", f"{project_path}/members", body={"expected_version": updated["version"],
            "user_code": self.codes["pro1"], "role": "professional"}))
        self.context(path)
        with Session(self.engine) as db:
            db.get(Listing, self.listing).type = "condo"
            db.commit()
        self.assert_refused(self.send("GET", path), 404)
        with Session(self.engine) as db:
            db.get(User, self.ids["outsider"]).role = "owner"
            listing = db.get(Listing, self.listing)
            listing.type, listing.owner_id = "villa", self.ids["outsider"]
            db.commit()
        self.assert_refused(self.send("GET", path), 404)
        self.assert_refused(self.send("GET", path, "outsider"), 404)

        private = self.private()
        request, _ = self.request(private)
        registry_path, _ = self.completed(request)
        path = self.context_path(registry_path)
        self.context(path)
        archived = self.edit_registry(private, status="archived")
        self.assert_refused(self.send("GET", path), 404)
        self.edit_registry(archived, status="active", property_class="office")
        before = self.recorded_counts()
        self.assert_refused(self.send("GET", path), 404)
        self.assertEqual(self.recorded_counts(), before)


if __name__ == "__main__":
    unittest.main()
