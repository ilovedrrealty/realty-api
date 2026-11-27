"""Customer-reviewed public contact commands; no route or model tool is attached.

The future server caller owns the session binding/key and an independent customer
confirmation control. Never obtain these from model arguments, put review/contact
data in a model prompt or URL, or interpret assistant prose as confirmation. The
existing public tool allowlist remains read-only. HTTP origin/rate-limit/session
integration is still required before this adapter is exposed.

Prepare performs validation only. Confirm uses the existing lead submission UUID
unique index and atomic lead/outbox transaction in a fresh session. A signature
binds the review; it is not the replay store. No inline email/CRM fallback, worker,
provider request, automatic retry, credential lookup or activation is provided.
"""
from dataclasses import dataclass, field
import hashlib
import hmac
import importlib
import json
import re
import time
from uuid import UUID, uuid4


TTL_SECONDS = 600
_INTENTS = {"buy": "buyer_interest", "sell": "seller_interest",
            "rent": "rental_interest", "property_inquiry": "property_inquiry"}
_CONTACT = {"intent", "name", "email", "phone", "message", "property_id", "contact_consent"}
_REVIEW = _CONTACT | {"locale", "consent_version", "consent_text"}
_CONSENT = {
    "en": "I agree that I Love DR Realty may contact me about this request using the email address or phone number I provided.",
    "es": "Acepto que I Love DR Realty se comunique conmigo sobre esta solicitud mediante el correo electrónico o el teléfono que proporcioné.",
}
_TOKEN = re.compile(r"v1\.([1-9][0-9]{8,11})\.([a-f0-9-]{36})\.([a-f0-9]{64})\.([a-f0-9]{64})\Z")


class LeadCommandError(ValueError):
    """Finite source-owned code; never contains contact, token or DB details."""


@dataclass(frozen=True, slots=True)
class ServerLeadContext:
    """Trusted server inputs, not a JSON/model schema or an account authority."""
    session_binding: str = field(repr=False)
    signing_key: bytes = field(repr=False)
    enabled: bool = False


@dataclass(frozen=True, slots=True)
class PreparedLeadReview:
    review_json: str = field(repr=False)
    confirmation_token: str = field(repr=False)
    expires_at: int


@dataclass(frozen=True, slots=True)
class ConfirmedLeadReceipt:
    reference: str
    recorded: bool
    replayed: bool
    delivery_confirmed: bool = False


def _context(value):
    if (type(value) is not ServerLeadContext or type(value.enabled) is not bool
            or type(value.session_binding) is not str
            or re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value.session_binding) is None
            or type(value.signing_key) is not bytes or not 32 <= len(value.signing_key) <= 128):
        raise LeadCommandError("invalid_server_context")
    return value


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _object(raw):
    try:
        if type(raw) is not str or not 1 <= len(raw.encode("utf-8")) <= 8192 or "\0" in raw:
            raise ValueError("size")
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        if type(value) is not dict:
            raise ValueError("object")
        _json(value).encode("utf-8")
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise LeadCommandError("invalid_contact_request") from None


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _backend():
    # Lazy actual validators/service, not a second lead-writing implementation.
    leads = importlib.import_module("routes.leads")
    database = importlib.import_module("database")
    return leads.LeadCreate, leads._persist_submission, database.SessionLocal


def _body(value, locale, submission_id, schema):
    try:
        if (set(value) - _CONTACT or not {"intent", "name", "contact_consent"} <= set(value)
                or type(value["intent"]) is not str or value["intent"] not in _INTENTS
                or type(value["name"]) is not str or value["contact_consent"] is not True
                or type(locale) is not str or locale not in {"en", "es"}):
            raise ValueError("shape")
        for key in ("name", "email", "phone", "message", "property_id"):
            text = value.get(key)
            if text is not None and (type(text) is not str or any(
                    ord(c) < 32 and (key != "message" or c not in "\n\r\t") or ord(c) == 127 for c in text)):
                raise ValueError("contact")
        if value.get("property_id") is not None and value["intent"] != "property_inquiry":
            raise ValueError("unrelated_property")
        if value["intent"] == "property_inquiry" and not value.get("property_id"):
            raise ValueError("property_required")
        return schema.model_validate({
            "type": _INTENTS[value["intent"]], "name": value["name"], "email": value.get("email"),
            "phone": value.get("phone"), "message": value.get("message"), "property_id": value.get("property_id"),
            "submission_id": submission_id,
            "tracking": {"form": "contact", "page_path": "/other/", "locale": locale,
                         "intent": value["intent"] if value["intent"] != "property_inquiry" else "general",
                         "contact_consent": True, "measurement_consent": "denied", "internal_test": False},
        })
    except Exception:
        raise LeadCommandError("invalid_contact_request") from None


