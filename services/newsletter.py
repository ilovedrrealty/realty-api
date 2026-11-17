"""Explicit newsletter consent and transactional confirmation only."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from hmac import compare_digest, new as hmac_new
from html import escape
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from config import settings
from models.newsletter import NewsletterSubscription


CONFIRMATION_LIFETIME = timedelta(hours=20)
SEND_INTERVAL = timedelta(minutes=5)
TRANSPORT = httpx.Timeout(connect=3, read=10, write=5, pool=1)
COPY = {
    "en": ("Confirm your iLoveDR Realty newsletter signup",
           "Click below to confirm you want real estate updates by email.",
           "Confirm subscription", "Cancel or unsubscribe"),
    "es": ("Confirma tu suscripción al boletín de iLoveDR Realty",
           "Pulsa abajo para confirmar que quieres recibir novedades inmobiliarias por correo.",
           "Confirmar suscripción", "Cancelar o darme de baja"),
}


class ConfirmationUnavailable(Exception):
    """A confirmation request could not be accepted by the mail provider."""


def now_utc():
    return datetime.now(timezone.utc)


def newsletter_page():
    url = settings.landing_url.rstrip("/")
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ConfirmationUnavailable()
    if parsed.scheme != "https" and not (settings.environment == "development"
                                           and parsed.hostname in {"localhost", "127.0.0.1"}):
        raise ConfirmationUnavailable()
    if not parsed.hostname:
        raise ConfirmationUnavailable()
    return url + "/newsletter/"


def action_token(row_id, action, nonce=None):
    message = f"ildr-newsletter:v1:{action}:{row_id}:{nonce or ''}"
    signature = hmac_new(settings.jwt_secret.encode(), message.encode(), sha256).hexdigest()
    return f"{row_id}.{signature}"


def send_confirmation(email, language, row_id, nonce, page):
    confirm = escape(page + "#confirm=" + action_token(row_id, "confirm", nonce), quote=True)
    unsubscribe = escape(page + "#unsubscribe=" + action_token(row_id, "unsubscribe"), quote=True)
    subject, instructions, confirm_label, cancel_label = COPY[language]
    html = (f'<p>{instructions}</p><p><a href="{confirm}">{confirm_label}</a></p>'
            f'<p><a href="{unsubscribe}">{cancel_label}</a></p>')
    try:
        result = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.resend_api_key}",
                     "Idempotency-Key": f"ildr-newsletter-confirm-{nonce}"},
            json={"from": settings.email_from, "to": [email], "subject": subject, "html": html},
            timeout=TRANSPORT,
        )
        provider_id = result.json().get("id") if result.is_success else None
        accepted = isinstance(provider_id, str) and 0 < len(provider_id) <= 200
    except (httpx.HTTPError, ValueError, AttributeError):
        raise ConfirmationUnavailable() from None
    if not accepted:
        raise ConfirmationUnavailable()


def subscribe(db, *, email, language):
    if not settings.resend_api_key:
        raise ConfirmationUnavailable()
    page = newsletter_page()
    email = email.casefold()
    now = now_utc()
    db.execute(insert(NewsletterSubscription).values(
        id=uuid4(), email=email, language=language, status="pending", consent_at=now,
    ).on_conflict_do_nothing(index_elements=[NewsletterSubscription.email]))
    row = db.scalar(select(NewsletterSubscription).where(NewsletterSubscription.email == email).with_for_update())
    if row.status == "subscribed":
        db.commit()
        return
    if row.last_attempt_at and now - row.last_attempt_at < SEND_INTERVAL:
        accepted = row.provider_accepted_at is not None
        db.commit()
        if not accepted:
            raise ConfirmationUnavailable()
        return
    if row.status == "unsubscribed" or row.confirmation_expires_at is None or row.confirmation_expires_at <= now:
        row.status = "pending"
        row.language = language
        row.consent_at = now
        row.confirmation_nonce = uuid4()
        row.confirmation_expires_at = now + CONFIRMATION_LIFETIME
        row.provider_accepted_at = None
    if row.provider_accepted_at is not None:
        db.commit()
        return
    row.last_attempt_at = now
    selected = (row.email, row.language, row.id, row.confirmation_nonce, page)
    db.commit()
    # Stable payload and provider key allow repeat requests after an uncertain
    # result without sending two messages during the same confirmation window.
    send_confirmation(*selected)
    _, _, row_id, nonce, _ = selected
    db.execute(update(NewsletterSubscription).where(
        NewsletterSubscription.id == row_id, NewsletterSubscription.confirmation_nonce == nonce,
    ).values(provider_accepted_at=now_utc()))
    db.commit()


def finish(db, *, action, token):
    row_id_text, _, _ = token.partition(".")
    try:
        row_id = UUID(row_id_text)
    except ValueError:
        return None
    row = db.scalar(select(NewsletterSubscription).where(NewsletterSubscription.id == row_id).with_for_update())
    if row is None:
        return None
    nonce = row.confirmation_nonce if action == "confirm" else None
    if not compare_digest(token, action_token(row.id, action, nonce)):
        return None
    now = now_utc()
    if action == "confirm":
        if not nonce or not row.confirmation_expires_at or row.confirmation_expires_at <= now or row.status == "unsubscribed":
            return None
        if row.status == "pending":
            row.status = "subscribed"
            row.subscribed_at = now
    elif row.status != "unsubscribed":
        row.status = "unsubscribed"
        row.unsubscribed_at = now
        row.confirmation_nonce = None
        row.confirmation_expires_at = None
        row.provider_accepted_at = None
    status = row.status
    db.commit()
    return status
