import httpx
import re
from datetime import datetime, timezone, timedelta
from html import escape
from sqlalchemy.orm import Session

from config import settings
from utils.phone import normalize_phone

GHL_API_BASE = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-07-28"

LEAD_TYPE_TAGS = {
    "buyer_interest":   ["buyer-interest", "website-lead"],
    "seller_interest":  ["seller-interest", "website-lead"],
    "rental_interest":  ["rental-interest", "website-lead"],
    "property_inquiry": ["property-inquiry", "website-lead"],
    "booking":          ["booking-request", "website-lead"],
}

LEAD_TYPE_LABELS = {
    "buyer_interest":   "Buyer Interest",
    "seller_interest":  "Seller Interest",
    "rental_interest":  "Rental Interest",
    "property_inquiry": "Property Inquiry",
    "booking":          "Booking",
}


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.ghl_api_key}",
        "Version": GHL_API_VERSION,
        "Content-Type": "application/json",
    }


def _split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split(" ", 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def _build_property_lines(property_info: dict, landing_url: str) -> list[str]:
    lines = []
    url = f"{landing_url.rstrip('/')}/listing/{property_info['id']}"
    title = escape(property_info["title"])
    lines.append(f'Property: <a href="{url}" target="_blank">{title}</a>')
    if property_info.get("listing_type"):
        lines.append(f"Type: {property_info['listing_type'].title()}")
    if property_info.get("location"):
        lines.append(f"Location: {property_info['location']}")
    if property_info.get("price"):
        lines.append(f"Price: ${property_info['price']:,.0f}")
    beds = property_info.get("bedrooms")
    baths = property_info.get("bathrooms")
    if beds or baths:
        lines.append(f"Beds/Baths: {beds or '—'} bd / {baths or '—'} ba")
    return lines


def _safe_failure(stage: str, exc: Exception) -> str:
    """Keep provider payloads, request headers and customer details out of diagnostics."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"GHL {stage} HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.RequestError):
        return f"GHL {stage} transport error"
    if isinstance(exc, ValueError):
        return f"GHL {stage} invalid response"
    return f"GHL {stage} integration error"


def _contact_id(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value) else None


def _post_extras(client: httpx.Client, contact_id: str, lead, note_parts: list[str]) -> list[str]:
    """Attempt both follow-ups once and report safe failures; never retry either."""
    failures = []
    if note_parts:
        message_lines = [l for l in note_parts if not l.startswith(("Property:", "Type:", "Location:", "Price:", "Beds"))]
        property_lines = [l for l in note_parts if l.startswith(("Property:", "Type:", "Location:", "Price:", "Beds"))]
        note_body_parts = []
        if message_lines:
            note_body_parts.extend(message_lines)
        if property_lines:
            if note_body_parts:
                note_body_parts.append("<br>")
            note_body_parts.extend(property_lines)
        try:
            note_resp = client.post(
                f"{GHL_API_BASE}/contacts/{contact_id}/notes",
                headers=_headers(),
                json={"body": "<br>".join(note_body_parts)},
            )
            note_resp.raise_for_status()
        except Exception as exc:
            failures.append(_safe_failure("note", exc))

    label = LEAD_TYPE_LABELS.get(lead.type, lead.type.replace("_", " ").title())
    task_lines = ["New lead from I Love DR Realty Website."]
    if lead.message:
        task_lines.append(f"<br><b>Message:</b> {escape(lead.message)}")
    property_lines = [l for l in note_parts if l.startswith(("Property:", "Type:", "Location:", "Price:", "Beds"))]
    if property_lines:
        task_lines.append("<br>")
        task_lines.extend(property_lines)
    due = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        task_resp = client.post(
            f"{GHL_API_BASE}/contacts/{contact_id}/tasks",
            headers=_headers(),
            json={
                "title": f"Follow up: {escape(lead.name)} ({label})",
                "body": "<br>".join(task_lines),
                "dueDate": due,
                "completed": False,
            },
        )
        task_resp.raise_for_status()
    except Exception as exc:
        failures.append(_safe_failure("task", exc))
    return failures


STATUS_TAGS: dict[str, str] = {
    "new":       "lead-new",
    "assigned":  "lead-assigned",
    "contacted": "lead-contacted",
    "closed":    "lead-closed",
}

# Reverse map used by the webhook handler to translate incoming tags → our status values
TAG_TO_STATUS: dict[str, str] = {v: k for k, v in STATUS_TAGS.items()}


def update_contact_status(ghl_contact_id: str, status: str) -> None:
    """Add the lead-status tag to a GHL contact when our status changes. Fire-and-forget."""
    if not settings.ghl_enabled or not settings.ghl_api_key or not ghl_contact_id:
        return
    tag = STATUS_TAGS.get(status)
    if not tag:
        return
    try:
        with httpx.Client(timeout=10) as client:
            r = client.post(
                f"{GHL_API_BASE}/contacts/{ghl_contact_id}/tags",
                headers=_headers(),
                json={"tags": [tag]},
            )
            if not r.is_success:
                print(f"[GHL] status tag update failed HTTP {r.status_code}")
    except Exception as exc:
        print(f"[GHL] status tag update failed: {_safe_failure('status tag', exc)}")


def create_contact(lead, property_info: dict | None, db: Session) -> None:
    """
    Push a newly created lead to GHL as a contact.
    Record contact linkage separately from complete contact/note/task success.
    Each provider operation is attempted once; this function adds no retries.
    """
    if not settings.ghl_enabled or not settings.ghl_api_key:
        return

    first, last = _split_name(lead.name)
    tags = LEAD_TYPE_TAGS.get(lead.type, ["website-lead"])

    note_parts = []
    if lead.message:
        note_parts.append(escape(lead.message))
    if property_info:
        note_parts.extend(_build_property_lines(property_info, settings.landing_url))

    payload: dict = {
        "locationId": settings.ghl_location_id,
        "firstName": first,
        "lastName": last,
        "source": "I Love DR Realty Website",
        "tags": tags,
    }
    if lead.email:
        payload["email"] = lead.email
    # Older booking records can have free-form numbers; do not send invalid values.
    stage = "contact"
    try:
        phone = normalize_phone(lead.phone)
    except ValueError:
        phone = None
    if phone:
        payload["phone"] = phone

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(
                f"{GHL_API_BASE}/contacts/",
                headers=_headers(),
                json=payload,
            )
            if resp.status_code == 400:
                body = resp.json()
                # Duplicate contact — GHL returns the existing contact ID, reuse it
                existing_id = _contact_id((body.get("meta") or {}).get("contactId"))
                if existing_id:
                    # Preserve a confirmed link even if tagging or follow-up fails.
                    lead.ghl_contact_id = existing_id
                    lead.ghl_synced_at = None
                    db.commit()
                    if lead.type in ("buyer_interest", "seller_interest", "rental_interest"):
                        # Add the new interest without replacing the contact's other tags.
                        stage = "contact tags"
                        tag_response = client.post(
                            f"{GHL_API_BASE}/contacts/{existing_id}/tags",
                            headers=_headers(),
                            json={"tags": tags},
                        )
                        tag_response.raise_for_status()
                    failures = _post_extras(client, existing_id, lead, note_parts)
                    lead.ghl_synced_at = None if failures else datetime.now(timezone.utc)
                    lead.ghl_sync_error = "; ".join(failures) or None
                    db.commit()
                    return
                resp.raise_for_status()
            elif not resp.is_success:
                resp.raise_for_status()

            data = resp.json()
            contact = data.get("contact") if isinstance(data, dict) else None
            contact_id = _contact_id(contact.get("id") if isinstance(contact, dict) else None)
            if not contact_id:
                raise ValueError("Missing valid contact identifier")
            lead.ghl_contact_id = contact_id
            lead.ghl_synced_at = None
            db.commit()
            failures = _post_extras(client, contact_id, lead, note_parts)
            lead.ghl_synced_at = None if failures else datetime.now(timezone.utc)
            lead.ghl_sync_error = "; ".join(failures) or None
            db.commit()
    except Exception as exc:
        diagnostic = _safe_failure(stage, exc)
        try:
            lead.ghl_synced_at = None
            lead.ghl_sync_error = diagnostic
            db.commit()
        except Exception:
            pass
        print(f"[GHL] sync failed: {diagnostic}")