def _review(body, intent, locale):
    return {"intent": intent, "locale": locale, "name": body.name,
            "email": str(body.email) if body.email else None, "phone": body.phone, "message": body.message,
            "property_id": str(body.property_id) if body.property_id else None, "contact_consent": True,
            "consent_version": "contact_request_v1", "consent_text": _CONSENT[locale]}


def _signature(context, unsigned):
    material = b"public-assistant-contact-v1\0" + context.session_binding.encode("ascii") + b"\0" + unsigned.encode("ascii")
    return hmac.new(context.signing_key, material, hashlib.sha256).hexdigest()


def _now():
    return int(time.time())


def prepare_contact_review(context, draft_json, *, locale):
    """Return the normalized private review for the customer, never for the model."""
    context = _context(context)
    value = _object(draft_json)
    try:
        schema, _, _ = _backend()
    except Exception:
        raise LeadCommandError("submission_unavailable") from None
    submission_id, issued = uuid4(), _now()
    body = _body(value, locale, submission_id, schema)
    review = _json(_review(body, value["intent"], locale))
    if len(review.encode("utf-8")) > 8192:
        raise LeadCommandError("invalid_contact_request")
    digest = hashlib.sha256(review.encode("utf-8")).hexdigest()
    unsigned = f"v1.{issued}.{submission_id}.{digest}"
    return PreparedLeadReview(review, unsigned + "." + _signature(context, unsigned), issued + TTL_SECONDS)


def confirm_contact_review(context, review_json, confirmation_token, *, customer_confirmed):
    """Execute only an independent explicit customer confirmation, in a fresh DB session.

    A recorded receipt is not delivery evidence. On an uncertain commit/error this
    function never retries. The same approved UUID may be explicitly resubmitted;
    the existing persistent unique index/payload comparison decides the replay.
    """
    context = _context(context)
    if not context.enabled:
        raise LeadCommandError("contact_commands_disabled")
    if customer_confirmed is not True:
        raise LeadCommandError("customer_confirmation_required")
    value = _object(review_json)
    if set(value) != _REVIEW:
        raise LeadCommandError("invalid_contact_request")
    if type(confirmation_token) is not str or len(confirmation_token) > 256:
        raise LeadCommandError("invalid_review")
    match = _TOKEN.fullmatch(confirmation_token)
    if match is None:
        raise LeadCommandError("invalid_review")
    issued, submission, digest, signature = match.groups()
    unsigned = confirmation_token.rsplit(".", 1)[0]
    if not hmac.compare_digest(signature, _signature(context, unsigned)):
        raise LeadCommandError("invalid_review")
    age = _now() - int(issued)
    if not 0 <= age <= TTL_SECONDS:
        raise LeadCommandError("review_expired")
    canonical = _json(value)
    if not hmac.compare_digest(digest, hashlib.sha256(canonical.encode("utf-8")).hexdigest()):
        raise LeadCommandError("review_changed")
    try:
        submission_id = UUID(submission)
        if submission_id.version != 4 or str(submission_id) != submission:
            raise ValueError("submission")
        schema, persist, factory = _backend()
        body = _body({k: v for k, v in value.items() if k in _CONTACT}, value["locale"], submission_id, schema)
        if _review(body, value["intent"], value["locale"]) != value:
            raise LeadCommandError("review_changed")
        # Never commit a caller-owned session or pass an account/model actor.
        with factory() as db:
            lead, created = persist(body, db, user=None, legacy=False)
            reference = str(lead.id)
        return ConfirmedLeadReceipt(reference, True, not created)
    except LeadCommandError:
        raise
    except Exception as error:
        code = {404: "property_unavailable", 409: "submission_conflict", 422: "invalid_contact_request"}.get(
            getattr(error, "status_code", None), "submission_unavailable")
        raise LeadCommandError(code) from None
