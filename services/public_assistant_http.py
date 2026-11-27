"""Server-only public session/security boundary; no automatic command or retry.

An HTTP disconnect never releases a running worker's slot early. Sessions bind
signed reviews, not account authority or proof of a human gesture. No contact
review/status is persisted here; unresolved requests require exact-review retry
within its unchanged TTL or truthful manual recovery, never automatic reprepare.
"""
from dataclasses import asdict, dataclass, field
import hashlib
import hmac
import json
import math
import re
import secrets
from threading import BoundedSemaphore
import time
from urllib.parse import urlsplit

from config import settings

COOKIE = "ildr_public_assistant"
COOKIE_PATH = "/public/assistant"
SESSION_SECONDS = 3600
_SESSION = re.compile(r"v1\.([1-9][0-9]{8,11})\.([A-Za-z0-9_-]{43})\.([a-f0-9]{64})\Z")
_MODEL_SLOTS = BoundedSemaphore(2)
_CONTACT_SLOTS = BoundedSemaphore(2)
_ERRORS = {
    "assistant_unavailable", "assistant_request_refused", "assistant_session_required",
    "invalid_assistant_request", "assistant_busy", "assistant_disabled", "invalid_contact_request",
    "invalid_review", "review_changed", "review_expired", "customer_confirmation_required",
    "property_unavailable", "submission_conflict", "submission_unknown",
}


class PublicHTTPError(ValueError):
    def __init__(self, status, code):
        valid = type(status) is int and status in (403, 404, 408, 409, 413, 415, 422, 429, 503) and type(code) is str and code in _ERRORS
        self.status, self.code = (status, code) if valid else (503, "assistant_unavailable")
        super().__init__(self.code)


@dataclass(frozen=True)
class PublicSession:
    token: str = field(repr=False)
    binding: str = field(repr=False)
    expires_at: int


def _now():
    return int(time.time())


def _key(purpose):
    secret = settings.jwt_secret
    if type(secret) is not str or not 32 <= len(secret.encode("utf-8")) <= 4096:
        raise PublicHTTPError(503, "assistant_unavailable")
    return hmac.new(secret.encode("utf-8"), b"ildr-public-assistant-http-v1\0" + purpose, hashlib.sha256).digest()


def require_origin(values):
    if len(values) != 1 or type(values[0]) is not str or len(values[0]) > 256:
        raise PublicHTTPError(403, "assistant_request_refused")
    origin = values[0]
    allowed = settings.origins_list
    try:
        parts = urlsplit(origin)
        valid = (origin in allowed and origin != "null" and "*" not in allowed
                 and parts.scheme in ("http", "https") and parts.hostname and not parts.username
                 and not parts.password and not parts.path and not parts.query and not parts.fragment
                 and (settings.environment != "production" or parts.scheme == "https"))
        _ = parts.port
    except (ValueError, UnicodeError):
        valid = False
    if not valid:
        raise PublicHTTPError(403, "assistant_request_refused")


def parse_session(token):
    if type(token) is not str or len(token) > 192:
        raise PublicHTTPError(403, "assistant_session_required")
    match = _SESSION.fullmatch(token)
    if match is None:
        raise PublicHTTPError(403, "assistant_session_required")
    issued, nonce, signature = match.groups()
    unsigned = token.rsplit(".", 1)[0]
    expected = hmac.new(_key(b"session"), unsigned.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature) or not 0 <= _now() - int(issued) <= SESSION_SECONDS:
        raise PublicHTTPError(403, "assistant_session_required")
    return PublicSession(token, nonce, int(issued) + SESSION_SECONDS)


def issue_session(existing):
    if existing:
        try:
            return parse_session(existing)
        except PublicHTTPError as error:
            if error.status != 403:
                raise
    issued, nonce = _now(), secrets.token_urlsafe(32)
    unsigned = f"v1.{issued}.{nonce}"
    signature = hmac.new(_key(b"session"), unsigned.encode("ascii"), hashlib.sha256).hexdigest()
    return PublicSession(unsigned + "." + signature, nonce, issued + SESSION_SECONDS)


def csrf(session):
    return hmac.new(_key(b"csrf"), session.token.encode("ascii"), hashlib.sha256).hexdigest()


