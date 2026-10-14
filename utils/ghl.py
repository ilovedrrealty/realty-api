import httpx
from datetime import datetime, timezone, timedelta
from html import escape
from sqlalchemy.orm import Session

from config import settings

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


def _post_extras(client: httpx.Client, contact_id: str, lead, note_parts: list[str]) -> None:
    """Post note + follow-up task to an existing GHL contact. Never raises."""
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
            if not note_resp.is_success:
                print(f"[GHL] note failed {note_resp.status_code}: {note_resp.text}")
        except Exception as exc:
            print(f"[GHL] note failed for contact {contact_id}: {exc}")

    label = LEAD_TYPE_LABELS.get(lead.type, lead.type.replace("_", " ").title())
    task_lines = ["New lead from I Love DR Realty Website."]
    if lead.message:
        task_lines.append(f"<br><b>Message:</b> {lead.message}")
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
        if not task_resp.is_success:
            print(f"[GHL] task failed {task_resp.status_code}: {task_resp.text}")
    except Exception as exc:
        print(f"[GHL] task failed for contact {contact_id}: {exc}")


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
                print(f"[GHL] status tag update failed {r.status_code}: {r.text}")
    except Exception as exc:
        print(f"[GHL] status tag update failed for contact {ghl_contact_id}: {exc}")


def create_contact(lead, property_info: dict | None, db: Session) -> None:
    """
    Push a newly created lead to GHL as a contact.
    Fire-and-forget: logs errors but never raises.
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
        "email": lead.email,
        "source": "I Love DR Realty Website",
        "tags": tags,
    }
    # GHL requires E.164 phone format — skip if not clearly valid to avoid a 400
    if lead.phone and lead.phone.strip().startswith("+"):
        payload["phone"] = lead.phone.strip()

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
                existing_id = (body.get("meta") or {}).get("contactId")
                if existing_id:
                    if lead.type == "rental_interest":
                        # An existing buyer can also inquire about renting.
                        tag_response = client.post(
                            f"{GHL_API_BASE}/contacts/{existing_id}/tags",
                            headers=_headers(),
                            json={"tags": tags},
                        )
                        tag_response.raise_for_status()
                    print(f"[GHL] duplicate contact, reusing existing id {existing_id}")
                    lead.ghl_contact_id = existing_id
                    lead.ghl_synced_at = datetime.now(timezone.utc)
                    lead.ghl_sync_error = None
                    db.commit()
                    _post_extras(client, existing_id, lead, note_parts)
                    return
                print(f"[GHL] contact creation failed 400: {resp.text}")
                resp.raise_for_status()
            elif not resp.is_success:
                print(f"[GHL] contact creation failed {resp.status_code}: {resp.text}")
                resp.raise_for_status()

            data = resp.json()
            contact_id = (data.get("contact") or {}).get("id")
            if contact_id:
                lead.ghl_contact_id = contact_id
                lead.ghl_synced_at = datetime.now(timezone.utc)
                lead.ghl_sync_error = None
                db.commit()
                _post_extras(client, contact_id, lead, note_parts)
    except Exception as exc:
        try:
            lead.ghl_sync_error = f"{type(exc).__name__}: {exc}"[:500]
            db.commit()
        except Exception:
            pass
        print(f"[GHL] sync failed for lead {lead.id}: {exc}")
