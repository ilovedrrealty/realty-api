"""Owned HTTP/PG definitions only; no live model, email, customers or startup.

Run separately with a disposable fixed loopback DB. TestClient calls the actual
main app without a lifespan context. Synthetic peer addresses isolate real rate
limits between tests; no limiter disabling or fabricated API response is used.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
import threading
import unittest
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

os.environ.update(DATABASE_URL="sqlite+pysqlite:///:memory:", JWT_SECRET="synthetic-public-assistant-http-signing-key-" + "x" * 32,
                  ENVIRONMENT="development", ALLOWED_ORIGINS="http://testserver", RESEND_API_KEY="",
                  GHL_ENABLED="false", GHL_API_KEY="", GHL_LOCATION_ID="", SUPABASE_URL="", SUPABASE_SERVICE_KEY="",
                  PAYPAL_CLIENT_ID="", PAYPAL_CLIENT_SECRET="", LEAD_DELIVERY_ENABLED="false", CUSTOMER_MESSAGES_ENABLED="false",
                  PUBLIC_ASSISTANT_ENABLED="false", PUBLIC_ASSISTANT_CONTACTS_ENABLED="false",
                  PUBLIC_ASSISTANT_GATEWAY_KEY="", PUBLIC_ASSISTANT_NO_NEW_SPEND_VERIFIED="false")

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from config import settings
from database import Base
import models
import models.trade_contact
from main import app
from models.lead import Lead
from models.lead_delivery import LeadDeliveryJob
from models.listing import Listing
from models.site_settings import SiteSettings
from models.user import User
from routes import leads
from services import assistant_gateway_transport as gateway
from services import public_assistant_http as boundary
from services import public_assistant_lead_commands as commands
from services import public_assistant_listing_reads as readers
from services import public_assistant_orchestrator as orchestrator
from services import lead_delivery
from utils.limiter import limiter


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires owned disposable PostgreSQL")
class PublicAssistantHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
        if (url.get_backend_name() != "postgresql" or url.host != "127.0.0.1" or url.port != 55449
                or url.database != "ildr_platform_test_20261025"):
            raise ValueError("Owned fixed loopback test database required")
        cls.schema = "assistant_http_test_" + uuid4().hex
        bootstrap = create_engine(url)
        try:
            with bootstrap.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        finally:
            bootstrap.dispose()
        cls.engine = create_engine(url, connect_args={"options": "-csearch_path=" + cls.schema}, pool_size=8)
        Base.metadata.create_all(cls.engine)
        cls.factory = sessionmaker(bind=cls.engine, autoflush=False)

    @classmethod
    def tearDownClass(cls):
        try:
            with cls.engine.begin() as connection:
                connection.execute(text("SET search_path TO public"))
                assert cls.schema.startswith("assistant_http_test_") and len(cls.schema) == len("assistant_http_test_") + 32
                UUID(cls.schema[-32:])
                connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        finally:
            cls.engine.dispose()

    def setUp(self):
        with self.engine.begin() as connection:
            names = [table.name for table in Base.metadata.sorted_tables]
            self.assertTrue(all(name.replace("_", "").isalnum() for name in names))
            connection.execute(text("TRUNCATE " + ",".join('"' + name + '"' for name in names)))
        with self.factory() as db:
            user = User(email="synthetic-http-owner@example.com", display_name="Owned HTTP owner", role="owner", status="active", email_verified=True)
            db.add(user); db.flush()
            listing = Listing(title="Owned public HTTP home", type="villa", transaction="sale", price=120000,
                              location="Synthetic town", status="active", submitted_by=user.id,
                              owner_paypal_email="PRIVATE-owned-payment-canary")
            db.add_all([listing, SiteSettings(id=1, data={"notify_email": "owned-team@example.com"})]); db.commit()
            self.listing_id = str(listing.id)
        for name, value in {"environment":"development", "allowed_origins":"http://testserver",
                            "public_assistant_enabled":False, "public_assistant_contacts_enabled":False,
                            "public_assistant_no_new_spend_verified":False, "public_assistant_model":"openai/owned-test",
                            "public_assistant_gateway_key":"synthetic-owned-key", "public_assistant_verified_models":"openai/owned-test",
                            "lead_delivery_enabled":True, "customer_messages_enabled":True}.items():
            self.enterContext(patch.object(settings, name, value))
        self.clock = self.enterContext(patch.object(boundary, "_now", return_value=1800000000))
        self.enterContext(patch.object(limiter, "enabled", True))
        self.enterContext(patch.object(commands, "_now", return_value=1800000000))
        self.enterContext(patch.object(commands, "_backend", return_value=(leads.LeadCreate, leads._persist_submission, self.factory)))
        self.enterContext(patch.object(readers, "_new_session", side_effect=self.factory))
        self.inline = self.enterContext(patch.object(leads, "send_lead_notification", side_effect=AssertionError("provider_forbidden")))
        self.crm = self.enterContext(patch.object(leads.ghl, "create_contact", side_effect=AssertionError("provider_forbidden")))
        self.worker = self.enterContext(patch.object(lead_delivery, "worker_loop", side_effect=AssertionError("worker_forbidden")))
        self.network = self.enterContext(patch.object(httpx.AsyncHTTPTransport, "handle_async_request", side_effect=AssertionError("live_model_forbidden")))
        self.addCleanup(self.assert_no_live_work)
        self.client = self.client_for()
        self.headers = {"Origin":"http://testserver"}

    def assert_no_live_work(self):
        for observed in (self.inline, self.crm, self.worker, self.network):
            observed.assert_not_called()

    def client_for(self, *, base_url="http://testserver"):
        peer = "owned_" + uuid4().hex
        async def isolated_peer(scope, receive, send):
            if scope["type"] == "http":
                scope = {**scope, "client":(peer, 50000)}
            await app(scope, receive, send)
        client = TestClient(isolated_peer, base_url=base_url)
        self.addCleanup(client.close)
        return client

    def session(self, client=None, origin="http://testserver"):
        client = client or self.client
        reply = client.post("/public/assistant/session", headers={"Origin":origin}, json={})
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.headers["cache-control"], "no-store")
        return {"Origin":origin, "X-Assistant-CSRF":reply.json()["csrf_token"]}, reply

    def draft(self, **changes):
        return {"intent":"buy", "name":" Owned customer ", "email":"visitor@example.com", "phone":None,
                "message":"Please contact me about my search.", "contact_consent":True, **changes}

    def review(self, **changes):
        settings.public_assistant_contacts_enabled = True
        headers, _ = self.session()
        reply = self.client.post("/public/assistant/contact-review", headers=headers, json={"locale":"en", "draft":self.draft(**changes)})
        self.assertEqual(reply.status_code, 200)
        return headers, reply.json()

    def confirm(self, headers, prepared, client=None):
        return (client or self.client).post("/public/assistant/contact-confirm", headers=headers,
            json={"review":prepared["review"], "confirmation_token":prepared["confirmation_token"], "customer_confirmed":True})

    def counts(self):
        with self.factory() as db:
            return db.query(Lead).count(), db.query(LeadDeliveryJob).count()

    def test_default_disabled_session_and_routes_never_invoke_model_or_write(self):
        headers, reply = self.session()
        self.assertFalse(reply.json()["model_enabled"]); self.assertFalse(reply.json()["contact_enabled"])
        with patch.object(orchestrator, "run_public_turn", side_effect=AssertionError("disabled_model_called")) as model:
            for path, value in [("turn", {"locale":"en", "message":"Find a villa"}),
                                ("contact-review", {"locale":"en", "draft":self.draft()})]:
                denied = self.client.post("/public/assistant/"+path, headers=headers, json=value)
                self.assertEqual(denied.status_code, 503); self.assertEqual(denied.json(), {"error":"assistant_disabled"})
            model.assert_not_called()
        self.assertEqual(self.counts(), (0, 0))

    def test_session_is_host_only_httponly_production_secure_lax_and_reused(self):
        settings.environment = "production"; settings.allowed_origins = "https://ilovedrrealty.com"
        client = self.client_for(base_url="https://api.ilovedrrealty.com")
        first_headers, first = self.session(client, "https://ilovedrrealty.com")
        _, second = self.session(client, "https://ilovedrrealty.com")
        cookie = first.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie); self.assertIn("secure", cookie); self.assertIn("samesite=lax", cookie)
        self.assertIn("path=/public/assistant", cookie); self.assertNotIn("domain=", cookie)
        self.assertEqual(first.json()["csrf_token"], second.json()["csrf_token"])
        self.assertEqual(first.json()["session_expires_at"], second.json()["session_expires_at"])

    def test_origin_missing_null_foreign_duplicate_and_production_http_refused(self):
        for headers in ({}, {"Origin":"null"}, {"Origin":"https://foreign.example"},
                        [("Origin","http://testserver"),("Origin","http://testserver")]):
            reply=self.client.post("/public/assistant/session",headers=headers,json={})
            self.assertEqual(reply.status_code,403); self.assertEqual(reply.json(),{"error":"assistant_request_refused"})
        settings.environment="production"
        self.assertEqual(self.client.post("/public/assistant/session",headers=self.headers,json={}).status_code,403)

    def test_no_session_wrong_csrf_and_other_customer_token_cannot_confirm(self):
        headers, prepared=self.review()
        self.assertEqual(self.confirm({"Origin":"http://testserver"},prepared).status_code,403)
        other=self.client_for(); other_headers,_=self.session(other)
        self.assertEqual(self.confirm(other_headers,prepared,other).status_code,409)
        other_headers["X-Assistant-CSRF"]=headers["X-Assistant-CSRF"]
        self.assertEqual(self.confirm(other_headers,prepared,other).status_code,403)
        self.assertEqual(self.counts(),(0,0))

    def test_expired_and_tampered_session_inputs_are_finite(self):
        headers,_=self.session()
        token=self.client.cookies.get(boundary.COOKIE)
        tampered=token[:-1]+("0" if token[-1]!="0" else "1")
        self.assertEqual(self.client.post("/public/assistant/turn",headers={**headers,"Cookie":boundary.COOKIE+"="+tampered},json={"locale":"en","message":"help"}).status_code,403)
        self.client.cookies.clear();headers,_=self.session();self.clock.return_value+=3601
        self.assertEqual(self.client.post("/public/assistant/turn",headers=headers,json={"locale":"en","message":"help"}).status_code,403)

    def test_duplicate_unknown_nonfinite_oversize_and_content_type_refused(self):
        headers,_=self.session();settings.public_assistant_contacts_enabled=True
        for raw in ('{"locale":"en","locale":"es","draft":{}}','{"locale":"en","draft":{"name":1e999}}',
                    '{"locale":"en","draft":{"actor":"admin"}}','{"locale":"en","draft":{"name":"\\ud800"}}'):
            reply=self.client.post("/public/assistant/contact-review",headers={**headers,"Content-Type":"application/json"},content=raw)
            self.assertEqual(reply.status_code,422);self.assertEqual(set(reply.json()),{"error"})
        self.assertEqual(self.client.post("/public/assistant/contact-review",headers={**headers,"Content-Type":"application/json"},content=b'x'*8193).status_code,413)
        self.assertEqual(self.client.post("/public/assistant/contact-review",headers=headers,content="text").status_code,415)
        self.assertEqual(self.client.post("/public/assistant/contact-review?actor=admin",headers=headers,json={"locale":"en","draft":self.draft()}).status_code,422)
        self.assertEqual(self.counts(),(0,0))

    def test_review_normalizes_en_es_and_never_writes_or_enters_model(self):
        settings.public_assistant_contacts_enabled=True;headers,_=self.session()
        with patch.object(orchestrator,"run_public_turn",side_effect=AssertionError("contact_data_entered_model")) as model:
            for locale in ("en","es"):
                reply=self.client.post("/public/assistant/contact-review",headers=headers,json={"locale":locale,"draft":self.draft()})
                self.assertEqual(reply.status_code,200);review=reply.json()["review"]
                self.assertEqual(review["name"],"Owned customer");self.assertEqual(review["locale"],locale)
                self.assertEqual(review["consent_text"],commands._CONSENT[locale]);self.assertTrue(review["contact_consent"])
            model.assert_not_called()
        self.assertEqual(self.counts(),(0,0))

    def test_only_explicit_true_and_unchanged_signed_review_can_write(self):
        headers, prepared=self.review()
        for confirmed in (False,1,"true",None):
            reply=self.client.post("/public/assistant/contact-confirm",headers=headers,json={"review":prepared["review"],"confirmation_token":prepared["confirmation_token"],"customer_confirmed":confirmed})
            self.assertEqual(reply.status_code,422)
        changed={**prepared,"review":{**prepared["review"],"email":"other@example.com"}}
        self.assertEqual(self.confirm(headers,changed).status_code,409)
        self.assertEqual(self.counts(),(0,0))
        reply=self.confirm(headers,prepared);self.assertEqual(reply.status_code,200)
        self.assertEqual(set(reply.json()),{"reference","recorded","replayed","delivery_confirmed"})
        self.assertTrue(reply.json()["recorded"]);self.assertFalse(reply.json()["delivery_confirmed"])

    def test_exact_replay_from_fresh_http_request_preserves_one_lead_and_jobs(self):
        headers, prepared=self.review();first=self.confirm(headers,prepared);before=self.counts()
        second=self.confirm(headers,prepared)
        self.assertEqual(first.status_code,200);self.assertEqual(second.status_code,200)
        self.assertTrue(second.json()["replayed"]);self.assertEqual(first.json()["reference"],second.json()["reference"])
        self.assertEqual(self.counts(),before);self.assertEqual(before[0],1);self.assertGreater(before[1],0)

    def test_withdrawn_property_blocks_new_command_and_does_not_backfill(self):
        headers, prepared=self.review(intent="property_inquiry",property_id=self.listing_id)
        with self.factory() as db:
            db.get(Listing,UUID(self.listing_id)).status="archived";db.commit()
        reply=self.confirm(headers,prepared)
        self.assertEqual(reply.status_code,404);self.assertEqual(reply.json(),{"error":"property_unavailable"})
        self.assertEqual(self.counts(),(0,0))

    def test_lost_postcommit_receipt_is_unknown_then_same_review_replays(self):
        headers, prepared=self.review();actual=leads._persist_submission
        def lost(*args,**kwargs):
            actual(*args,**kwargs);raise RuntimeError("PRIVATE synthetic lost receipt")
        with patch.object(commands,"_backend",return_value=(leads.LeadCreate,lost,self.factory)):
            reply=self.confirm(headers,prepared)
        self.assertEqual(reply.status_code,503);self.assertEqual(reply.json(),{"error":"submission_unknown"})
        before=self.counts();self.assertEqual(before[0],1)
        replay=self.confirm(headers,prepared);self.assertEqual(replay.status_code,200);self.assertTrue(replay.json()["replayed"])
        self.assertEqual(self.counts(),before)

    def test_expiry_does_not_reprepare_retry_or_claim_prior_write_absent(self):
        headers, prepared=self.review()
        with patch.object(commands,"_now",return_value=prepared["expires_at"]+1):
            with patch.object(commands,"_backend",side_effect=AssertionError("expired_backend")) as backend:
                reply=self.confirm(headers,prepared);backend.assert_not_called()
        self.assertEqual(reply.status_code,409);self.assertEqual(reply.json(),{"error":"review_expired"})
        self.assertEqual(self.counts(),(0,0))

    def test_contact_error_after_actual_commit_projection_is_conservatively_unknown(self):
        headers, prepared=self.review()
        from schemas.public_assistant_http import AssistantConfirmationResponse
        with patch.object(AssistantConfirmationResponse,"model_validate",side_effect=RuntimeError("PRIVATE projection error")):
            reply=self.confirm(headers,prepared)
        self.assertEqual(reply.status_code,503);self.assertEqual(reply.json(),{"error":"submission_unknown"})
        self.assertEqual(self.counts()[0],1)

    def test_actual_rate_limit_six_preparations_then429_without_writes(self):
        settings.public_assistant_contacts_enabled=True;headers,_=self.session()
        replies=[self.client.post("/public/assistant/contact-review",headers=headers,json={"locale":"en","draft":self.draft()}) for _ in range(7)]
        self.assertEqual([r.status_code for r in replies],[200]*6+[429])
        self.assertEqual(replies[-1].json(),{"error":"assistant_busy"})
        self.assertEqual(replies[-1].headers["cache-control"],"no-store");self.assertEqual(self.counts(),(0,0))

    def test_actual_worker_slot_remains_owned_until_worker_finishes(self):
        entered,release=threading.Event(),threading.Event();slots=threading.BoundedSemaphore(1)
        def held():
            entered.set();self.assertTrue(release.wait(2));return "done"
        with patch.object(boundary,"_MODEL_SLOTS",slots),ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(boundary.call_in_worker,"model",held)
            self.assertTrue(entered.wait(2))
            with self.assertRaises(boundary.PublicHTTPError) as busy:boundary.call_in_worker("model",lambda:None)
            self.assertEqual(busy.exception.status,429);release.set();self.assertEqual(pending.result(timeout=2),"done")
            self.assertEqual(boundary.call_in_worker("model",lambda:"next"),"next")

    def test_model_route_runs_in_worker_and_never_exposes_transport_metadata(self):
        settings.public_assistant_enabled=settings.public_assistant_no_new_spend_verified=True
        headers,_=self.session();threads=[]
        def complete(raw,*,binding):
            with self.assertRaises(RuntimeError):asyncio.get_running_loop()
            threads.append(threading.current_thread().name)
            return orchestrator.PublicTurn("es","Puedo ayudarle con su búsqueda.",(),True,1,0)
        with patch.object(orchestrator,"run_public_turn",side_effect=complete):
            denied=self.client.post("/public/assistant/turn",headers=headers,json={"locale":"es","message":"Ayúdame","actor_id":"admin"})
            self.assertEqual(denied.status_code,422)
            reply=self.client.post("/public/assistant/turn",headers=headers,json={"locale":"es","message":"Ayúdame"})
        self.assertEqual(reply.status_code,200);self.assertEqual(reply.json()["locale"],"es")
        self.assertFalse(reply.json()["availability_verified"]);self.assertNotIn("model_steps",reply.json());self.assertEqual(len(threads),1)

    def test_actual_transport_public_reader_and_http_projection_with_private_canaries(self):
        settings.public_assistant_enabled=settings.public_assistant_no_new_spend_verified=True
        headers,_=self.session();actual=gateway.complete;calls=[]
        def model(request):
            payload=json.loads(request.content);calls.append(payload)
            if len(calls)==1:
                message={"role":"assistant","content":None,"tool_calls":[{"id":"owned_read1","type":"function","function":{"name":"read_public_listing","arguments":json.dumps({"locale":"en","listing_id":self.listing_id})}}]}
            else:message={"role":"assistant","content":json.dumps({"answer":"This public villa is listed at120000USD.","listing_ids":[self.listing_id]})}
            return httpx.Response(200,json={"object":"chat.completion","model":"openai/owned-test","choices":[{"index":0,"message":message,"finish_reason":"tool_calls" if len(calls)==1 else "stop"}]})
        async def bounded(binding,messages,tools,**kwargs):
            return await actual(binding,messages,tools,transport=httpx.MockTransport(model),**kwargs)
        with patch.object(gateway,"complete",side_effect=bounded):
            reply=self.client.post("/public/assistant/turn",headers=headers,json={"locale":"en","message":"Tell me about the public villa"})
        self.assertEqual(reply.status_code,200);value=reply.json();self.assertEqual(len(calls),2)
        self.assertEqual(value["citations"][0]["listing_id"],self.listing_id)
        self.assertEqual(value["citations"][0]["url"],"https://ilovedrrealty.com/listing/"+self.listing_id+"/")
        self.assertNotIn("synthetic-http-owner@example.com",json.dumps(calls));self.assertNotIn("signing",reply.text)
        self.assertNotIn("PRIVATE-owned-payment-canary",json.dumps(calls));self.assertNotIn("PRIVATE-owned-payment-canary",reply.text)
        self.assertEqual(self.counts(),(0,0))

    def test_actual_cors_allows_only_configured_origin_and_csrf_header(self):
        headers={"Origin":"http://testserver","Access-Control-Request-Method":"POST","Access-Control-Request-Headers":"content-type,x-assistant-csrf"}
        allowed=self.client.options("/public/assistant/turn",headers=headers)
        self.assertEqual(allowed.status_code,200);self.assertEqual(allowed.headers["access-control-allow-origin"],"http://testserver")
        denied=self.client.options("/public/assistant/turn",headers={**headers,"Origin":"https://preview.example"})
        self.assertEqual(denied.status_code,400);self.assertNotIn("access-control-allow-origin",denied.headers)