def require_csrf(session, values):
    if len(values) != 1 or type(values[0]) is not str or not re.fullmatch(r"[a-f0-9]{64}", values[0]) or not hmac.compare_digest(csrf(session), values[0]):
        raise PublicHTTPError(403, "assistant_request_refused")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def parse_object(raw, maximum):
    try:
        if type(raw) is not bytes or not 1 <= len(raw) <= maximum:
            raise ValueError("size")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        if type(value) is not dict:
            raise ValueError("object")
        pending, count = [(value, 0)], 0
        while pending:
            item, depth = pending.pop()
            count += 1
            if count > 512 or depth > 8:
                raise ValueError("bounds")
            if type(item) is str:
                item.encode("utf-8")
                if "\0" in item:
                    raise ValueError("control")
            elif type(item) is dict:
                pending.extend((part, depth + 1) for pair in item.items() for part in pair)
            elif type(item) is list:
                pending.extend((part, depth + 1) for part in item)
            elif type(item) is float and not math.isfinite(item):
                raise ValueError("nonfinite")
            elif type(item) not in (int, float, bool, type(None)):
                raise ValueError("type")
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise PublicHTTPError(422, "invalid_assistant_request") from None


def model_enabled():
    return bool(settings.public_assistant_enabled and settings.public_assistant_no_new_spend_verified
                and settings.public_assistant_model and settings.public_assistant_gateway_key
                and settings.public_assistant_model in {x.strip() for x in settings.public_assistant_verified_models.split(",") if x.strip()})


def contact_enabled():
    return settings.public_assistant_contacts_enabled is True


def call_in_worker(kind, operation, *args):
    slots = _MODEL_SLOTS if kind == "model" else _CONTACT_SLOTS
    if not slots.acquire(blocking=False):
        raise PublicHTTPError(429, "assistant_busy")
    try:
        return operation(*args)
    finally:
        # Executed in the actual worker, including after a canceled HTTP await.
        slots.release()


def public_turn(value):
    if not model_enabled():
        raise PublicHTTPError(503, "assistant_disabled")
    if set(value) != {"locale", "message"}:
        raise PublicHTTPError(422, "invalid_assistant_request")
    from services.assistant_gateway_transport import Binding
    from services.public_assistant_orchestrator import PublicAssistantUnavailable, run_public_turn
    binding = Binding(settings.public_assistant_model, settings.public_assistant_gateway_key,
                      True, True, frozenset(x.strip() for x in settings.public_assistant_verified_models.split(",") if x.strip()))
    try:
        result = run_public_turn(json.dumps(value, ensure_ascii=False, allow_nan=False), binding=binding)
        return {"locale": result.locale, "answer": result.answer,
                "citations": [asdict(item) for item in result.citations], "model_invoked": result.model_invoked,
                "grounding_scope": result.grounding_scope, "availability_verified": result.availability_verified}
    except PublicAssistantUnavailable as error:
        if str(error) == "invalid_public_input":
            raise PublicHTTPError(422, "invalid_assistant_request") from None
        raise PublicHTTPError(503, "assistant_unavailable") from None


def _command_context(session):
    if not contact_enabled():
        raise PublicHTTPError(503, "assistant_disabled")
    from services.public_assistant_lead_commands import ServerLeadContext
    return ServerLeadContext(session.binding, _key(b"contact-review"), True)


def _command_error(error, *, confirming=False):
    code = str(error)
    status = {"invalid_contact_request": 422, "invalid_review": 409, "review_changed": 409,
              "review_expired": 409, "customer_confirmation_required": 422,
              "property_unavailable": 404, "submission_conflict": 409}.get(code)
    if status is not None:
        return PublicHTTPError(status, code)
    # A persistence exception may follow a committed write. Never claim failure.
    return PublicHTTPError(503, "submission_unknown" if confirming else "assistant_unavailable")


def contact_review(value, session):
    context = _command_context(session)
    if set(value) != {"locale", "draft"} or type(value["draft"]) is not dict:
        raise PublicHTTPError(422, "invalid_assistant_request")
    from services.public_assistant_lead_commands import LeadCommandError, prepare_contact_review
    try:
        prepared = prepare_contact_review(context, json.dumps(value["draft"], ensure_ascii=False, allow_nan=False), locale=value["locale"])
        return {"review": json.loads(prepared.review_json), "confirmation_token": prepared.confirmation_token,
                "expires_at": prepared.expires_at}
    except LeadCommandError as error:
        raise _command_error(error) from None


def contact_confirm(value, session):
    context = _command_context(session)
    if (set(value) != {"review", "confirmation_token", "customer_confirmed"}
            or type(value["review"]) is not dict or value["customer_confirmed"] is not True):
        raise PublicHTTPError(422, "invalid_assistant_request")
    from services.public_assistant_lead_commands import LeadCommandError, confirm_contact_review
    try:
        receipt = confirm_contact_review(context, json.dumps(value["review"], ensure_ascii=False, allow_nan=False),
                                         value["confirmation_token"], customer_confirmed=True)
        return asdict(receipt)
    except LeadCommandError as error:
        raise _command_error(error, confirming=True) from None
