"""One bounded Gateway turn, not an activated assistant or an authorization layer.

Only a trusted server orchestrator may build Binding/messages/tools. It must
verify current model access, privacy and zero-new-spend entitlement first and
bind an actor's permitted tools itself. No configuration/credentials are read
here. All defaults refuse activation. Provider text/arguments remain untrusted;
the orchestrator must validate arguments with the existing tool boundary before
executing a tool. No tool is executed by this transport.
"""
import asyncio
from dataclasses import dataclass, field
import json
import math
import re

import httpx

ENDPOINT = "https://ai-gateway.vercel.sh/v1/chat/completions"
DEADLINE_SECONDS = 15
MAX_REQUEST_BYTES = 48 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
_TOOLS = frozenset({"feature_help", "search_public_listings", "read_public_listing"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
_MODEL = re.compile(r"[a-z0-9_.-]+/[a-zA-Z0-9_.:-]{1,100}\Z")


class GatewayError(ValueError):
    """Finite error only; never attach response, request, headers or credentials."""


@dataclass(frozen=True)
class Binding:
    model: str = ""
    credential: str = field(default="", repr=False)
    enabled: bool = False
    no_new_spend_verified: bool = False
    verified_models: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters_json: str = field(repr=False)


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments_json: str = field(repr=False)


@dataclass(frozen=True)
class Step:
    kind: str
    text: str | None = field(default=None, repr=False)
    call: ToolCall | None = None


def _need(value, code="invalid_gateway_protocol"):
    if not value:
        raise GatewayError(code)


def _pairs(items):
    result = {}
    for key, value in items:
        _need(key not in result)
        result[key] = value
    return result


def _json(raw, limit):
    _need(type(raw) in (str, bytes))
    encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
    _need(len(encoded) <= limit)
    value = json.loads(encoded.decode("utf-8"), object_pairs_hook=_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(GatewayError("invalid_gateway_protocol")))
    nodes = 0
    for_depth = [(value, 0)]
    while for_depth:
        item, depth = for_depth.pop()
        nodes += 1
        _need(depth <= 16 and nodes <= 4000)
        if isinstance(item, dict):
            for_depth.extend((child, depth + 1) for child in item.values())
            # A model may not turn a schema reference into a network lookup.
            _need("$ref" not in item or type(item["$ref"]) is str and item["$ref"].startswith("#/"))
        elif isinstance(item, list):
            for_depth.extend((child, depth + 1) for child in item)
        elif type(item) is float:
            _need(math.isfinite(item))
    return value


def _encoded(value, limit):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    _need(len(raw) <= limit)
    return raw


def _text(value, limit=16000):
    _need(type(value) is str and 0 < len(value.encode("utf-8")) <= limit)
    return value


def _tool_specs(tools):
    _need(type(tools) is tuple and 1 <= len(tools) <= 3)
    specs = {}
    for tool in tools:
        _need(type(tool) is Tool and tool.name in _TOOLS and tool.name not in specs)
        schema = _json(tool.parameters_json, 8192)
        _need(type(schema) is dict and schema.get("type") == "object"
              and type(schema.get("properties")) is dict and len(schema["properties"]) <= 32
              and schema.get("additionalProperties") is False)
        _need(type(schema.get("required", [])) is list
              and all(type(key) is str for key in schema.get("required", [])))
        required = schema.get("required", [])
        _need(len(set(required)) == len(required) and set(required) <= set(schema["properties"]))
        specs[tool.name] = {"type": "function", "function": {"name": tool.name,
            "description": _text(tool.description, 1000), "parameters": schema}}
    return specs


def _tool_call(value, specs, seen):
    _need(type(value) is dict and set(value) == {"id", "type", "function"} and value["type"] == "function")
    call_id = value["id"]
    _need(type(call_id) is str and _IDENTIFIER.fullmatch(call_id) and call_id not in seen)
    function = value["function"]
    _need(type(function) is dict and set(function) == {"name", "arguments"}
          and type(function["name"]) is str and function["name"] in specs
          and type(function["arguments"]) is str)
    arguments = _json(function["arguments"], 8192)
    schema = specs[function["name"]]["function"]["parameters"]
    _need(type(arguments) is dict and set(arguments) <= set(schema["properties"])
          and set(schema.get("required", [])) <= set(arguments))
    # Preserve the original wire JSON for the tool's own strict input boundary.
    return ToolCall(call_id, function["name"], function["arguments"])


def _messages(messages, specs):
    _need(type(messages) is list and 2 <= len(messages) <= 16)
    output, seen, pending = [], set(), None
    for index, message in enumerate(messages):
        _need(type(message) is dict and set(message) <= {"role", "content", "tool_calls", "tool_call_id"})
        role = message.get("role")
        _need(type(role) is str and role in {"system", "user", "assistant", "tool"})
        _need((role == "system") == (index == 0))
        if role == "tool":
            _need(set(message) == {"role", "content", "tool_call_id"} and pending is not None
                  and message["tool_call_id"] == pending)
            _text(message["content"]);pending = None
        else:
            _need(pending is None and "tool_call_id" not in message)
            calls = message.get("tool_calls")
            if calls is not None:
                _need(role == "assistant" and type(calls) is list and len(calls) == 1
                      and message.get("content") in (None, ""))
                call = _tool_call(calls[0], specs, seen)
                seen.add(call.call_id);pending = call.call_id
            else:
                _need(set(message) == {"role", "content"})
                _text(message["content"])
        output.append(message)
    _need(pending is None and output[-1]["role"] in {"user", "tool"})
    # Copy through bounded strict JSON; no caller mutation can change the wire body.
    return _json(_encoded(output, MAX_REQUEST_BYTES), MAX_REQUEST_BYTES), seen


def _response(raw, binding, specs, seen):
    value = _json(raw, MAX_RESPONSE_BYTES)
    _need(type(value) is dict and value.get("object") == "chat.completion"
          and value.get("model") == binding.model)
    choices = value.get("choices")
    _need(type(choices) is list and len(choices) == 1 and type(choices[0]) is dict)
    choice = choices[0]
    _need(type(choice.get("index")) is int and choice["index"] == 0)
    message = choice.get("message")
    _need(type(message) is dict and message.get("role") == "assistant" and "function_call" not in message)
    _need(message.get("refusal") in (None, ""), "gateway_model_refused")
    calls = message.get("tool_calls")
    if calls is not None:
        _need(choice.get("finish_reason") == "tool_calls" and type(calls) is list and len(calls) == 1
              and message.get("content") in (None, ""))
        return Step("tool", call=_tool_call(calls[0], specs, seen))
    _need(choice.get("finish_reason") == "stop", "gateway_incomplete")
    return Step("text", text=_text(message.get("content"), 6000))


async def complete(binding, messages, tools, *, prior_call_ids=frozenset(), transport=None):
    """One request, no retry, no tool execution and no grants from model JSON.

    Binding/messages/tools are trusted server inputs, never a public request DTO.
    ``transport`` accepts only MockTransport for future owned tests. A future
    orchestrator owns verified actors, current tool validation and conversation
    idempotency/cancellation. Cost/entitlement checks are not performed here.
    """
    _need(type(binding) is Binding and binding.enabled is True
          and binding.no_new_spend_verified is True, "gateway_disabled")
    try:
        _need(type(binding.verified_models) is frozenset and binding.model in binding.verified_models
              and type(binding.model) is str and len(binding.model) <= 120 and _MODEL.fullmatch(binding.model), "gateway_disabled")
        _need(type(binding.credential) is str and 8 <= len(binding.credential) <= 8192
              and all(33 <= ord(c) <= 126 for c in binding.credential), "gateway_disabled")
        _need(type(prior_call_ids) is frozenset and len(prior_call_ids) <= 32
              and all(type(i) is str and _IDENTIFIER.fullmatch(i) for i in prior_call_ids))
        _need(transport is None or isinstance(transport, httpx.MockTransport))
        specs = _tool_specs(tools)
        history, history_ids = _messages(messages, specs)
        body = _encoded({"model": binding.model, "messages": history, "tools": list(specs.values()),
                         "tool_choice": "auto", "parallel_tool_calls": False, "stream": False,
                         "n": 1, "max_tokens": 768}, MAX_REQUEST_BYTES)
    except GatewayError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise GatewayError("invalid_gateway_input") from None
    try:
        async with asyncio.timeout(DEADLINE_SECONDS):
            limits = httpx.Limits(max_connections=1, max_keepalive_connections=0)
            wire = transport if transport is not None else httpx.AsyncHTTPTransport(
                retries=0, trust_env=False, limits=limits)
            async with httpx.AsyncClient(transport=wire,
                    trust_env=False, follow_redirects=False, timeout=httpx.Timeout(5, connect=5, pool=1),
                    limits=limits) as client:
                async with client.stream("POST", ENDPOINT, content=body, headers={
                        "Authorization": "Bearer " + binding.credential, "Content-Type": "application/json",
                        "Accept": "application/json", "Accept-Encoding": "identity"}) as response:
                    code = response.status_code
                    if code != 200:
                        raise GatewayError("gateway_auth_unavailable" if code in (401,403) else
                                           "gateway_funds_unavailable" if code == 402 else
                                           "gateway_rate_limited" if code == 429 else "gateway_unavailable")
                    _need(response.headers.get("content-type", "").split(";",1)[0].strip().lower() == "application/json"
                          and response.headers.get("content-encoding", "identity").lower() == "identity")
                    length = response.headers.get("content-length")
                    _need(length is None or length.isascii() and length.isdigit() and int(length) <= MAX_RESPONSE_BYTES)
                    collected = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        _need(len(collected) + len(chunk) <= MAX_RESPONSE_BYTES)
                        collected.extend(chunk)
                    return _response(bytes(collected), binding, specs, history_ids | prior_call_ids)
    except GatewayError:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise GatewayError("gateway_deadline") from None
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise GatewayError("invalid_gateway_protocol") from None
    except Exception:
        raise GatewayError("gateway_unavailable") from None
