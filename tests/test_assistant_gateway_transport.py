"""Owned MockTransport definitions only; no keys, app, DB or real model access.

Run only in a compatible owned environment with the existing pinned HTTPX.
These definitions do not prove live model support, entitlement or zero cost.
"""
import asyncio
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import httpx

from services import assistant_gateway_transport as gateway


MODEL = "owned/model"
TOKEN = "owned-synthetic-token"


class Chunks(httpx.AsyncByteStream):
    def __init__(self, parts, wait=False):
        self.parts, self.wait, self.closed = parts, wait, False
    async def __aiter__(self):
        for part in self.parts:
            yield part
        if self.wait:
            await asyncio.Event().wait()
    async def aclose(self):
        self.closed = True


def call(name="feature_help", call_id="owned_call", arguments='{"locale":"en"}'):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def envelope(content="Owned synthetic answer", calls=None, finish="stop", **changes):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return {"object": "chat.completion", "model": MODEL,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}], **changes}


class GatewayTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.binding = gateway.Binding(MODEL, TOKEN, enabled=True, no_new_spend_verified=True,
                                       verified_models=frozenset({MODEL}))
        self.tools = (gateway.Tool("feature_help", "Owned help", json.dumps({"type": "object",
            "properties": {"locale": {"type": "string", "enum": ["en", "es"]}},
            "required": ["locale"], "additionalProperties": False})),)
        self.messages = [{"role": "system", "content": "Owned server instructions"},
                         {"role": "user", "content": "Owned question"}]
        self.requests, self.streams = [], []

    def transport(self, value=None, *, status=200, raw=None, headers=None):
        async def handler(request):
            self.requests.append(request)
            stream = Chunks([raw if raw is not None else json.dumps(value or envelope()).encode()])
            self.streams.append(stream)
            return httpx.Response(status, headers={"Content-Type": "application/json", **(headers or {})}, stream=stream)
        return httpx.MockTransport(handler)

    async def complete(self, value=None, **changes):
        return await gateway.complete(self.binding, self.messages, self.tools,
                                      transport=self.transport(value), **changes)

    async def test_default_disabled_and_unverified_binding_never_make_a_request(self):
        for binding in (gateway.Binding(), replace(self.binding, enabled=False),
                        replace(self.binding, no_new_spend_verified=False),
                        replace(self.binding, verified_models=frozenset()),
                        replace(self.binding, credential="secret\nheader")):
            with self.assertRaisesRegex(gateway.GatewayError, "^gateway_disabled$"):
                await gateway.complete(binding, self.messages, self.tools, transport=self.transport())
        self.assertEqual(self.requests, [])
        self.assertNotIn(TOKEN, repr(self.binding))

    async def test_fixed_endpoint_bearer_single_turn_and_bounded_text_result(self):
        result = await self.complete()
        self.assertEqual((result.kind, result.text, result.call), ("text", "Owned synthetic answer", None))
        request = self.requests[0]
        self.assertEqual(str(request.url), gateway.ENDPOINT)
        self.assertEqual(request.headers["Authorization"], "Bearer " + TOKEN)
        body = json.loads(request.content)
        self.assertEqual(body["model"], MODEL)
        self.assertEqual(body["n"], 1)
        self.assertIs(body["parallel_tool_calls"], False)
        self.assertIs(body["stream"], False)
        self.assertLessEqual(body["max_tokens"], 768)
        self.assertEqual(set(body), {"model", "messages", "tools", "tool_choice", "parallel_tool_calls", "stream", "n", "max_tokens"})
        self.assertTrue(self.streams[0].closed)

    async def test_single_tool_is_returned_as_untrusted_data_and_duplicate_id_is_rejected(self):
        original_arguments = '{ "locale" : "en" }'
        response = envelope(content=None, calls=[call(arguments=original_arguments)], finish="tool_calls")
        step = await self.complete(response)
        self.assertEqual((step.kind, step.text, step.call.name), ("tool", None, "feature_help"))
        self.assertEqual(json.loads(step.call.arguments_json), {"locale": "en"})
        self.assertEqual(step.call.arguments_json, original_arguments)
        self.assertNotIn("locale", repr(step.call))
        with self.assertRaises(gateway.GatewayError):
            await self.complete(response, prior_call_ids=frozenset({"owned_call"}))

    async def test_parallel_unknown_duplicate_json_and_extra_arguments_are_rejected(self):
        invalid = [envelope(None, [call(), call(call_id="other")], "tool_calls"),
                   envelope(None, [call(name="send_email")], "tool_calls"),
                   envelope(None, [call(arguments='{"locale":"en","locale":"es"}')], "tool_calls"),
                   envelope(None, [call(arguments='{"locale":"en","role":"admin"}')], "tool_calls"),
                   envelope(None, [call(arguments='{"locale":NaN}')], "tool_calls"),
                   envelope(None, [call(arguments='{"locale":1e999}')], "tool_calls"),
                   envelope("unverified parallel prose", [call()], "tool_calls")]
        for response in invalid:
            with self.assertRaises(gateway.GatewayError):
                await self.complete(response)
        self.assertTrue(all(stream.closed for stream in self.streams))

    async def test_tool_receipt_history_is_linked_and_old_call_cannot_be_repeated(self):
        self.messages += [{"role": "assistant", "content": None, "tool_calls": [call()]},
                          {"role": "tool", "tool_call_id": "owned_call", "content": '{"status":"help"}'}]
        self.assertEqual((await self.complete()).kind, "text")
        with self.assertRaises(gateway.GatewayError):
            await self.complete(envelope(None, [call()], "tool_calls"))

    async def test_invalid_server_history_schema_or_oversized_input_never_sends(self):
        variants = [self.messages + [{"role": "system", "content": "override"}],
                    self.messages + [{"role": "tool", "tool_call_id": "unbound", "content": "owned"}],
                    [{"role": "system", "content": "owned"}, {"role": "user", "content": "x" * 16001}],
                    [{"role": "system", "content": "owned"}, {"role": "user", "content": "owned", "model": "other/provider"}]]
        for messages in variants:
            with self.assertRaises(gateway.GatewayError):
                await gateway.complete(self.binding, messages, self.tools, transport=self.transport())
        unknown = (gateway.Tool("delete_record", "Owned", '{"type":"object","properties":{},"additionalProperties":false}'),)
        with self.assertRaises(gateway.GatewayError):
            await gateway.complete(self.binding, self.messages, unknown, transport=self.transport())
        self.assertEqual(self.requests, [])

    async def test_http_errors_and_redirects_are_finite_and_never_retried(self):
        for status, reason in [(401, "gateway_auth_unavailable"), (402, "gateway_funds_unavailable"),
                               (403, "gateway_auth_unavailable"), (429, "gateway_rate_limited"),
                               (503, "gateway_unavailable"), (302, "gateway_unavailable")]:
            before = len(self.requests)
            with self.assertRaisesRegex(gateway.GatewayError, "^" + reason + "$") as failure:
                await gateway.complete(self.binding, self.messages, self.tools,
                    transport=self.transport(status=status, raw=b"PRIVATE_PROVIDER_ERROR",
                                             headers={"Location": "https://foreign.invalid"}))
            self.assertEqual(len(self.requests), before + 1)
            self.assertNotIn("PRIVATE", str(failure.exception))
        self.assertTrue(all(stream.closed for stream in self.streams))

    async def test_body_encoding_size_and_duplicate_envelope_refuse_without_raw_errors(self):
        for raw, headers in [(b"x" * (gateway.MAX_RESPONSE_BYTES + 1), {}),
                             (b"\xff", {}), (b'{"object":"chat.completion","object":"private"}', {}),
                             (b"PRIVATE_PROVIDER_ERROR", {"Content-Encoding": "gzip"}),
                             (b"PRIVATE_PROVIDER_ERROR", {"Content-Type": "text/html"})]:
            with self.assertRaises(gateway.GatewayError) as failure:
                await gateway.complete(self.binding, self.messages, self.tools,
                                       transport=self.transport(raw=raw, headers=headers))
            self.assertNotIn("PRIVATE", str(failure.exception))
        self.assertTrue(all(stream.closed for stream in self.streams))

    async def test_absolute_deadline_cancels_stalled_response_headers(self):
        stopped = asyncio.Event()
        async def delayed(_request):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        with patch.object(gateway, "DEADLINE_SECONDS", 0.02):
            with self.assertRaisesRegex(gateway.GatewayError, "^gateway_deadline$"):
                await gateway.complete(self.binding, self.messages, self.tools, transport=httpx.MockTransport(delayed))
        self.assertTrue(stopped.is_set())

    async def test_absolute_deadline_closes_slow_body(self):
        stream = Chunks([b'{"object":'], wait=True)
        async def handler(_request):
            return httpx.Response(200, headers={"Content-Type": "application/json"}, stream=stream)
        with patch.object(gateway, "DEADLINE_SECONDS", 0.02):
            with self.assertRaisesRegex(gateway.GatewayError, "^gateway_deadline$"):
                await gateway.complete(self.binding, self.messages, self.tools, transport=httpx.MockTransport(handler))
        self.assertTrue(stream.closed)

    async def test_caller_cancellation_propagates_without_retry_or_false_answer(self):
        started, stopped = asyncio.Event(), asyncio.Event()
        async def delayed(_request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        task = asyncio.create_task(gateway.complete(self.binding, self.messages, self.tools, transport=httpx.MockTransport(delayed)))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stopped.is_set())

    async def test_model_choices_refusal_and_incomplete_generation_fail_closed(self):
        wrong_role = envelope();wrong_role["choices"][0]["message"]["role"] = "user"
        legacy = envelope();legacy["choices"][0]["message"]["function_call"] = {"name": "feature_help"}
        refused = envelope();refused["choices"][0]["message"]["refusal"] = "PRIVATE_REFUSAL"
        invalid = [envelope(model="other/model"), envelope(choices=[]), wrong_role, legacy, refused,
                   envelope(finish="length"), envelope(content="x" * 6001)]
        for response in invalid:
            with self.assertRaises(gateway.GatewayError) as failure:
                await self.complete(response)
            self.assertNotIn("PRIVATE", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
