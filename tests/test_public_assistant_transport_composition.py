"""Actual400 loop plus actual43f transport, using only owned HTTPX responses.

Typed reader doubles and real anonymous help are used; no DB, app route, live
model, credential lookup or entitlement proof. All three definitions are UNRUN
until the isolated hosted lane. Existing12/25/14 methods are not imported here.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from schemas.public_assistant_listing import (
    AssistantListingDetail, AssistantListingFact, AssistantListingPage,
    ListingCitation, ListingPriceFact,
)
from services import assistant_gateway_transport as gateway
from services import public_assistant_orchestrator as loop


ID = "00000000-0000-4000-8000-000000000001"
MODEL = "owned/model"


def listing(locale="en", amount="250000", comparable=None):
    return AssistantListingFact(listing_id=ID, locale=locale, title="Owned public villa",
        title_truncated=False, property_type="villa", transaction="sale", location="Sosúa",
        location_truncated=False, location_precision="area", location_verified=False,
        bedrooms=3, bathrooms=2,
        sale_price=ListingPriceFact(amount=Decimal(amount), currency="USD", period="sale"),
        comparable_price=comparable,
        citation=ListingCitation(url="https://ilovedrrealty.com/listing/" + ID + "/",
                                 label="Public listing" if locale == "en" else "Ficha pública"))


def tool(name, arguments, call_id):
    return {"object": "chat.completion", "model": MODEL, "choices": [{"index": 0,
        "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": name, "arguments": arguments}}]}}]}


def final(locale="en", ids=()):
    return {"object": "chat.completion", "model": MODEL, "choices": [{"index": 0,
        "finish_reason": "stop", "message": {"role": "assistant",
            "content": json.dumps({"answer": "Public facts." if locale == "en" else "Datos públicos.",
                                   "listing_ids": list(ids)})}}]}


class PublicAssistantTransportCompositionTests(unittest.TestCase):
    def setUp(self):
        self.binding = gateway.Binding(MODEL, "owned-synthetic-gateway-token", enabled=True,
            no_new_spend_verified=True, verified_models=frozenset({MODEL}))
        self.actual_complete = gateway.complete
        self.current = listing()
        self.detail_arguments = []
        self.search_arguments = []
        self.requests = []

        def read(arguments):
            self.detail_arguments.append(arguments)
            return AssistantListingDetail(locale=self.current.locale, item=self.current,
                                          projected_at=datetime.now(timezone.utc))

        self.read = read
        self.reader = SimpleNamespace(read_public_listing=read, search_public_listings=None)

    @contextmanager
    def wire(self, sequence, *, status=200):
        self.requests = []
        pending = list(sequence)

        def handler(request):
            self.assertEqual(str(request.url), gateway.ENDPOINT)
            self.assertEqual(request.method, "POST")
            body = json.loads(request.content)
            self.assertEqual(body["model"], MODEL)
            self.assertIs(body["parallel_tool_calls"], False)
            self.assertEqual({item["function"]["name"] for item in body["tools"]},
                {"feature_help", "search_public_listings", "read_public_listing"})
            self.requests.append(body)
            self.assertTrue(pending)
            return httpx.Response(status, json=pending.pop(0))

        transport = httpx.MockTransport(handler)

        async def complete(binding, messages, tools, *, prior_call_ids):
            return await self.actual_complete(binding, messages, tools,
                prior_call_ids=prior_call_ids, transport=transport)

        with patch.object(gateway, "complete", complete), patch.object(loop, "_readers", return_value=self.reader):
            yield

    def turn(self, locale="en", *, binding=None):
        return loop.run_public_turn(json.dumps({"locale": locale, "message": "Owned public question"}),
                                    binding=self.binding if binding is None else binding)

    def test_en_actual_transport_search_detail_final_preserves_raw_arguments_and_fresh_citations(self):
        search = '{ "locale":"en", "price_currency":"DOP", "rental_period":"day", "page":2, "locations":["Sosúa"] }'
        detail = '{ "locale" : "en", "listing_id" : "' + ID + '" }'
        comparable = ListingPriceFact(amount=Decimal("14875000"), currency="DOP", period="sale")

        def search_reader(arguments):
            self.search_arguments.append(arguments)
            return AssistantListingPage(locale="en", items=[listing(comparable=comparable)], total=20,
                page=2, page_size=12, search_currency="DOP", rental_period="day", estimated_usd_to_dop=59.5,
                fx_kind="route_default_estimate", projected_at=datetime.now(timezone.utc))

        self.reader.search_public_listings = search_reader
        with self.wire([tool("search_public_listings", search, "owned_search"),
                        tool("read_public_listing", detail, "owned_detail"), final(ids=[ID])]):
            result = self.turn()
        self.assertEqual((result.model_steps, result.tool_calls), (3, 2))
        self.assertEqual(self.search_arguments, [search])
        self.assertEqual(self.detail_arguments[0], detail)
        self.assertEqual(json.loads(self.detail_arguments[1]), {"locale": "en", "listing_id": ID})
        self.assertEqual(len(self.detail_arguments), 2)
        self.assertEqual(self.requests[1]["messages"][-2]["tool_calls"][0]["function"]["arguments"], search)
        self.assertEqual(self.requests[2]["messages"][-2]["tool_calls"][0]["function"]["arguments"], detail)
        returned = json.loads(self.requests[1]["messages"][-1]["content"])
        self.assertEqual((returned["search_currency"], returned["rental_period"], returned["page"]), ("DOP", "day", 2))
        self.assertEqual((result.citations[0].listing_id, result.citations[0].url),
                         (ID, "https://ilovedrrealty.com/listing/" + ID + "/"))
        self.assertFalse(result.availability_verified)

    def test_es_actual_transport_anonymous_help_binds_locale_and_refuses_model_actor(self):
        with self.wire([tool("feature_help", '{ "locale":"es", "topic":"signup" }', "owned_help"), final("es")]):
            result = self.turn("es")
        payload = json.loads(self.requests[1]["messages"][-1]["content"])
        self.assertEqual((result.locale, payload["guide"]["topic"], payload["locale"]), ("es", "signup", "es"))
        self.assertEqual(payload["guide_state"], "unpublished_source")
        self.assertEqual(result.citations, ())
        with self.wire([tool("read_public_listing", json.dumps({"locale": "en", "listing_id": ID}), "wrong_locale")]):
            with self.assertRaisesRegex(loop.PublicAssistantUnavailable, "^tool_locale_mismatch$"):
                self.turn("es")
        with self.wire([tool("feature_help", '{"locale":"es","role":"admin"}', "wrong_actor")]):
            with self.assertRaisesRegex(loop.PublicAssistantUnavailable, "^public_model_unavailable$"):
                self.turn("es")
        self.assertEqual(self.detail_arguments, [])

    def test_actual_transport_withdrawal_changed_facts_errors_and_disabled_turn_never_return_cached_answer(self):
        arguments = json.dumps({"locale": "en", "listing_id": ID})

        def withdrawn(raw):
            if self.detail_arguments:
                raise RuntimeError("PRIVATE_SYNTHETIC_READER_ERROR")
            return self.read(raw)

        self.reader.read_public_listing = withdrawn
        with self.wire([tool("read_public_listing", arguments, "withdrawal"), final(ids=[ID])]):
            with self.assertRaisesRegex(loop.PublicAssistantUnavailable, "^public_facts_unavailable$") as failure:
                self.turn()
        self.assertNotIn("PRIVATE", str(failure.exception))
        self.detail_arguments = []

        def changed(raw):
            if self.detail_arguments:
                self.current = listing(amount="260000")
            return self.read(raw)

        self.reader.read_public_listing = changed
        with self.wire([tool("read_public_listing", arguments, "changed"), final(ids=[ID])]):
            with self.assertRaisesRegex(loop.PublicAssistantUnavailable, "^public_facts_changed$"):
                self.turn()
        self.detail_arguments = []
        with self.wire([{"error": "PRIVATE_SYNTHETIC_PROVIDER_ERROR"}], status=503):
            with self.assertRaisesRegex(loop.PublicAssistantUnavailable, "^public_model_unavailable$") as failure:
                self.turn()
        self.assertNotIn("PRIVATE", str(failure.exception))
        self.assertEqual(self.detail_arguments, [])
        with self.wire([]):
            with self.assertRaisesRegex(loop.PublicAssistantUnavailable, "^public_model_disabled$"):
                self.turn(binding=gateway.Binding())
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
