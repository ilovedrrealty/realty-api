"""Scoped, read-only inquiry feeds in random disposable loopback schemas."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-owner-feed-only",
    GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="", SUPABASE_URL="",
    SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="")
import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from config import settings
from database import Base, get_db
import models
from models.user import User
from models.listing import Listing
from models.lead import Lead
from models.inquiry import Inquiry
from models.lead_delivery import LeadDeliveryJob
from routes import owner_leads, inquiries


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires disposable loopback Postgres")
class OwnerLeadFeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host not in ("127.0.0.1", "localhost", "::1") or url.get_backend_name() != "postgresql":
            raise ValueError("Owner feed tests require disposable loopback-only Postgres")
        cls.schema = "owner_feed_test_"+uuid4().hex
        cls.bootstrap = create_engine(url)
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path="+cls.schema})
        Base.metadata.create_all(cls.engine)
        app = FastAPI(); app.include_router(owner_leads.router); app.include_router(inquiries.router)
        def local_db():
            with Session(cls.engine) as db:
                yield db
        app.dependency_overrides[get_db] = local_db
        cls.client = TestClient(app)
        cls.ids, cls.headers, cls.listings = {}, {}, {}
        with Session(cls.engine) as db:
            for name, role, verified, status in [("owner_a","owner",True,"active"),("owner_b","owner",True,"active"),
                ("buyer_a","buyer",True,"active"),("buyer_b","buyer",True,"active"),("realtor","realtor",True,"active"),
                ("admin","admin",True,"active"),("unverified","owner",False,"active"),("suspended","owner",True,"suspended")]:
                user = User(email=name+"@example.invalid", display_name="Synthetic "+name, role=role,
                    email_verified=verified, status=status)
                db.add(user); db.flush(); cls.ids[name] = user.id
                token = jwt.encode({"sub":str(user.id),"exp":datetime.now(timezone.utc)+timedelta(hours=2)},settings.jwt_secret,algorithm="HS256")
                cls.headers[name] = {"Authorization":"Bearer "+token}
            for key, owner, submitter, status in [("owned","owner_a","owner_a","active"),("transferred","owner_b","owner_a","active"),
                ("legacy",None,"owner_a","active"),("foreign","owner_b","owner_b","active"),
                ("archived","owner_a","owner_a","archived"),("unowned_admin",None,"admin","active")]:
                listing = Listing(title="Synthetic "+key, type="villa", transaction="sale", price=199000, location="Synthetic area",
                    owner_id=cls.ids[owner] if owner else None, submitted_by=cls.ids[submitter], assigned_realtor_id=cls.ids["realtor"], status=status)
                db.add(listing); db.flush(); cls.listings[key] = listing.id
            now = datetime.now(timezone.utc)
            for index in range(55):
                db.add(Lead(type="property_inquiry",name="Synthetic request "+str(index),
                    email=None if index == 0 else "request@example.invalid", phone="+18095551234" if index == 0 else None,
                    message=None if index == 0 else "Synthetic comments", property_id=cls.listings["owned"],
                    from_user_id=cls.ids["buyer_a"], assigned_realtor_id=cls.ids["realtor"] if index == 1 else None,
                    status=("new","assigned","schedule","contacted","closed")[index%5], created_at=now-timedelta(seconds=index),
                    tracking={"private":"PRIVATE-ATTRIBUTION"},ghl_contact_id="PRIVATE-CRM",ghl_sync_error="PRIVATE-PROVIDER"))
            for key, status in [("legacy","new"),("archived","closed"),("transferred","new"),("foreign","new"),("unowned_admin","new")]:
                db.add(Lead(type="property_inquiry",name="Synthetic "+key,email="request@example.invalid",property_id=cls.listings[key],status=status,created_at=now-timedelta(days=1)))
            db.add(Lead(type="property_inquiry",name="Unknown status",phone="+18095551234",property_id=cls.listings["owned"],status="future_state",created_at=now-timedelta(days=2)))
            db.add(Lead(type="seller_interest",name="Excluded service request",email="request@example.invalid",property_id=cls.listings["owned"],status="new"))
            db.add(Lead(type="booking",name="Excluded booking",email="request@example.invalid",property_id=cls.listings["owned"],status="new"))
            db.add(Lead(type="property_inquiry",name="No property",email="request@example.invalid",status="new"))
            db.add(Inquiry(listing_id=cls.listings["owned"],from_user_id=cls.ids["buyer_a"],name="Previous synthetic request",email="previous@example.invalid",message=None))
            db.add(Inquiry(listing_id=cls.listings["foreign"],from_user_id=cls.ids["buyer_b"],name="Foreign previous request",email="previous@example.invalid",message="Previous foreign comments"))
            db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.client.close(); cls.engine.dispose()
        if not cls.schema.startswith("owner_feed_test_"):
            raise ValueError("Unexpected disposable schema")
        with cls.bootstrap.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.bootstrap.dispose()

    def read(self, query="", actor="owner_a", status=200):
        response = self.client.get("/owner/leads"+query, headers=self.headers.get(actor, {}))
        self.assertEqual(response.status_code,status,response.text)
        self.assertEqual(response.headers.get("cache-control"),"no-store")
        self.assertEqual(response.headers.get("pragma"),"no-cache")
        self.assertEqual(response.headers.get("vary"),"Cookie, Authorization")
        return response.json()

    def test_actual_role_verified_active_matrix(self):
        for actor, status in [(None,401),("buyer_a",403),("realtor",403),("unverified",403),("suspended",403)]:
            self.read(actor=actor,status=status)
        self.assertEqual(self.read(actor="owner_b")["total"],2)
        self.assertEqual(self.read(actor="admin")["total"],61)

    def test_scope_pagination_full_counts_and_no_private_metadata(self):
        first = self.read(); second = self.read("?page=2")
        self.assertEqual((len(first["items"]),len(second["items"]),first["total"],second["total"]),(50,8,58,58))
        self.assertEqual(first["summary"],{"all_total":58,"open_total":45,"closed_total":12,"unknown_total":1})
        self.assertEqual(first["summary"],second["summary"])
        all_rows = first["items"]+second["items"]
        self.assertEqual(len({row["id"] for row in all_rows}),58)
        self.assertEqual({row["property_id"] for row in all_rows},{str(self.listings[key]) for key in ("owned","legacy","archived")})
        self.assertNotIn("PRIVATE",str(all_rows))
        for row in all_rows:
            self.assertEqual(row["type"],"property_inquiry")
            self.assertFalse({"tracking","ghl_contact_url","ghl_sync_error","from_user_id","payload"}.intersection(row))
        self.assertTrue(any(row["email"] is None and row["phone"] for row in all_rows))
        self.assertTrue(any(row["phone"] is None and row["email"] for row in all_rows))
        self.assertTrue(any(row["status"] == "unknown" for row in all_rows))

    def test_open_closed_filter_totals_use_whole_scope(self):
        opened, closed = self.read("?state=open"),self.read("?state=closed")
        self.assertEqual((opened["total"],closed["total"]),(45,12))
        self.assertEqual(opened["summary"],closed["summary"])
        self.assertTrue(all(row["status"] in ("new","assigned","schedule","contacted") for row in opened["items"]))
        self.assertTrue(all(row["status"] == "closed" for row in closed["items"]))

    def test_former_submitter_cannot_read_transferred_property_or_enumerate(self):
        foreign = self.read("?property_id="+str(self.listings["transferred"]))
        missing = self.read("?property_id="+str(uuid4()))
        self.assertEqual(foreign,missing)
        self.assertEqual(foreign["total"],0)
        self.assertEqual(self.read("?role=admin&workspace=Admin")["total"],58)
        self.assertEqual(self.read("?property_id="+str(self.listings["transferred"]),actor="owner_b")["total"],1)

    def test_current_ownership_change_revokes_old_owner_and_exposes_to_new_owner(self):
        listing_id = self.listings["legacy"]
        try:
            with Session(self.engine) as db:
                db.get(Listing,listing_id).owner_id=self.ids["owner_b"]; db.commit()
            self.assertEqual(self.read("?property_id="+str(listing_id))["total"],0)
            self.assertEqual(self.read("?property_id="+str(listing_id),actor="owner_b")["total"],1)
        finally:
            with Session(self.engine) as db:
                db.get(Listing,listing_id).owner_id=None; db.commit()

    def test_bounds_errors_and_database_failure_are_controlled_private(self):
        for query in ("?page=0","?page=10001","?state=PRIVATE-SENTINEL","?property_id=PRIVATE-SENTINEL"):
            self.assertNotIn("PRIVATE-SENTINEL",str(self.read(query,status=422)))
        with patch.object(owner_leads,"owner_lead_page",side_effect=SQLAlchemyError("PRIVATE-DRIVER-PARAMETERS")):
            self.assertNotIn("PRIVATE",str(self.read(status=503)))

    def test_one_read_snapshot_no_new_jobs_no_status_mutation(self):
        statements=[]
        def capture(_conn,_cursor,statement,_parameters,_context,_many):
            statements.append(statement)
        event.listen(self.engine,"before_cursor_execute",capture)
        try:
            self.read()
        finally:
            event.remove(self.engine,"before_cursor_execute",capture)
        self.assertEqual(sum("owner_inquiry_scope AS" in statement for statement in statements),1)
        self.assertFalse(any("lead_delivery_jobs" in statement for statement in statements))
        self.assertTrue(all(statement.lstrip().lower().startswith(("select","with")) for statement in statements))
        self.assertEqual(self.client.post("/owner/leads",json={},headers=self.headers["owner_a"]).status_code,405)
        with Session(self.engine) as db:
            self.assertEqual(db.query(LeadDeliveryJob).count(),0)

    def test_legacy_history_is_separate_authorized_private_and_nullable(self):
        for path, actor, expected in [("/inquiries/mine","buyer_a",1),("/inquiries/for-owner","owner_a",1),("/inquiries/for-owner","owner_b",1)]:
            response = self.client.get(path,headers=self.headers[actor])
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.headers.get("cache-control"),"no-store")
            self.assertEqual(len(response.json()),expected)
            self.assertTrue(all("status" not in row for row in response.json()))
        for actor, expected in [(None,401),("buyer_a",403),("realtor",403),("unverified",403),("suspended",403)]:
            response=self.client.get("/inquiries/for-owner",headers=self.headers.get(actor,{}))
            self.assertEqual(response.status_code,expected,response.text)
            self.assertEqual(response.headers.get("cache-control"),"no-store")
        self.assertEqual(self.read()["total"],58)


if __name__ == "__main__":
    unittest.main()
