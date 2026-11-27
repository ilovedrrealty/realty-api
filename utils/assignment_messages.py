"""Pure copies of existing assignment notice rendering; never send or read recipients."""
from html import escape
from config import settings
from utils.email import _email_wrap, _lead_email_row, _is_google_scheduling_url


def build_upgrade_approved_notification(display_name: str, new_role: str) -> tuple[str, str]:
    role_label = escape(new_role.replace("_", " ").title())
    body = f"""
      <h2 style="margin:0 0 10px;color:#00102e;font-size:21px;font-weight:700">Account upgraded to {role_label}</h2>
      <p style="margin:0 0 20px;color:#555;font-size:14.5px;line-height:1.6">
        Hi {escape(display_name)}, your request to become a <strong>{role_label}</strong> on I Love DR Realty has been approved.
        Log in to your dashboard to access your new features.
      </p>
      <table cellpadding="0" cellspacing="0" style="margin-bottom:28px">
        <tr><td>
          <a href="{settings.landing_url}/login" style="display:inline-block;background:#00102e;color:#ffffff;text-decoration:none;font-weight:700;font-size:14.5px;padding:13px 30px;border-radius:8px">
            Go to Dashboard &rarr;
          </a>
        </td></tr>
      </table>
    """
    return f"Your account has been upgraded to {role_label}", _email_wrap("Account upgrade approved", body)


def build_realtor_assigned_owner_notification(realtor_name: str, owner_name: str, owner_email: str) -> tuple[str, str]:
    body = f"""
      <h2 style="margin:0 0 10px;color:#00102e;font-size:21px;font-weight:700">You've been assigned a new property owner</h2>
      <p style="margin:0 0 20px;color:#555;font-size:14.5px;line-height:1.6">
        Hi {escape(realtor_name)}, a property owner has been assigned to you on I Love DR Realty.
        Log in to your dashboard to view their details and begin managing their listing.
      </p>
      <div style="background:#f5f9ff;border:1px solid #d0e4f7;border-radius:10px;padding:16px 20px;margin-bottom:24px">
        <p style="margin:0 0 4px;color:#0b63ab;font-size:13px;font-weight:700">{escape(owner_name)}</p>
        <p style="margin:0;color:#555;font-size:13px">{escape(owner_email)}</p>
      </div>
      <table cellpadding="0" cellspacing="0" style="margin-bottom:28px">
        <tr><td>
          <a href="{settings.landing_url}/dashboard" style="display:inline-block;background:#00102e;color:#ffffff;text-decoration:none;font-weight:700;font-size:14.5px;padding:13px 30px;border-radius:8px">
            Go to Dashboard &rarr;
          </a>
        </td></tr>
      </table>
    """
    return f"New owner assigned to you — {owner_name}", _email_wrap("Owner assignment", body)


def build_realtor_lead_assigned_notification(
    realtor_name: str,
    lead,
    property_info: dict | None = None,
    calendly_url: str | None = None,
) -> tuple[str, str]:
    type_labels = {
        "property_inquiry": "Property Inquiry",
        "buyer_interest":   "Buyer Interest",
        "seller_interest":  "Seller Interest",
        "rental_interest":  "Rental Interest",
        "booking":          "Booking Request",
    }
    type_label = type_labels.get(lead.type, escape(lead.type))
    phone_row = f"<tr><td style='padding:4px 0;color:#888;font-size:13px;width:90px'>Phone</td><td style='padding:4px 0;font-size:13px;color:#00102e'>{escape(lead.phone)}</td></tr>" if lead.phone else ""
    message_block = f"""
      <div style="background:#f5f3ef;border-radius:8px;padding:14px 16px;margin:16px 0">
        <p style="margin:0;color:#555;font-size:13.5px;line-height:1.6;white-space:pre-wrap">{escape(lead.message)}</p>
      </div>""" if lead.message else ""
    property_block = ""
    if property_info:
        prop_title = escape(property_info.get("title", ""))
        prop_url = f"{settings.landing_url}/listing/{property_info.get('id', '')}"
        property_block = f"""
      <p style="margin:16px 0 6px;font-size:12px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:.06em">Property</p>
      <a href="{prop_url}" style="color:#0b63ab;font-size:13.5px;font-weight:600;text-decoration:none">{prop_title} &rarr;</a>"""
    dashboard_url = f"{settings.landing_url}/dashboard"
    body = f"""
      <h2 style="margin:0 0 4px;color:#00102e;font-size:20px;font-weight:700">You have a new lead</h2>
      <p style="margin:0 0 20px;color:#888;font-size:13px">Hi {escape(realtor_name)}, a lead has been assigned to you.</p>
      <p style="margin:0 0 6px;font-size:12px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:.06em">{type_label}</p>
      <table style="border-collapse:collapse;width:100%;margin-bottom:4px">
        <tr><td style="padding:4px 0;color:#888;font-size:13px;width:90px">Name</td><td style="padding:4px 0;font-size:13.5px;font-weight:600;color:#00102e">{escape(lead.name)}</td></tr>
        {_lead_email_row(lead)}
        {phone_row}
      </table>
      {message_block}
      {property_block}
      <table cellpadding="0" cellspacing="0" style="margin-top:24px;margin-bottom:8px">
        <tr><td>
          <a href="{dashboard_url}" style="display:inline-block;background:#00102e;color:#ffffff;text-decoration:none;font-weight:700;font-size:13.5px;padding:11px 26px;border-radius:8px">
            View Lead &rarr;
          </a>
        </td></tr>
      </table>
    """
    scheduling_label = "Google Calendar" if _is_google_scheduling_url(calendly_url) else "Calendly"
    calendly_block = f"""
<table cellpadding="0" cellspacing="0" style="margin-bottom:16px">
  <tr><td>
    <a href="{escape(calendly_url or '')}" style="display:inline-block;background:#006BFF;color:#ffffff;text-decoration:none;font-weight:700;font-size:14px;padding:11px 26px;border-radius:8px">
      Schedule a Meeting &#8594;
    </a>
  </td></tr>
</table>
<p style="margin:0 0 20px;color:#888;font-size:12.5px;line-height:1.6">
  Use your {scheduling_label} link above to schedule a meeting with this lead.
</p>
""" if calendly_url else ""
    body = body + calendly_block
    return f"New lead assigned to you — {lead.name}", _email_wrap("Lead assigned", body)
