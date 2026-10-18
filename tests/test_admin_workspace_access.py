"""Admin personal workspaces retain identity and ownership boundaries.

Uses synthetic records in a random schema on a disposable loopback Postgres.
No production accounts, credentials, messages, payments or external APIs.
"""
import os
import re
import unittest
from uuid import UUID, uuid4

os.environ.update(
    DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-admin-workspace-only",
    GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", RESEND_API_KEY="",
    SUPABASE_URL="", SUPABASE_SERVICE_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="",
)

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from database import Base, get_db
from models.user import User
from models.listing import Listing
from models.inquiry import Inquiry
from models.saved_home import SavedHome
from models.booking import Booking
from models.lead import Lead
from routes import auth, admin, listings, inquiries, saved_homes, bookings, leads
from utils.auth import get_current_user


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires disposable local Postgres")
class AdminWorkspaceAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host not in ("127.0.0.1", "localhost", "::1") or url.get_backend_name() != "postgresql":
            raise ValueError("Tests require a disposable loopback-only Postgres")
        cls.schema = "admin_workspace_test_" + uuid4().hex
        cls.engine = create_engine(url)
        cls.connection = cls.engine.connect()
        cls.connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.connection.execute(text(f'SET search_path TO "{cls.schema}"'))
        Base.metadata.create_all(cls.connection)
        cls.connection.commit()
        cls.db = Session(cls.connection)
        cls.accounts = {}
        for n, (key, role) in enumerate((('admin', 'admin'), ('other_admin', 'admin'), ('buyer', 'buyer'), ('owner', 'owner'), ('realtor', 'realtor')), 1000):
            user = User(id=UUID(int=n), email=f"{key}@example.invalid", display_name=key, role=role)
            cls.accounts[key] = user
            cls.db.add(user)
        cls.db.flush()
        cls.accounts['admin'].assigned_realtor_id = cls.accounts['realtor'].id
        own_admin = cls.accounts['admin'].id
        other_realtor = cls.accounts['realtor'].id
        fixture_ownership = [
            (1, own_admin, None, None),
            (2, other_realtor, own_admin, None),
            (3, other_realtor, None, own_admin),
            (4, cls.accounts['owner'].id, cls.accounts['owner'].id, other_realtor),
            (5, cls.accounts['other_admin'].id, None, None),
        ]
        for n, submitter, owner_id, realtor_id in fixture_ownership:
            cls.db.add(Listing(id=UUID(int=n), title=f"Synthetic home {n}", location="Sosúa", type="villa", transaction="rent", currency="USD", price=1000, price_per_month=1000, images=[], features=[], status="active", submitted_by=submitter, owner_id=owner_id, assigned_realtor_id=realtor_id))
        cls.db.flush()
        for n, listing_id, from_user in ((101, 4, own_admin), (102, 1, cls.accounts['buyer'].id), (103, 2, cls.accounts['buyer'].id), (104, 3, cls.accounts['buyer'].id), (105, 5, cls.accounts['buyer'].id)):
            cls.db.add(Inquiry(id=UUID(int=n), listing_id=UUID(int=listing_id), name=f"Synthetic inquiry {n}", email="inquiry@example.invalid", message="Synthetic inquiry only", from_user_id=from_user))
        cls.db.add_all([
            SavedHome(user_id=own_admin, listing_id=UUID(int=4)),
            SavedHome(user_id=cls.accounts['buyer'].id, listing_id=UUID(int=1)),
            Booking(id=UUID(int=201), listing_id=UUID(int=4), buyer_id=own_admin, check_in="2027-01-01", check_out="2027-01-02"),
            Booking(id=UUID(int=202), listing_id=UUID(int=1), buyer_id=cls.accounts['buyer'].id, check_in="2027-01-01", check_out="2027-01-02"),
            Booking(id=UUID(int=203), listing_id=UUID(int=2), buyer_id=cls.accounts['buyer'].id, check_in="2027-01-01", check_out="2027-01-02"),
            Booking(id=UUID(int=204), listing_id=UUID(int=5), buyer_id=cls.accounts['buyer'].id, check_in="2027-01-01", check_out="2027-01-02"),
            Lead(id=UUID(int=301), type="buyer_interest", name="Admin personal lead", email="lead@example.invalid", from_user_id=own_admin, assigned_realtor_id=own_admin),
            Lead(id=UUID(int=302), type="buyer_interest", name="Other personal lead", email="otherlead@example.invalid", from_user_id=cls.accounts['buyer'].id, assigned_realtor_id=other_realtor),
        ])
        cls.db.commit()
        app = FastAPI()
        for router in (auth.router, admin.router, listings.router, inquiries.router, saved_homes.router, bookings.router, leads.router):
            app.include_router(router)
        cls.current_user = cls.accounts['admin']
        app.dependency_overrides[get_current_user] = lambda: cls.current_user
        app.dependency_overrides[get_db] = lambda: cls.db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.db.close()
        cls.connection.rollback()
        assert re.fullmatch(r'admin_workspace_test_[0-9a-f]{32}', cls.schema)
        cls.connection.execute(text('SET search_path TO public'))
        cls.connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.connection.commit()
        cls.connection.close()
        cls.engine.dispose()

    def setUp(self):
        self.__class__.current_user = self.accounts['admin']

    def get(self, path, expected=200):
        response = self.client.get(path)
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    @staticmethod
    def ids(rows, key='id'):
        return {UUID(row[key]).int for row in rows}

    def test_admin_identity_is_unchanged_in_every_workspace(self):
        for workspace in ('admin', 'buyer', 'owner', 'realtor'):
            result = self.get('/auth/me?workspace=' + workspace)
            self.assertEqual(result['role'], 'admin')
            self.assertEqual(result['id'], str(self.accounts['admin'].id))

    def test_admin_own_listings_union_and_pagination_exclude_other_accounts(self):
        self.assertEqual(self.ids(self.get('/listings/mine')), {1, 2, 3})
        result = self.get('/listings/mine/page?page_size=2')
        second = self.get('/listings/mine/page?page_size=2&page=2')
        self.assertEqual(result['total'], 3)
        self.assertEqual(second['total'], 3)
        self.assertEqual(self.ids(result['items']) | self.ids(second['items']), {1, 2, 3})
        self.assertFalse(self.ids(result['items']) & self.ids(second['items']))
        self.assertEqual(self.ids(self.get('/listings/mine/page?q=home%202')['items']), {2})

    def test_every_admin_has_self_access_without_receiving_another_admins_records(self):
        self.__class__.current_user = self.accounts['other_admin']
        self.assertEqual(self.ids(self.get('/listings/mine')), {5})
        self.assertEqual(self.get('/saved-homes/mine'), [])
        self.assertEqual(self.get('/inquiries/mine'), [])

    def test_personal_saved_homes_inquiries_bookings_and_leads_remain_private(self):
        self.assertEqual(self.ids(self.get('/saved-homes/mine'), 'listing_id'), {4})
        self.assertEqual(self.ids(self.get('/inquiries/mine')), {101})
        self.assertEqual(self.ids(self.get('/bookings/mine')), {201})
        self.assertEqual(self.ids(self.get('/leads/mine')), {301})
        self.assertEqual(self.ids(self.get('/realtor/leads')), {301})

    def test_owner_and_realtor_activity_uses_only_admin_related_properties(self):
        self.assertEqual(self.ids(self.get('/inquiries/for-owner')), {102, 103})
        self.assertEqual(self.ids(self.get('/inquiries/for-realtor')), {102, 104})
        self.assertEqual(self.ids(self.get('/bookings/for-owner')), {202, 203})
        agent = self.get('/auth/me/agent')
        self.assertEqual(agent['realtor_id'], str(self.accounts['realtor'].id))

    def test_workspace_parameter_never_grants_non_admin_privileges(self):
        self.__class__.current_user = self.accounts['buyer']
        for path in ('/listings/mine', '/listings/mine/page', '/admin/settings', '/admin/leads'):
            self.get(path + '?workspace=admin', expected=403)
        self.assertEqual(self.get('/auth/me?workspace=admin')['role'], 'buyer')
        self.__class__.current_user = self.accounts['owner']
        self.assertEqual(self.ids(self.get('/listings/mine?workspace=admin')), {4})
        self.get('/admin/settings?workspace=admin', expected=403)

    def test_admin_can_request_deal_for_own_property_but_not_unrelated_property(self):
        body = {'discount_value': 5, 'discount_type': 'pct', 'message': 'Synthetic test only'}
        for listing_id in (1, 2, 3):
            response = self.client.post(f'/listings/{UUID(int=listing_id)}/deal-request', json=body)
            self.assertEqual(response.status_code, 201, response.text)
        response = self.client.post(f'/listings/{UUID(int=4)}/deal-request', json=body)
        self.assertEqual(response.status_code, 403, response.text)
        self.__class__.current_user = self.accounts['buyer']
        response = self.client.post(f'/listings/{UUID(int=4)}/deal-request?workspace=admin', json=body)
        self.assertEqual(response.status_code, 403, response.text)
