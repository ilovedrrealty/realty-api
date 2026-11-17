"""Real database tests use a fresh, disposable loopback schema and no providers."""
import os
import importlib.util
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-management-tests-only", GHL_ENABLED="false", RESEND_API_KEY="", PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="", SUPABASE_SERVICE_KEY="")
import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from alembic.migration import MigrationContext
from alembic.operations import Operations
from config import settings
from database import Base, get_db
import models
from models.user import User
from models.listing import Listing
from models.property_management import PropertyManagementRecord as Record, PropertyManagementRevision as Revision
from models.lead_delivery import LeadDeliveryJob
from routes import property_management
from services import property_management as service
from utils.limiter import limiter


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires disposable loopback Postgres")
class PropertyManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if url.host not in ("127.0.0.1", "localhost", "::1") or url.get_backend_name() != "postgresql":
            raise ValueError("Disposable loopback-only database required")
        cls.schema = "management_test_" + uuid4().hex
        cls.bootstrap = create_engine(url)
        with cls.bootstrap.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema}, pool_size=10)
        Base.metadata.create_all(cls.engine)
        app = FastAPI(); app.include_router(property_management.router)
        def local_db():
            with Session(cls.engine) as db:
                yield db
        app.dependency_overrides[get_db] = local_db
        cls.client = TestClient(app)
        cls.old_limiter = limiter.enabled; limiter.enabled = False
        cls.headers, cls.ids = {}, {}
        with Session(cls.engine) as db:
            for name, role, verified, status in [("owner", "owner", True, "active"), ("other", "owner", True, "active"), ("agent", "realtor", True, "active"), ("other_agent", "realtor", True, "active"), ("buyer", "buyer", True, "active"), ("admin", "admin", True, "active"), ("unverified", "owner", False, "active"), ("suspended", "owner", True, "suspended")]:
                user = User(email=name+"@example.com", display_name="Synthetic "+name, role=role, email_verified=verified, status=status)
                db.add(user); db.flush(); cls.ids[name] = user.id
                cls.headers[name] = {"Authorization": "Bearer " + jwt.encode({"sub": str(user.id), "exp": datetime.now(timezone.utc)+timedelta(hours=3)}, settings.jwt_secret, algorithm="HS256")}
            listing = Listing(title="Synthetic managed property", type="apartment", transaction="rent", price=1000, location="Synthetic area", submitted_by=cls.ids['owner'], owner_id=cls.ids['owner'], assigned_realtor_id=cls.ids['agent'], status="active")
            other = Listing(title="Other synthetic property", type="apartment", transaction="rent", price=1000, location="Other area", submitted_by=cls.ids['other'], owner_id=cls.ids['other'], status="active")
            db.add_all([listing, other]); db.flush(); cls.listing_id, cls.other_listing_id = listing.id, other.id; db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.client.close(); limiter.enabled = cls.old_limiter
        cls.engine.dispose()
        with cls.bootstrap.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.bootstrap.dispose()

    def setUp(self):
        with self.engine.begin() as conn:
            conn.execute(text(f'TRUNCATE "{self.schema}".property_management_revisions, "{self.schema}".property_management_records CASCADE'))
        self.clock = patch.object(service, 'today_dr', return_value=date(2026,10,25)); self.clock.start()

    def tearDown(self):
        self.clock.stop()

    def call(self, method, path, body=None, actor='owner', status=200):
        response = self.client.request(method, '/property-management'+path, json=body, headers=self.headers.get(actor, {}))
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.headers.get('cache-control'), 'no-store')
        return response.json()

    def body(self, kind='task', data=None, status=None, listing_id=None, **extra):
        defaults={
            'task': {'due_date':'2026-10-20'},
            'lease': {'tenant_name':'Synthetic tenant','start_date':'2026-09-01','end_date':'2027-08-31','rent':'1000','currency':'USD','due_day':1},
            'maintenance': {'estimated_cost':'100','currency':'USD','requires_approval':True},
            'inspection': {'due_date':'2026-10-25'},
            'ledger': {'direction':'income','category':'rent','amount':'1000','currency':'USD','transaction_date':'2026-10-20'},
        }
        statuses={'task':'open','lease':'active','maintenance':'open','inspection':'scheduled','ledger':'recorded'}
        return {'listing_id':str(listing_id or self.listing_id),'kind':kind,'title':'Synthetic '+kind,'status':status or statuses[kind],'data':{**defaults[kind],**(data or {})},**extra}

    def update_body(self, row, **changes):
        value={k:row[k] for k in ('listing_id','kind','title','status','data','origin_record_id')}
        value.update(expected_version=row['version']); value.update(changes)
        return value

    def test_scoped_properties_records_and_all_admin_access(self):
        for actor in ('owner','agent'):
            data=self.call('GET','/properties',actor=actor)
            self.assertEqual([x['id'] for x in data['items']],[str(self.listing_id)])
        self.assertEqual(self.call('GET','/properties',actor='admin')['total'],2)
        row=self.call('POST','/records',self.body(),status=201)
        self.call('GET','/records/'+row['id'],actor='agent')
        self.call('GET','/records/'+row['id'],actor='admin')
        for actor in ('other','other_agent'):
            self.call('GET','/records/'+row['id'],actor=actor,status=404)
            self.call('GET','/records/'+row['id']+'/history',actor=actor,status=404)
            self.call('POST','/records',self.body(),actor=actor,status=404)
        self.call('GET','/records?listing_id='+str(self.other_listing_id),status=404)

    def test_auth_and_private_error_responses(self):
        for actor,code in ((None,401),('buyer',403),('unverified',403),('suspended',403)):
            self.call('GET','/properties',actor=actor,status=code)
        invalid=self.body(data={'unexpected_private_field':'secret-private-note'})
        response=self.call('POST','/records',invalid,status=422)
        self.assertNotIn('secret-private-note',str(response))
        self.call('GET','/records/not-a-uuid',status=422)
        response=self.client.post('/property-management/records',content=b'x'*65537,headers={**self.headers['owner'],'Content-Type':'application/json'})
        self.assertEqual(response.status_code,413); self.assertEqual(response.headers.get('cache-control'),'no-store')

    def test_save_reload_version_conflict_and_history(self):
        row=self.call('POST','/records',self.body(data={'notes':'Private synthetic note'}),status=201)
        changed=self.call('PUT','/records/'+row['id'],self.update_body(row,title='Saved revision'))
        self.assertEqual(changed['version'],2)
        self.assertEqual(self.call('GET','/records/'+row['id'])['title'],'Saved revision')
        self.call('PUT','/records/'+row['id'],self.update_body(row,title='Stale'),status=409)
        history=self.call('GET','/records/'+row['id']+'/history')
        self.assertEqual(history['total'],2)
        self.assertEqual([x['version'] for x in history['items']],[2,1])

    def test_overlapping_active_leases_and_units(self):
        self.call('POST','/records',self.body('lease',{'unit_reference':'A-1'}),status=201)
        self.call('POST','/records',self.body('lease',{'unit_reference':'a-1','start_date':'2027-01-01'}),status=409)
        self.call('POST','/records',self.body('lease',{'unit_reference':'B-1'}),status=201)
        self.call('POST','/records',self.body('lease',{'unit_reference':'A-1','start_date':'2027-09-01','end_date':'2028-08-31'}),status=201)
        self.call('POST','/records',self.body('lease',{'end_date':None},status='ended'),status=422)

    def test_concurrent_lease_creation_cannot_double_occupy(self):
        def save():
            response=self.client.post('/property-management/records',json=self.body('lease'),headers=self.headers['owner'])
            return response.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=sorted(pool.map(lambda _:save(),range(2)))
        self.assertEqual(statuses,[201,409])

    def test_owner_approval_budget_invalidation_and_no_agent_bypass(self):
        row=self.call('POST','/records',self.body('maintenance'),actor='agent',status=201)
        self.assertEqual(row['approval_status'],'pending')
        self.call('PUT','/records/'+row['id'],self.update_body(row,status='in_progress'),actor='agent',status=409)
        self.call('POST','/records/'+row['id']+'/approval',{'expected_version':1,'decision':'approved'},actor='agent',status=403)
        approved=self.call('POST','/records/'+row['id']+'/approval',{'expected_version':1,'decision':'approved','limit':'120','comment':'Synthetic owner approval'})
        moving=self.call('PUT','/records/'+row['id'],self.update_body(approved,status='in_progress'),actor='agent')
        changed=self.call('PUT','/records/'+row['id'],self.update_body(moving,data={**moving['data'],'estimated_cost':'180'}),actor='agent')
        self.assertEqual((changed['approval_status'],changed['status']),('pending','awaiting_approval'))
        self.call('PUT','/records/'+row['id'],self.update_body(changed,data={**changed['data'],'requires_approval':False}),actor='agent',status=403)
        self.call('POST','/records/'+row['id']+'/approval',{'expected_version':changed['version'],'decision':'approved','limit':'100'},status=422)
        approved=self.call('POST','/records/'+row['id']+'/approval',{'expected_version':changed['version'],'decision':'approved','limit':'200'})
        over=self.call('PUT','/records/'+row['id'],self.update_body(approved,status='completed',data={**approved['data'],'actual_cost':'250'}),actor='agent')
        self.assertEqual((over['approval_status'],over['status']),('pending','awaiting_approval'))

    def test_financial_statements_separate_currencies_deposits_and_plans(self):
        for data,status in [({'amount':'1000.10'},'recorded'),({'amount':'100.05','direction':'expense','category':'maintenance'},'recorded'),({'amount':'500','category':'deposit'},'recorded'),({'amount':'30000','currency':'DOP'},'recorded'),({'amount':'99999'},'planned'),({'amount':'77777'},'void')]:
            self.call('POST','/records',self.body('ledger',data,status=status),status=201)
        report=self.call('GET',f'/properties/{self.listing_id}/statement?start=2026-10-01&end=2026-10-25')
        usd=report['totals']['USD']; dop=report['totals']['DOP']
        self.assertEqual(Decimal(usd['income']),Decimal('1000.10'))
        self.assertEqual(Decimal(usd['expenses']),Decimal('100.05'))
        self.assertEqual(Decimal(usd['net']),Decimal('900.05'))
        self.assertEqual(Decimal(usd['deposit_cash']),Decimal('500'))
        self.assertEqual(Decimal(usd['deposit_balance']),Decimal('500'))
        self.assertEqual(Decimal(dop['income']),Decimal('30000'))
        self.assertEqual(len(report['entries']),4)

    def test_rent_period_allocation_and_native_lease_currency(self):
        lease=self.call('POST','/records',self.body('lease'),status=201)
        self.call('POST','/records',self.body('ledger',{'lease_id':lease['id'],'rent_period':'2026-10-01','transaction_date':'2026-09-25','amount':'400'}),status=201)
        report=self.call('GET',f'/properties/{self.listing_id}/statement?start=2026-10-01&end=2026-10-25')
        self.assertEqual(Decimal(report['rent_due'][0]['outstanding']),Decimal('600'))
        self.assertTrue(report['rent_due'][0]['overdue'])
        self.assertEqual(report['entries'],[])
        self.call('POST','/records',self.body('ledger',{'lease_id':lease['id'],'currency':'DOP'}),status=422)
        self.call('POST','/records',self.body('ledger',{'rent_period':'2026-10-01'}),status=422)
        self.call('POST','/records',self.body('ledger',{'transaction_date':'2026-11-01'}),status=422)

    def test_first_period_and_expired_renewals_remain_visible(self):
        lease=self.call('POST','/records',self.body('lease',{'start_date':'2026-10-15','first_rent':'500','due_day':31}),status=201)
        report=self.call('GET',f'/properties/{self.listing_id}/statement?start=2026-10-01&end=2026-10-25')
        self.assertEqual((report['rent_due'][0]['due_date'],Decimal(report['rent_due'][0]['expected'])),('2026-10-15',Decimal('500')))
        self.call('PUT','/records/'+lease['id'],self.update_body(lease,data={**lease['data'],'start_date':'2026-01-01','end_date':'2026-08-31','first_rent':None}))
        self.assertEqual(len(self.call('GET',f'/properties/{self.listing_id}/statement')['renewals']),1)

    def test_inspection_checks_issue_notes_and_safe_evidence(self):
        self.call('POST','/records',self.body('inspection',status='completed'),status=422)
        checks={k:'pass' for k in ('entrance','plumbing','electrical','cooling','appliances','safety','meters','condition')}
        checks['plumbing']='issue'
        self.call('POST','/records',self.body('inspection',{'checks':checks},status='completed'),status=422)
        row=self.call('POST','/records',self.body('inspection',{'checks':checks,'item_notes':{'plumbing':'Synthetic repair needed'},'evidence_links':['https://example.com/inspection']},status='completed'),status=201)
        self.assertEqual(row['status'],'completed')
        response=self.call('POST','/records',self.body('inspection',{'evidence_links':['https://user:secret@example.com/file']}),status=422)
        self.assertNotIn('secret',str(response))

    def test_recurring_month_end_anchor_and_idempotent_next(self):
        first=self.call('POST','/records',self.body(data={'due_date':'2026-01-31','recurrence':'monthly'},status='completed'),status=201)
        child=self.call('POST','/records/'+first['id']+'/schedule-next',{'expected_version':first['version']})
        duplicate=self.call('POST','/records/'+first['id']+'/schedule-next',{'expected_version':first['version']})
        self.assertEqual(child['id'],duplicate['id']); self.assertEqual(child['due_date'],'2026-02-28')
        done=self.call('PUT','/records/'+child['id'],self.update_body(child,status='completed'))
        march=self.call('POST','/records/'+done['id']+'/schedule-next',{'expected_version':done['version']})
        self.assertEqual(march['due_date'],'2026-03-31')

    def test_ownership_changes_revoke_previous_submitter_access(self):
        row=self.call('POST','/records',self.body(),status=201)
        try:
            with Session(self.engine) as db:
                listing=db.get(Listing,self.listing_id); listing.owner_id=self.ids['other']; db.commit()
            self.call('GET','/records/'+row['id'],status=404)
            self.call('GET','/records/'+row['id'],actor='other')
        finally:
            with Session(self.engine) as db:
                db.get(Listing,self.listing_id).owner_id=self.ids['owner']; db.commit()

    def test_read_only_operations_never_enqueue_or_modify_records(self):
        row=self.call('POST','/records',self.body(),status=201)
        self.call('GET','/properties')
        self.call('GET','/records?listing_id='+str(self.listing_id))
        self.call('GET',f'/properties/{self.listing_id}/statement')
        self.call('GET','/records/'+row['id']+'/history')
        with Session(self.engine) as db:
            self.assertEqual(db.query(LeadDeliveryJob).count(),0)
            self.assertEqual(db.query(Record).count(),1)
            self.assertEqual(db.query(Revision).count(),1)

    def test_scope_changes_reset_approval_but_progress_and_money_format_do_not(self):
        row=self.call('POST','/records',self.body('maintenance',{'notes':'Repair the faucet'}),actor='agent',status=201)
        approved=self.call('POST','/records/'+row['id']+'/approval',{'expected_version':row['version'],'decision':'approved'})
        progress=self.call('PUT','/records/'+row['id'],self.update_body(approved,status='in_progress',data={**approved['data'],'estimated_cost':'100.00','progress_notes':'Arrived on site'}),actor='agent')
        self.assertEqual(progress['approval_status'],'approved')
        scoped=self.call('PUT','/records/'+row['id'],self.update_body(progress,title='Different work',data={**progress['data'],'notes':'Replace the roof','category':'roof'}),actor='agent')
        self.assertEqual((scoped['approval_status'],scoped['status']),('pending','awaiting_approval'))

    def test_recurring_origins_cannot_be_forged_and_rescheduled_child_is_reused(self):
        parent=self.call('POST','/records',self.body(data={'due_date':'2026-01-31','recurrence':'monthly'},status='completed'),status=201)
        self.call('POST','/records',self.body('ledger',{'transaction_date':'2026-02-28'},status='planned',origin_record_id=parent['id']),status=422)
        child=self.call('POST','/records/'+parent['id']+'/schedule-next',{'expected_version':parent['version']})
        changed=self.call('PUT','/records/'+child['id'],self.update_body(child,data={**child['data'],'due_date':'2026-03-05'}))
        repeated=self.call('POST','/records/'+parent['id']+'/schedule-next',{'expected_version':parent['version']})
        self.assertEqual((repeated['id'],repeated['due_date']),(changed['id'],'2026-03-05'))
        self.assertEqual(self.call('GET','/records?listing_id='+str(self.listing_id)+'&kind=task')['total'],2)
        inspection=self.call('POST','/records',self.body('inspection'),status=201)
        for title in ('First repair','Second repair'):
            self.call('POST','/records',self.body('maintenance',{'due_date':'2026-10-25'},origin_record_id=inspection['id'],title=title),status=201)

    def test_database_failures_are_private_and_sanitized(self):
        from sqlalchemy.exc import SQLAlchemyError
        with patch.object(service,'listings_for',side_effect=SQLAlchemyError('secret-tenant-parameter')):
            response=self.call('GET','/properties',status=503)
        self.assertNotIn('secret-tenant-parameter',str(response))

    def test_supported_date_edges_fail_safely_without_overflow(self):
        task=self.call('POST','/records',self.body(data={'due_date':'9999-12-31','recurrence':'yearly'},status='completed'),status=201)
        self.call('POST','/records/'+task['id']+'/schedule-next',{'expected_version':task['version']},status=422)
        lease=self.call('POST','/records',self.body('lease',{'start_date':'9999-12-01','end_date':'9999-12-31','due_day':31}),status=201)
        report=self.call('GET',f'/properties/{self.listing_id}/statement?start=9999-12-01&end=9999-12-31')
        self.assertEqual(len(report['rent_due']),1)
        self.assertEqual(report['rent_due'][0]['lease_id'],lease['id'])
        self.assertEqual(report['rent_due'][0]['due_date'],'9999-12-01')

    def test_cookie_mutations_require_json_and_trusted_origin(self):
        token=self.headers['owner']['Authorization'].split(' ',1)[1]
        cookie={'Cookie':'ildr_token='+token}
        path='/property-management/records'
        for headers,content,expected in [
            (cookie,'{}',415),
            ({**cookie,'Content-Type':'application/json'},'{}',403),
            ({**cookie,'Content-Type':'application/json','Authorization':'Bearer '},'{}',403),
            ({**cookie,'Content-Type':'application/json','Origin':'https://untrusted.example'},'{}',403),
            ({**cookie,'Content-Type':'application/json','Origin':'null'},'{}',403),
        ]:
            response=self.client.post(path,content=content,headers=headers)
            self.assertEqual(response.status_code,expected)
            self.assertEqual(response.headers.get('cache-control'),'no-store')
        with Session(self.engine) as db:
            self.assertEqual(db.query(Record).count(),0)
        response=self.client.post(path,json=self.body(),headers={**cookie,'Origin':settings.origins_list[0]})
        self.assertEqual(response.status_code,201,response.text)

    def test_private_migration_repeat_and_forward_rollback_retention(self):
        row=self.call('POST','/records',self.body(),status=201)
        path=Path(__file__).resolve().parents[1]/'alembic/versions/c90e75f2a6d1_private_property_management.py'
        spec=importlib.util.spec_from_file_location('management_migration',path); migration=importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        with self.engine.begin() as conn:
            with patch.object(migration,'op',Operations(MigrationContext.configure(conn))):
                migration.upgrade(); migration.upgrade(); migration.downgrade()
            for table in ('property_management_records','property_management_revisions'):
                self.assertTrue(conn.execute(text('SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND c.relname=:table'),{'schema':self.schema,'table':table}).scalar_one())
                for role in ('anon','authenticated'):
                    self.assertFalse(conn.execute(text("SELECT has_table_privilege(:role,:table,'SELECT')"),{'role':role,'table':self.schema+'.'+table}).scalar())
        self.assertEqual(self.call('GET','/records/'+row['id'])['version'],1)


if __name__=='__main__':
    unittest.main()
