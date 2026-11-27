"""Owned state/authority unit definitions; no provider, DB or application route.

The gateway is a controlled test double, not evidence of actual model invocation.
Existing listing-read PG definitions remain responsible for real query policy.
The real transport pair and this loop still need composed hosted verification.
"""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from schemas.public_assistant_listing import (
    AssistantListingDetail, AssistantListingFact, AssistantListingPage,
    ListingCitation, ListingPriceFact,
)
from services import public_assistant_orchestrator as loop


LISTING = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"


@dataclass(frozen=True)
class Binding:
    enabled: bool = False
    no_new_spend_verified: bool = False


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters_json: str


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class Step:
    kind: str
    text: str | None = None
    call: ToolCall | None = None


class Gateway:
    Binding, Tool, ToolCall, Step = Binding, Tool, ToolCall, Step
    sequence = []
    requests = []

    @classmethod
    async def complete(cls, binding, messages, tools, *, prior_call_ids):
        cls.requests.append((deepcopy(messages), tools, prior_call_ids))
        value = cls.sequence.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def raw(value):
    return json.dumps(value, ensure_ascii=False)


def tool(name, args, call_id="call_1"):
    return Step("tool", call=ToolCall(call_id, name, args if isinstance(args, str) else raw(args)))


def answer(ids=None, text="Public property facts."):
    return Step("text", text=raw({"answer": text, "listing_ids": ids or []}))


def fact(locale="en", *, amount="250000", comparable=None):
    return AssistantListingFact(
        listing_id=LISTING, locale=locale, title="Public villa", title_truncated=False,
        property_type="villa", transaction="sale", location="Public area",
        location_truncated=False, location_precision="area", location_verified=False,
        bedrooms=3, bathrooms=2, sale_price=ListingPriceFact(amount=Decimal(amount), currency="USD", period="sale"),
        comparable_price=comparable,
        citation=ListingCitation(url="https://ilovedrrealty.com/listing/" + LISTING + "/",
                                 label="Public listing" if locale == "en" else "Ficha pública"),
    )


def detail(item):
    return AssistantListingDetail(locale=item.locale, item=item, projected_at=datetime.now(timezone.utc))


class PublicAssistantOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.gateway = patch.object(loop, "_gateway", return_value=Gateway)
        self.gateway.start()
        self.addCleanup(self.gateway.stop)
        Gateway.sequence, Gateway.requests = [], []
        self.reads = []
        self.current = fact()

        def read(arguments):
            self.reads.append(arguments)
            return detail(self.current)

        self.reader = SimpleNamespace(read_public_listing=read, search_public_listings=None)
        self.readers = patch.object(loop, "_readers", return_value=self.reader)
        self.readers.start()
        self.addCleanup(self.readers.stop)

    def run_turn(self, locale="en", *, request=None, binding=None):
        return loop.run_public_turn(request or raw({"locale": locale, "message": "Find a public villa"}),
                                    binding=binding or Binding(True, True))

    def refused(self, code, **kwargs):
        with self.assertRaisesRegex(loop.PublicAssistantUnavailable, "^" + code + "$"):
            self.run_turn(**kwargs)

    def test_disabled_and_unverified_entitlement_do_not_invoke_model_or_tools(self):
        for binding in (Binding(), Binding(True, False), SimpleNamespace(enabled=True, no_new_spend_verified=True)):
            with self.subTest(binding=binding):
                self.refused("public_model_disabled", binding=binding)
        self.assertEqual((Gateway.requests, self.reads), ([], []))

    def test_strict_raw_input_rejects_duplicate_actor_unknown_and_oversize_before_gateway(self):
        for request in ('{"locale":"en","locale":"es","message":"x"}',
                        raw({"locale": "en", "message": "x", "actor": "admin"}),
                        raw({"locale": "en", "message": "x" * 1501}),
                        '{"locale":"en","message":NaN}',
                        '{"locale":"en","message":"\\ud800"}'):
            self.refused("invalid_public_input", request=request)
        self.assertEqual(Gateway.requests, [])

    def test_async_caller_is_refused_without_blocking_event_loop(self):
        async def invoke():
            self.refused("worker_thread_required")
        asyncio.run(invoke())
        self.assertEqual(Gateway.requests, [])

    def test_real_anonymous_help_is_a_tool_after_model_call_and_never_actor_authority(self):
        Gateway.sequence = [tool("feature_help", {"locale": "en", "topic": "signup"}), answer(text="Use signup.")]
        result = self.run_turn()
        self.assertTrue(result.model_invoked)
        messages = Gateway.requests[1][0]
        payload = json.loads(messages[-1]["content"])
        self.assertEqual(payload["guide_state"], "unpublished_source")
        self.assertEqual(payload["guide"]["topic"], "signup")
        Gateway.sequence = [tool("feature_help", {"locale": "en", "topic": "signup", "role": "admin"})]
        self.refused("public_tool_unavailable")
        self.assertEqual(self.reads, [])

    def test_raw_search_then_detail_and_final_freshness_preserve_existing_context(self):
        comparable = ListingPriceFact(amount=Decimal("14875000"), currency="DOP", period="sale")
        item = fact(comparable=comparable)
        search_args = raw({"locale": "en", "price_currency": "DOP", "rental_period": "day", "page": 2})
        searched = []

        def search(arguments):
            searched.append(arguments)
            return AssistantListingPage(locale="en", items=[item], total=20, page=2, page_size=12,
                                        search_currency="DOP", rental_period="day", estimated_usd_to_dop=59.5,
                                        fx_kind="route_default_estimate", projected_at=datetime.now(timezone.utc))

        self.reader.search_public_listings = search
        Gateway.sequence = [tool("search_public_listings", search_args),
                            tool("read_public_listing", {"locale": "en", "listing_id": LISTING}, "call_2"),
                            answer([LISTING])]
        result = self.run_turn()
        self.assertEqual(searched, [search_args])
        self.assertEqual(len(self.reads), 2)  # explicit tool and fresh final cited read
        self.assertEqual((result.model_steps, result.tool_calls), (3, 2))
        self.assertFalse(result.availability_verified)
        self.assertEqual(result.citations[0].url, item.citation.url)
        self.assertEqual(Gateway.requests[2][2], frozenset({"call_1", "call_2"}))

    def test_spanish_locale_is_bound_for_every_tool_and_final_citation(self):
        self.current = fact("es")
        Gateway.sequence = [tool("read_public_listing", {"locale": "es", "listing_id": LISTING}), answer([LISTING], "Datos públicos.")]
        self.assertEqual(self.run_turn("es").citations[0].label, "Ficha pública")
        Gateway.sequence = [tool("read_public_listing", {"locale": "en", "listing_id": LISTING})]
        self.refused("tool_locale_mismatch", locale="es")
        self.assertEqual(len(self.reads), 2)

    def test_duplicate_unknown_nonfinite_and_url_tool_inputs_never_reach_reader(self):
        for arguments in ('{"locale":"en","listing_id":"' + LISTING + '","listing_id":"' + OTHER + '"}',
                          raw({"locale": "en", "listing_id": "https://example.com/private"}),
                          raw({"locale": "en", "listing_id": LISTING, "actor": "admin"}),
                          '{"locale":"en","listing_id":NaN}'):
            Gateway.sequence = [tool("read_public_listing", arguments)]
            with self.assertRaises(loop.PublicAssistantUnavailable):
                self.run_turn()
        self.assertEqual(self.reads, [])

    def test_unknown_write_tools_duplicate_ids_and_parallel_calls_fail_closed(self):
        Gateway.sequence = [tool("create_public_lead", {"locale": "en"})]
        self.refused("invalid_model_step")
        Gateway.sequence = [Step("tool", call=[ToolCall("call_1", "feature_help", '{"locale":"en"}')])]
        self.refused("invalid_model_step")
        Gateway.sequence = [tool("feature_help", {"locale": "en"}), tool("feature_help", {"locale": "en"})]
        self.refused("invalid_model_step")
        self.assertEqual(self.reads, [])

    def test_three_tools_allow_fourth_final_step_but_no_fourth_tool(self):
        Gateway.sequence = [tool("feature_help", {"locale": "en"}, f"call_{i}") for i in range(3)] + [answer()]
        result = self.run_turn()
        self.assertEqual((result.model_steps, result.tool_calls), (4, 3))
        Gateway.requests = []
        Gateway.sequence = [tool("feature_help", {"locale": "en"}, f"call_{i}") for i in range(4)]
        self.refused("public_tool_limit")
        self.assertEqual(len(Gateway.requests), 4)
        Gateway.requests = []
        Gateway.sequence = [answer()]
        with patch.object(loop, "MAX_MESSAGES_BYTES", 1):
            self.refused("public_context_limit")
        self.assertEqual(Gateway.requests, [])

    def test_final_foreign_citation_urls_omitted_sources_and_oversize_are_refused(self):
        for response in (answer([OTHER]), answer([LISTING], "See https://example.com"),
                         answer(), answer([LISTING], "x" * 4001)):
            Gateway.sequence = [tool("read_public_listing", {"locale": "en", "listing_id": LISTING}), response]
            self.refused("invalid_public_answer")
        self.assertEqual(len(self.reads), 4)  # none reached final grounding

    def test_withdrawn_final_read_never_returns_cached_answer_or_source_error(self):
        first = self.reader.read_public_listing

        def withdraw(arguments):
            if self.reads:
                raise RuntimeError("PRIVATE_PROVIDER_AND_CUSTOMER_CANARY")
            return first(arguments)

        self.reader.read_public_listing = withdraw
        Gateway.sequence = [tool("read_public_listing", {"locale": "en", "listing_id": LISTING}), answer([LISTING])]
        self.refused("public_facts_unavailable")

    def test_changed_final_prices_or_conflicting_tool_facts_refuse_stale_answer(self):
        first = self.reader.read_public_listing

        def change(arguments):
            if self.reads:
                self.current = fact(amount="260000")
            return first(arguments)

        self.reader.read_public_listing = change
        Gateway.sequence = [tool("read_public_listing", {"locale": "en", "listing_id": LISTING}), answer([LISTING])]
        self.refused("public_facts_changed")
        self.reads = []
        self.current = fact()
        Gateway.sequence = [tool("read_public_listing", {"locale": "en", "listing_id": LISTING}),
                            tool("read_public_listing", {"locale": "en", "listing_id": LISTING}, "call_2")]
        self.refused("public_facts_changed")

    def test_full_dto_or_raw_private_record_cannot_enter_model_tool_messages(self):
        self.reader.read_public_listing = lambda _: {"owner_paypal": "PRIVATE_CANARY", "item": fact()}
        Gateway.sequence = [tool("read_public_listing", {"locale": "en", "listing_id": LISTING})]
        self.refused("public_tool_output_unavailable")
        self.assertEqual(len(Gateway.requests), 1)
        self.assertNotIn("PRIVATE_CANARY", repr(Gateway.requests))
        invalid_citation = fact().model_copy(update={"citation": ListingCitation(url="https://example.com/private", label="Public listing")})
        self.reader.read_public_listing = lambda _: detail(invalid_citation)
        Gateway.sequence = [tool("read_public_listing", {"locale": "en", "listing_id": LISTING})]
        self.refused("public_tool_output_unavailable")
        self.assertEqual(len(Gateway.requests), 2)
        self.reader.read_public_listing = lambda _: detail(fact())
        Gateway.sequence = [tool("read_public_listing", {"locale": "en", "listing_id": LISTING})]
        with patch.object(loop, "MAX_TOOL_BYTES", 1):
            self.refused("public_tool_output_limit")
        self.assertEqual(len(Gateway.requests), 3)

    def test_transport_failure_has_no_faq_fallback_or_private_exception_projection(self):
        Gateway.sequence = [RuntimeError("SECRET_CREDENTIAL_PRIVATE_PROMPT")]
        self.refused("public_model_unavailable")
        self.assertEqual(self.reads, [])


if __name__ == "__main__":
    unittest.main()
