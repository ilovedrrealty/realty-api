"""Tracking receipts are not proof Google Ads attributed a conversion."""
from datetime import datetime, timezone


def tracking_metadata(body) -> dict | None:
    if body.tracking is None:
        return None
    return {**body.tracking.model_dump(mode="json", exclude_none=True),
            "received_at": datetime.now(timezone.utc).isoformat(), "version": 1}


def measurement_receipt(lead) -> dict:
    tracking = lead.tracking or {}
    if not tracking:
        reason = "legacy_submission"
    elif tracking.get("internal_test"):
        reason = "internal_test"
    elif lead.type not in {"buyer_interest", "seller_interest", "rental_interest"}:
        reason = "other_form"
    elif tracking.get("measurement_consent") != "granted":
        reason = "measurement_not_granted"
    else:
        reason = "eligible_inquiry"
    lead_type = lead.type if lead.type in {"buyer_interest", "seller_interest", "rental_interest"} else "other"
    source = tracking.get("form")
    intake_source = source if source in {"get_started", "contact", "spanish_landing", "lead_popup"} else "other"
    return {"eligible": reason == "eligible_inquiry", "reason": reason, "lead_type": lead_type, "intake_source": intake_source}


def private_tracking_summary(lead) -> dict | None:
    tracking = lead.tracking or {}
    if not tracking:
        return None
    attribution = tracking.get("attribution") or {}
    return {
        "form": tracking.get("form"), "page_path": tracking.get("page_path"),
        "intent": tracking.get("intent") if tracking.get("intent") in {"buy", "sell", "rent", "general"} else "unknown",
        "locale": tracking.get("locale"), "contact_consent": tracking.get("contact_consent") is True,
        "measurement_consent": tracking.get("measurement_consent", "unset"),
        "internal_test": tracking.get("internal_test") is True,
        "source": attribution.get("utm_source"), "medium": attribution.get("utm_medium"),
        "campaign": attribution.get("utm_campaign"),
        "has_ad_click_id": any(attribution.get(k) for k in ("gclid", "gbraid", "wbraid")),
        "measurement": measurement_receipt(lead),
    }
